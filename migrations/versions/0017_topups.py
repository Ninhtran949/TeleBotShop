"""Verified top-up evidence, exclusive deposit routes and idempotent review."""

from alembic import op

revision = "0017_topups"
down_revision = "0016_credit_checkout"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE deposit_routes (
id UUID NOT NULL,
buyer_id UUID NOT NULL,
network VARCHAR(200) NOT NULL,
token_contract VARCHAR(200) NOT NULL,
recipient VARCHAR(200) NOT NULL,
minimum_confirmations INTEGER DEFAULT '3' NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_deposit_route_buyer UNIQUE (buyer_id),
CONSTRAINT uq_deposit_route_address UNIQUE (network, token_contract, recipient),
CONSTRAINT ck_deposit_route_finality CHECK (minimum_confirmations BETWEEN 1 AND 1000000),
CONSTRAINT fk_deposit_route_buyer FOREIGN KEY(buyer_id) REFERENCES users (id)
)
    """)
    op.execute("""
CREATE TABLE settlement_events (
id UUID NOT NULL,
provider VARCHAR(16) NOT NULL,
transaction_id VARCHAR(300) NOT NULL,
facts JSONB NOT NULL,
digest BYTEA NOT NULL,
confirmed BOOLEAN NOT NULL,
confirmations INTEGER NOT NULL,
required_confirmations INTEGER NOT NULL,
token_verified BOOLEAN NOT NULL,
source VARCHAR(32) NOT NULL,
proof_digest BYTEA NOT NULL,
conflict BOOLEAN DEFAULT false NOT NULL,
state VARCHAR(32) DEFAULT 'DETECTED' NOT NULL,
reason VARCHAR(64),
intent_id UUID,
PRIMARY KEY (id),
CONSTRAINT uq_settlement_identity UNIQUE (provider, transaction_id),
CONSTRAINT ck_settlement_proof CHECK (provider IN ('SEPAY','BINANCE') AND
octet_length(digest)=32 AND octet_length(proof_digest)=32),
CONSTRAINT ck_settlement_state CHECK (state IN
('DETECTED','PENDING_CONFIRMATION','CONFIRMED','REVIEW_REQUIRED','CREDITED','REJECTED'))
)
    """)
    op.execute("""
