"""Credit orders and atomic ledger-backed payments; preserve Stars guard functions."""

import sqlalchemy as sa
from alembic import op

revision = "0016_credit_checkout"
down_revision = "0015_wallet_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE product_credit_prices (
      product_id UUID PRIMARY KEY CONSTRAINT fk_credit_price_product REFERENCES products(id),
      price_credits BIGINT NOT NULL,
      CONSTRAINT ck_credit_price CHECK(price_credits BETWEEN 1 AND 999999999999999))""")
    op.add_column(
        "orders",
        sa.Column("payment_method", sa.String(32), nullable=False, server_default="TELEGRAM_STARS"),
    )
    op.create_check_constraint(
        "ck_orders_method", "orders", "payment_method IN ('TELEGRAM_STARS','WALLET_CREDIT')"
    )
    op.add_column("order_items", sa.Column("price_credits", sa.BigInteger))
    op.alter_column("order_items", "price_stars", nullable=True)
    op.drop_constraint("ck_order_items_price", "order_items")
    op.create_check_constraint(
        "ck_order_items_price",
        "order_items",
        "(currency='XTR' AND price_stars IS NOT NULL AND price_stars>0 "
        "AND price_credits IS NULL) OR (currency='CRD' AND price_stars IS NULL "
        "AND price_credits IS NOT NULL AND price_credits BETWEEN 1 AND 999999999999999)",
    )
    op.add_column(
        "payments",
        sa.Column("payment_method", sa.String(32), nullable=False, server_default="TELEGRAM_STARS"),
    )
    op.add_column("payments", sa.Column("wallet_journal_id", sa.Uuid))
    op.create_foreign_key(
        "fk_payment_wallet_journal", "payments", "wallet_journals", ["wallet_journal_id"], ["id"]
    )
    op.create_unique_constraint("uq_payment_wallet_journal", "payments", ["wallet_journal_id"])
    for name in ["charge_id", "payload", "provider_charge_id"]:
        op.alter_column("payments", name, nullable=True)
    op.create_check_constraint(
        "ck_payment_method_shape",
        "payments",
        "(payment_method='TELEGRAM_STARS' AND wallet_journal_id IS NULL "
        "AND charge_id IS NOT NULL AND payload IS NOT NULL AND provider_charge_id IS NOT NULL) OR "
        "(payment_method='WALLET_CREDIT' AND wallet_journal_id IS NOT NULL AND charge_id IS NULL "
        "AND payload IS NULL AND provider_charge_id IS NULL AND currency='CRD' AND amount>0 "
        "AND status='MATCHED' AND NOT is_recurring AND NOT is_first_recurring "
        "AND NOT has_order_info AND subscription_expiration_date IS NULL "
        "AND shipping_option_id IS NULL)",
    )
    op.execute("""CREATE FUNCTION guard_order_payment_method() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.payment_method IS DISTINCT FROM OLD.payment_method THEN
          RAISE EXCEPTION 'Order payment method is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER order_payment_method_guard BEFORE UPDATE ON orders "
        "FOR EACH ROW EXECUTE FUNCTION guard_order_payment_method()"
    )
    # A disjoint trigger keeps the predecessor Stars proof unchanged and mandatory for Stars.
    op.execute("DROP TRIGGER committed_payment ON payments")
    op.execute(
        "CREATE CONSTRAINT TRIGGER committed_payment AFTER INSERT ON payments "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.payment_method='TELEGRAM_STARS') "
        "EXECUTE FUNCTION check_committed_payment()"
    )
    op.execute("""CREATE FUNCTION check_committed_credit_payment() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM orders o JOIN order_items i ON i.order_id=o.id
        JOIN users b ON b.id=o.buyer_id
        JOIN wallet_journals j ON j.id=NEW.wallet_journal_id
        JOIN wallet_postings x ON x.journal_id=j.id
        JOIN wallet_accounts a ON a.id=x.account_id AND a.user_id=o.buyer_id
        WHERE o.id=NEW.order_id AND o.status='PAID' AND o.attention_reason IS NULL
          AND o.payment_method='WALLET_CREDIT' AND i.currency='CRD'
          AND NEW.amount=i.price_credits AND NEW.payer_id=b.telegram_id
          AND j.kind='PURCHASE' AND j.subject_id=o.id AND j.actor_id=b.id
          AND x.delta=-NEW.amount AND EXISTS (
            SELECT 1 FROM fulfillments f WHERE f.payment_id=NEW.id
              AND f.order_id=o.id AND f.order_item_id=i.id
              AND fulfillment_entitlement_matches(f))) THEN
        RAISE EXCEPTION 'Credit debit, paid order and entitlement must commit together'
          USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER committed_credit_payment AFTER INSERT ON payments "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.payment_method='WALLET_CREDIT') "
        "EXECUTE FUNCTION check_committed_credit_payment()"
    )
    op.execute("""CREATE FUNCTION check_purchase_journal() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.kind='PURCHASE' AND NOT EXISTS(SELECT 1 FROM payments p
        WHERE p.wallet_journal_id=NEW.id AND p.payment_method='WALLET_CREDIT') THEN
        RAISE EXCEPTION 'Purchase debit requires wallet order payment' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER purchase_journal_source AFTER INSERT ON wallet_journals "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_purchase_journal()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM orders WHERE payment_method='WALLET_CREDIT')
        OR EXISTS(SELECT 1 FROM product_credit_prices) THEN
        RAISE EXCEPTION 'Credit orders and prices require a forward migration';
      END IF;
    END $$""")
    op.execute("DROP TRIGGER purchase_journal_source ON wallet_journals")
    op.execute("DROP FUNCTION check_purchase_journal()")
    op.execute("DROP TRIGGER committed_credit_payment ON payments")
    op.execute("DROP FUNCTION check_committed_credit_payment()")
    op.execute("DROP TRIGGER committed_payment ON payments")
    op.execute(
        "CREATE CONSTRAINT TRIGGER committed_payment AFTER INSERT ON payments "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_committed_payment()"
    )
    op.execute("DROP TRIGGER order_payment_method_guard ON orders")
    op.execute("DROP FUNCTION guard_order_payment_method()")
    for name in [
        "ck_payment_method_shape",
        "uq_payment_wallet_journal",
        "fk_payment_wallet_journal",
    ]:
        op.drop_constraint(name, "payments")
    op.drop_column("payments", "wallet_journal_id")
    op.drop_column("payments", "payment_method")
    for name in ["charge_id", "payload", "provider_charge_id"]:
        op.alter_column("payments", name, nullable=False)
    op.drop_constraint("ck_order_items_price", "order_items")
    op.drop_column("order_items", "price_credits")
    op.alter_column("order_items", "price_stars", nullable=False)
    op.create_check_constraint(
        "ck_order_items_price", "order_items", "currency='XTR' AND price_stars>0"
    )
    op.drop_constraint("ck_orders_method", "orders")
    op.drop_column("orders", "payment_method")
    op.drop_table("product_credit_prices")
