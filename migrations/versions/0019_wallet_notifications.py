"""Reuse the durable metadata-only notification outbox for wallet outcomes."""

from alembic import op

revision = "0019_wallet_notifications"
down_revision = "0018_credit_recovery"
branch_labels = None
depends_on = None


def kinds(wallet):
    values = (
        "'PAYMENT_REVIEW','INVOICE_UNCERTAIN','PAID_ATTENTION','DELIVERY_ATTENTION',"
        "'MANUAL_WAITING','BACKLOG','LOW_STOCK','REFUND_UNCERTAIN','TICKET_ACTIVITY','TICKET_REPLY'"
    )
    if wallet:
        values += ",'WALLET_TOPUP','TOPUP_REVIEW'"
    op.drop_constraint("ck_notification_kind", "notifications")
    op.create_check_constraint("ck_notification_kind", "notifications", "kind IN (" + values + ")")


def upgrade():
    kinds(True)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM notifications
      WHERE kind IN ('WALLET_TOPUP','TOPUP_REVIEW')) THEN
      RAISE EXCEPTION 'Wallet notification history requires a forward migration';
      END IF; END $$""")
    kinds(False)