CREATE TABLE topup_requests (
id UUID NOT NULL,
buyer_id UUID NOT NULL,
action_id UUID NOT NULL,
rail VARCHAR(32) NOT NULL,
reference VARCHAR(100) NOT NULL,
credit_amount BIGINT NOT NULL,
amount NUMERIC(36, 18) NOT NULL,
asset VARCHAR(16) NOT NULL,
recipient VARCHAR(200) NOT NULL,
network VARCHAR(200) NOT NULL,
token_contract VARCHAR(200) NOT NULL,
route_id UUID,
rate NUMERIC(36, 18) NOT NULL,
rate_source VARCHAR(64) NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
state VARCHAR(32) DEFAULT 'DETECTED' NOT NULL,
event_id UUID,
journal_id UUID,
PRIMARY KEY (id),
CONSTRAINT uq_topup_action UNIQUE (buyer_id, action_id),
CONSTRAINT uq_topup_reference UNIQUE (reference),
CONSTRAINT uq_topup_settlement UNIQUE (event_id),
CONSTRAINT uq_topup_journal UNIQUE (journal_id),
CONSTRAINT ck_topup_rail CHECK (rail IN ('BANK_TRANSFER','USDT_BINANCE')),
CONSTRAINT ck_topup_amount CHECK (credit_amount BETWEEN 1 AND 999999999999999 AND amount>0
AND rate>0),
CONSTRAINT ck_topup_ttl CHECK (expires_at>created_at AND expires_at<=created_at+interval '30
minutes'),
CONSTRAINT ck_topup_state CHECK (state IN
('DETECTED','PENDING_CONFIRMATION','CONFIRMED','REVIEW_REQUIRED','CREDITED','REJECTED')),
CONSTRAINT ck_topup_credit CHECK ((state='CREDITED')=(journal_id IS NOT NULL) AND
(journal_id IS NULL OR event_id IS NOT NULL)),
CONSTRAINT fk_topup_buyer FOREIGN KEY(buyer_id) REFERENCES users (id),
CONSTRAINT fk_topup_route FOREIGN KEY(route_id) REFERENCES deposit_routes (id),
CONSTRAINT fk_topup_event FOREIGN KEY(event_id) REFERENCES settlement_events (id),
CONSTRAINT fk_topup_journal FOREIGN KEY(journal_id) REFERENCES wallet_journals (id)
)
    """)
    op.execute("""
CREATE TABLE settlement_observations (
id UUID NOT NULL,
event_id UUID NOT NULL,
digest BYTEA NOT NULL,
source VARCHAR(32) NOT NULL,
conflict BOOLEAN NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_settlement_observation UNIQUE (event_id, digest),
CONSTRAINT fk_settlement_observation FOREIGN KEY(event_id) REFERENCES settlement_events (id)
)
    """)
    op.execute("""
CREATE TABLE topup_review_actions (
id UUID NOT NULL,
actor_id UUID NOT NULL,
event_id UUID NOT NULL,
intent_id UUID,
decision VARCHAR(16) DEFAULT 'APPROVE' NOT NULL,
reason VARCHAR(300) NOT NULL,
source_digest BYTEA NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
applied BOOLEAN DEFAULT false NOT NULL,
PRIMARY KEY (id),
CONSTRAINT fk_topup_review_actor FOREIGN KEY(actor_id) REFERENCES users (id),
CONSTRAINT fk_topup_review_event FOREIGN KEY(event_id) REFERENCES settlement_events (id),
CONSTRAINT fk_topup_review_intent FOREIGN KEY(intent_id) REFERENCES topup_requests (id),
CONSTRAINT ck_topup_review_decision CHECK(decision IN ('APPROVE','REJECT','ESCALATE') AND
  (decision<>'APPROVE' OR intent_id IS NOT NULL))
)
    """)
    op.execute("""
CREATE TABLE reconciliation_checkpoints (
provider VARCHAR(16) NOT NULL,
completed_until TIMESTAMP WITH TIME ZONE NOT NULL,
PRIMARY KEY (provider)
)
    """)
    op.execute("""
ALTER TABLE settlement_events ADD CONSTRAINT fk_settlement_intent FOREIGN KEY(intent_id)
REFERENCES topup_requests (id)
    """)
    op.execute("""
CREATE UNIQUE INDEX uq_topup_active_route ON topup_requests (route_id) WHERE state NOT IN
('CREDITED','REJECTED')
    """)
    upgrade_guards()


def upgrade_guards():
    op.execute("""CREATE FUNCTION guard_topup_request() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR TG_OP='TRUNCATE' THEN
        RAISE EXCEPTION 'Top-up history is immutable' USING ERRCODE='23514';
      END IF;
      IF TG_OP='UPDATE' AND (
        (to_jsonb(NEW)-ARRAY['state','event_id','journal_id']) IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['state','event_id','journal_id']) OR
        (OLD.state IN ('CREDITED','REJECTED') AND NEW IS DISTINCT FROM OLD)) THEN
        RAISE EXCEPTION 'Top-up quote and completed credit are immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER topup_request_guard BEFORE UPDATE OR DELETE ON topup_requests "
        "FOR EACH ROW EXECUTE FUNCTION guard_topup_request()"
    )
    op.execute(
        "CREATE TRIGGER topup_request_truncate BEFORE TRUNCATE ON topup_requests "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_topup_request()"
    )
    op.execute("""CREATE FUNCTION guard_settlement_event() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP IN ('DELETE','TRUNCATE') THEN
        RAISE EXCEPTION 'Settlement evidence cannot be deleted' USING ERRCODE='23514';
      END IF;
      IF ROW(NEW.id,NEW.provider,NEW.transaction_id,NEW.facts,NEW.digest) IS DISTINCT FROM
         ROW(OLD.id,OLD.provider,OLD.transaction_id,OLD.facts,OLD.digest)
        OR (OLD.confirmed AND NOT NEW.confirmed)
        OR (OLD.token_verified AND NOT NEW.token_verified)
        OR NEW.confirmations<OLD.confirmations
        OR NEW.required_confirmations<OLD.required_confirmations
        OR (OLD.conflict AND NOT NEW.conflict)
        OR (OLD.state='CREDITED' AND (NEW.state<>'CREDITED' OR
           NEW.intent_id IS DISTINCT FROM OLD.intent_id)) THEN
        RAISE EXCEPTION 'Settlement facts and confirmed progress are immutable'
          USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER settlement_event_guard BEFORE UPDATE OR DELETE ON settlement_events "
        "FOR EACH ROW EXECUTE FUNCTION guard_settlement_event()"
    )
    op.execute(
        "CREATE TRIGGER settlement_event_truncate BEFORE TRUNCATE ON settlement_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_settlement_event()"
    )
    for table in ["settlement_observations", "deposit_routes"]:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_payment_history()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()"
        )
    op.execute("""CREATE FUNCTION check_topup_credit() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE intent_uuid UUID;
    BEGIN
      intent_uuid := coalesce((to_jsonb(NEW)->>'subject_id')::uuid,
                              (to_jsonb(NEW)->>'id')::uuid);
      IF TG_TABLE_NAME='wallet_journals' AND to_jsonb(NEW)->>'kind'<>'TOPUP'
        THEN RETURN NEW; END IF;
      IF EXISTS(SELECT 1 FROM topup_requests t WHERE t.id=intent_uuid AND t.state<>'CREDITED')
         AND TG_TABLE_NAME<>'wallet_journals' THEN RETURN NEW; END IF;
      IF NOT EXISTS(
        SELECT 1 FROM topup_requests t JOIN settlement_events e ON e.id=t.event_id
        JOIN wallet_journals j ON j.id=t.journal_id AND j.kind='TOPUP' AND j.subject_id=t.id
        JOIN wallet_postings p ON p.journal_id=j.id
        JOIN wallet_accounts a ON a.id=p.account_id AND a.user_id=t.buyer_id
        LEFT JOIN deposit_routes r ON r.id=t.route_id
        WHERE t.id=intent_uuid AND t.state='CREDITED' AND p.delta=t.credit_amount
          AND t.credit_amount=floor(t.amount*t.rate)
          AND e.state='CREDITED' AND e.intent_id=t.id AND e.confirmed AND e.token_verified
          AND e.confirmations>=greatest(e.required_confirmations,
              coalesce(r.minimum_confirmations,1))
          AND e.facts->>'recipient'=t.recipient AND e.facts->>'asset'=t.asset
          AND e.facts->>'network'=t.network AND e.facts->>'token_contract'=t.token_contract
          AND (e.facts->>'amount')::numeric=t.amount AND e.facts->>'direction'='in'
          AND j.evidence=e.digest) THEN
        RAISE EXCEPTION 'Top-up credit requires a unique verified settlement and ledger journal'
          USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER topup_credit_commit AFTER INSERT OR UPDATE ON "
        "topup_requests DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_topup_credit()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER topup_journal_source AFTER INSERT ON wallet_journals "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_topup_credit()"
    )
    op.execute("""CREATE FUNCTION guard_topup_review() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP IN ('DELETE','TRUNCATE') THEN
        RAISE EXCEPTION 'Review history is immutable' USING ERRCODE='23514';
      END IF;
      IF (to_jsonb(NEW)-'applied') IS DISTINCT FROM (to_jsonb(OLD)-'applied')
        OR (OLD.applied AND NOT NEW.applied) THEN
        RAISE EXCEPTION 'Review decision is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER topup_review_guard BEFORE UPDATE OR DELETE ON topup_review_actions "
        "FOR EACH ROW EXECUTE FUNCTION guard_topup_review()"
    )
    op.execute(
        "CREATE TRIGGER topup_review_truncate BEFORE TRUNCATE ON topup_review_actions "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_topup_review()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM topup_requests) OR EXISTS(SELECT 1 FROM settlement_events)
        OR EXISTS(SELECT 1 FROM deposit_routes) THEN
        RAISE EXCEPTION 'Top-up evidence requires a forward migration';
      END IF;
    END $$""")
    op.execute("DROP TRIGGER topup_journal_source ON wallet_journals")
    op.drop_constraint("fk_settlement_intent", "settlement_events")
    for table in [
        "reconciliation_checkpoints",
        "topup_review_actions",
        "settlement_observations",
        "topup_requests",
        "settlement_events",
        "deposit_routes",
    ]:
        op.drop_table(table)
    for name in [
        "guard_topup_request",
        "guard_settlement_event",
        "check_topup_credit",
        "guard_topup_review",
    ]:
        op.execute(f"DROP FUNCTION {name}()")
