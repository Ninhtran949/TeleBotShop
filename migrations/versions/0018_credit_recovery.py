"""Compensating credit refunds and top-up reversal; no external refund calls."""

import sqlalchemy as sa
from alembic import op

revision = "0018_credit_recovery"
down_revision = "0017_topups"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE wallet_refund_actions (
id UUID NOT NULL,
order_id UUID NOT NULL,
actor_id UUID NOT NULL,
reason VARCHAR(300) NOT NULL,
source_digest BYTEA NOT NULL,
expected BYTEA NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
journal_id UUID,
PRIMARY KEY (id),
CONSTRAINT uq_wallet_refund_journal UNIQUE (journal_id),
CONSTRAINT ck_wallet_refund_evidence CHECK (octet_length(source_digest)=32 AND
octet_length(expected)=32),
CONSTRAINT fk_wallet_refund_order FOREIGN KEY(order_id) REFERENCES orders (id),
CONSTRAINT fk_wallet_refund_actor FOREIGN KEY(actor_id) REFERENCES users (id),
CONSTRAINT fk_wallet_refund_journal FOREIGN KEY(journal_id) REFERENCES wallet_journals (id)
)
    """)
    op.execute("""
CREATE UNIQUE INDEX uq_wallet_refund_order ON wallet_refund_actions (order_id) WHERE
journal_id IS NOT NULL
    """)
    op.execute("""
