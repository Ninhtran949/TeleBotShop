"""Immutable double-entry credit ledger; no external money movement."""

from alembic import op

revision = "0015_wallet_ledger"
down_revision = "0014_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE wallet_accounts (
      id UUID PRIMARY KEY, user_id UUID CONSTRAINT fk_wallet_account_user REFERENCES users(id),
      system_code VARCHAR(16), balance BIGINT NOT NULL DEFAULT 0,
      CONSTRAINT uq_wallet_account_user UNIQUE(user_id),
      CONSTRAINT uq_wallet_account_system UNIQUE(system_code),
      CONSTRAINT ck_wallet_account_owner CHECK ((user_id IS NULL) <> (system_code IS NULL)),
      CONSTRAINT ck_wallet_system CHECK (system_code IS NULL OR
        system_code IN ('CLEARING','SALES','ADJUSTMENT')),
      CONSTRAINT ck_wallet_nonnegative CHECK(user_id IS NULL OR balance>=0),
      CONSTRAINT ck_wallet_balance_bound CHECK(balance BETWEEN
        -999999999999999 AND 999999999999999))""")
    op.execute("""CREATE TABLE wallet_journals (
      id UUID PRIMARY KEY, operation_key VARCHAR(128) NOT NULL,
      kind VARCHAR(16) NOT NULL, subject_id UUID NOT NULL,
      actor_id UUID CONSTRAINT fk_wallet_journal_actor REFERENCES users(id),
      reason VARCHAR(300) NOT NULL, evidence BYTEA NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT uq_wallet_operation UNIQUE(operation_key),
      CONSTRAINT ck_wallet_journal_kind CHECK(kind IN
        ('ADJUSTMENT','TOPUP','PURCHASE','REFUND','REVERSAL')),
      CONSTRAINT ck_wallet_journal_evidence CHECK(octet_length(evidence)=32
        AND length(reason) BETWEEN 1 AND 300))""")
    op.execute("""CREATE TABLE wallet_postings (
      journal_id UUID CONSTRAINT fk_wallet_posting_journal REFERENCES wallet_journals(id),
      account_id UUID CONSTRAINT fk_wallet_posting_account REFERENCES wallet_accounts(id),
      delta BIGINT NOT NULL, PRIMARY KEY(journal_id,account_id),
      CONSTRAINT ck_wallet_posting_delta CHECK(delta<>0 AND delta BETWEEN
        -999999999999999 AND 999999999999999))""")
    op.execute("""CREATE TABLE wallet_permissions (
      user_id UUID CONSTRAINT fk_wallet_permission_user REFERENCES users(id),
      permission VARCHAR(16), PRIMARY KEY(user_id,permission),
      CONSTRAINT ck_wallet_permission CHECK(permission IN
        ('ADJUST','REVIEW','REFUND','AUDIT','PRICE')))""")
    op.execute("""CREATE FUNCTION guard_wallet_account() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.balance<>0 THEN
          RAISE EXCEPTION 'Wallet must start at zero' USING ERRCODE='23514';
        END IF;
      ELSIF TG_OP='UPDATE' THEN
        IF pg_trigger_depth()<2 OR ROW(NEW.id,NEW.user_id,NEW.system_code) IS DISTINCT FROM
           ROW(OLD.id,OLD.user_id,OLD.system_code) THEN
          RAISE EXCEPTION 'Wallet balance requires ledger postings' USING ERRCODE='23514';
        END IF;
      ELSE RAISE EXCEPTION 'Wallet history is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER wallet_account_guard BEFORE INSERT OR UPDATE OR DELETE ON "
        "wallet_accounts FOR EACH ROW EXECUTE FUNCTION guard_wallet_account()"
    )
    op.execute(
        "CREATE TRIGGER wallet_account_truncate BEFORE TRUNCATE ON wallet_accounts "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_wallet_account()"
    )
    op.execute("""CREATE FUNCTION apply_wallet_posting() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      UPDATE wallet_accounts SET balance=balance+NEW.delta WHERE id=NEW.account_id;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER wallet_posting_projection AFTER INSERT ON wallet_postings "
        "FOR EACH ROW EXECUTE FUNCTION apply_wallet_posting()"
    )
    for table in ["wallet_journals", "wallet_postings"]:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_payment_history()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()"
        )
    op.execute("""CREATE FUNCTION check_wallet_journal() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE journal_uuid UUID := (to_jsonb(NEW)->>TG_ARGV[0])::uuid;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM wallet_postings p JOIN wallet_accounts a ON a.id=p.account_id
        WHERE p.journal_id=journal_uuid HAVING count(*)=2 AND sum(p.delta)=0
        AND count(a.user_id)=1) THEN
        RAISE EXCEPTION 'Wallet journal must balance one customer and one system account'
          USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER wallet_journal_balanced AFTER INSERT ON wallet_journals "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_wallet_journal('id')"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER wallet_postings_balanced AFTER INSERT ON wallet_postings "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_wallet_journal('journal_id')"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM wallet_journals) OR EXISTS(SELECT 1 FROM wallet_permissions) THEN
        RAISE EXCEPTION 'Wallet history requires explicit retention and forward migration';
      END IF;
    END $$""")
    for table in ["wallet_permissions", "wallet_postings", "wallet_journals", "wallet_accounts"]:
        op.drop_table(table)
    for name in ["check_wallet_journal", "apply_wallet_posting", "guard_wallet_account"]:
        op.execute(f"DROP FUNCTION {name}()")
