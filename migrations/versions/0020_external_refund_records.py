"""Append-only operator evidence, never a bank/crypto payout implementation."""

import sqlalchemy as sa
from alembic import op

revision = "0020_external_refund_records"
down_revision = "0019_wallet_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_refund_records",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "topup_id",
            sa.Uuid,
            sa.ForeignKey("topup_requests.id", name="fk_cash_refund_topup"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid,
            sa.ForeignKey("users.id", name="fk_cash_refund_actor"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("source_digest", sa.LargeBinary, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "state IN ('REQUESTED','UNCERTAIN','CONFIRMED','REJECTED')", name="ck_cash_refund_state"
        ),
        sa.CheckConstraint("octet_length(source_digest)=32", name="ck_cash_refund_source"),
        sa.UniqueConstraint("topup_id", "state", name="uq_cash_refund_state"),
    )
    op.execute("""CREATE FUNCTION check_cash_refund_record() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE previous TEXT;
    BEGIN
      PERFORM 1 FROM topup_requests WHERE id=NEW.topup_id AND state='CREDITED' FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Credited top-up required' USING ERRCODE='23514'; END IF;
      SELECT state INTO previous FROM external_refund_records WHERE topup_id=NEW.topup_id
        ORDER BY created_at DESC,id DESC LIMIT 1;
      IF (previous IS NULL AND NEW.state<>'REQUESTED') OR
         (previous IS NOT NULL AND (previous IN ('CONFIRMED','REJECTED') OR
           NEW.state IN ('REQUESTED',previous))) OR
         (NEW.state='CONFIRMED' AND NOT EXISTS(
           SELECT 1 FROM topup_reversals WHERE topup_id=NEW.topup_id)) THEN
        RAISE EXCEPTION 'Invalid manual cash refund transition' USING ERRCODE='23514';
      END IF;
      NEW.created_at:=clock_timestamp();
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER cash_refund_transition BEFORE INSERT ON external_refund_records "
        "FOR EACH ROW EXECUTE FUNCTION check_cash_refund_record()"
    )
    op.execute(
        "CREATE TRIGGER cash_refund_immutable BEFORE UPDATE OR DELETE ON external_refund_records "
        "FOR EACH ROW EXECUTE FUNCTION guard_payment_history()"
    )
    op.execute(
        "CREATE TRIGGER cash_refund_no_truncate BEFORE TRUNCATE ON external_refund_records "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM external_refund_records) THEN
      RAISE EXCEPTION 'External refund evidence requires a forward migration'; END IF; END $$""")
    op.drop_table("external_refund_records")
    op.execute("DROP FUNCTION check_cash_refund_record()")