CREATE TABLE topup_reversals (
topup_id UUID NOT NULL,
journal_id UUID NOT NULL,
actor_id UUID NOT NULL,
reason VARCHAR(300) NOT NULL,
evidence BYTEA NOT NULL,
PRIMARY KEY (topup_id),
CONSTRAINT uq_topup_reversal_journal UNIQUE (journal_id),
CONSTRAINT fk_reversal_topup FOREIGN KEY(topup_id) REFERENCES topup_requests (id),
CONSTRAINT fk_reversal_journal FOREIGN KEY(journal_id) REFERENCES wallet_journals (id),
CONSTRAINT fk_reversal_actor FOREIGN KEY(actor_id) REFERENCES users (id)
)
    """)
    upgrade_guards()


def clone_wallet_guard(name, target):
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef(to_regproc(:name))"), {"name": name}
    )
    if (
        not definition
        or definition.count(name + "()") != 1
        or "refund_confirmed(" not in definition
    ):
        raise RuntimeError("Unexpected predecessor financial guard")
    op.execute(
        definition.replace(name + "()", target + "()", 1).replace(
            "refund_confirmed(", "credit_refund_confirmed("
        )
    )


def delivery_triggers(wallet):
    for table, key in [("fulfillments", "id"), ("delivery_attempts", "work_id")]:
        name = table + "_commit"
        op.execute(f"DROP TRIGGER {name} ON {table}")
        condition = f"WHEN (NOT is_credit_work(NEW.{key})) " if wallet else ""
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {name} AFTER INSERT OR UPDATE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            + condition
            + f"EXECUTE FUNCTION check_delivery_commit('{key}')"
        )
        if wallet:
            op.execute(
                f"CREATE CONSTRAINT TRIGGER credit_{name} AFTER INSERT OR UPDATE ON "
                f"{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                f"WHEN (is_credit_work(NEW.{key})) "
                f"EXECUTE FUNCTION check_credit_delivery_commit('{key}')"
            )


def upgrade_guards():
    op.execute("""CREATE FUNCTION credit_refund_confirmed(order_uuid UUID) RETURNS boolean
    LANGUAGE sql STABLE AS $$ SELECT EXISTS(
      SELECT 1 FROM wallet_refund_actions r JOIN wallet_journals j ON j.id=r.journal_id
      JOIN payments p ON p.order_id=r.order_id AND p.payment_method='WALLET_CREDIT'
      JOIN wallet_postings x ON x.journal_id=j.id
      JOIN wallet_accounts a ON a.id=x.account_id
      JOIN orders o ON o.id=p.order_id AND a.user_id=o.buyer_id
      WHERE r.order_id=order_uuid AND j.kind='REFUND' AND j.subject_id=o.id
        AND x.delta=p.amount AND j.actor_id=r.actor_id AND j.evidence=r.source_digest) $$""")
    clone_wallet_guard("guard_order", "guard_credit_order")
    op.execute("DROP TRIGGER order_guard ON orders")
    op.execute(
        "CREATE TRIGGER order_guard BEFORE UPDATE OR DELETE ON orders FOR EACH ROW "
        "WHEN (OLD.payment_method='TELEGRAM_STARS') EXECUTE FUNCTION guard_order()"
    )
    op.execute(
        "CREATE TRIGGER credit_order_guard BEFORE UPDATE OR DELETE ON orders FOR EACH ROW "
        "WHEN (OLD.payment_method='WALLET_CREDIT') EXECUTE FUNCTION guard_credit_order()"
    )
    op.execute("""CREATE FUNCTION is_credit_work(work_uuid UUID) RETURNS boolean
    LANGUAGE sql STABLE AS $$ SELECT EXISTS(SELECT 1 FROM fulfillments f JOIN orders o
      ON o.id=f.order_id WHERE f.id=work_uuid AND o.payment_method='WALLET_CREDIT') $$""")
    clone_wallet_guard("check_delivery_commit", "check_credit_delivery_commit")
    delivery_triggers(True)
    op.execute("""CREATE FUNCTION check_credit_reversal_source() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.kind='REFUND' AND NOT credit_refund_confirmed(NEW.subject_id) THEN
        RAISE EXCEPTION 'Refund credit requires its original debit and authorized refund intent'
          USING ERRCODE='23514';
      ELSIF NEW.kind='REVERSAL' AND NOT EXISTS(SELECT 1 FROM topup_reversals r
        JOIN topup_requests t ON t.id=r.topup_id JOIN wallet_postings p ON p.journal_id=NEW.id
        JOIN wallet_accounts a ON a.id=p.account_id AND a.user_id=t.buyer_id
        WHERE r.journal_id=NEW.id AND r.actor_id=NEW.actor_id AND r.evidence=NEW.evidence
          AND NEW.subject_id=t.id AND p.delta=-t.credit_amount AND t.state='CREDITED') THEN
        RAISE EXCEPTION 'Top-up reversal requires its original credited top-up'
          USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER credit_reversal_source AFTER INSERT ON wallet_journals "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_credit_reversal_source()"
    )
    op.execute("""CREATE FUNCTION guard_wallet_refund_action() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP IN ('DELETE','TRUNCATE') THEN
        RAISE EXCEPTION 'Credit refund history is immutable' USING ERRCODE='23514';
      END IF;
      IF (to_jsonb(NEW)-'journal_id') IS DISTINCT FROM (to_jsonb(OLD)-'journal_id')
        OR (OLD.journal_id IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Credit refund evidence is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER wallet_refund_guard BEFORE UPDATE OR DELETE ON wallet_refund_actions "
        "FOR EACH ROW EXECUTE FUNCTION guard_wallet_refund_action()"
    )
    op.execute(
        "CREATE TRIGGER wallet_refund_truncate BEFORE TRUNCATE ON wallet_refund_actions "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_wallet_refund_action()"
    )
    op.execute(
        "CREATE TRIGGER topup_reversal_guard BEFORE UPDATE OR DELETE ON topup_reversals "
        "FOR EACH ROW EXECUTE FUNCTION guard_payment_history()"
    )
    op.execute(
        "CREATE TRIGGER topup_reversal_truncate BEFORE TRUNCATE ON topup_reversals "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM wallet_refund_actions)
      OR EXISTS(SELECT 1 FROM topup_reversals) THEN
      RAISE EXCEPTION 'Credit refund history requires a forward migration'; END IF; END $$""")
    for table in ["fulfillments", "delivery_attempts"]:
        op.execute(f"DROP TRIGGER credit_{table}_commit ON {table}")
    delivery_triggers(False)
    op.execute("DROP TRIGGER credit_order_guard ON orders")
    op.execute("DROP TRIGGER order_guard ON orders")
    op.execute(
        "CREATE TRIGGER order_guard BEFORE UPDATE OR DELETE ON orders FOR EACH ROW "
        "EXECUTE FUNCTION guard_order()"
    )
    op.execute("DROP TRIGGER credit_reversal_source ON wallet_journals")
    op.drop_table("topup_reversals")
    op.drop_table("wallet_refund_actions")
    for name in [
        "guard_credit_order",
        "check_credit_delivery_commit",
        "check_credit_reversal_source",
        "guard_wallet_refund_action",
    ]:
        op.execute(f"DROP FUNCTION {name}()")
    op.execute("DROP FUNCTION is_credit_work(uuid)")
    op.execute("DROP FUNCTION credit_refund_confirmed(uuid)")
