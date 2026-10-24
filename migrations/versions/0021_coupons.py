"""Credit-only coupons with immutable, atomically paid redemption snapshots."""

from alembic import op

revision = "0021_coupons"
down_revision = "0020_external_refund_records"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE coupons (
      id UUID PRIMARY KEY,
      code VARCHAR(32) NOT NULL CONSTRAINT uq_coupon_code UNIQUE,
      kind VARCHAR(16) NOT NULL,
      value BIGINT NOT NULL,
      expires_at TIMESTAMPTZ NOT NULL,
      usage_cap INTEGER NOT NULL,
      per_user_limit INTEGER NOT NULL,
      product_id UUID CONSTRAINT fk_coupon_product REFERENCES products(id),
      category_code VARCHAR(32) CONSTRAINT fk_coupon_category REFERENCES categories(code),
      active BOOLEAN NOT NULL DEFAULT true,
      created_by UUID NOT NULL CONSTRAINT fk_coupon_creator REFERENCES users(id),
      CONSTRAINT ck_coupon_code CHECK(code ~ '^[A-Z0-9][A-Z0-9_-]{2,31}$'),
      CONSTRAINT ck_coupon_discount CHECK(
        (kind='PERCENT' AND value BETWEEN 1 AND 99) OR
        (kind='FIXED' AND value BETWEEN 1 AND 999999999999999)),
      CONSTRAINT ck_coupon_caps CHECK(usage_cap BETWEEN 1 AND 1000000000
        AND per_user_limit BETWEEN 1 AND usage_cap),
      CONSTRAINT ck_coupon_scope CHECK(product_id IS NULL OR category_code IS NULL))""")
    op.execute("""CREATE TABLE coupon_redemptions (
      order_id UUID PRIMARY KEY CONSTRAINT fk_redemption_order REFERENCES orders(id),
      coupon_id UUID NOT NULL CONSTRAINT fk_redemption_coupon REFERENCES coupons(id),
      buyer_id UUID NOT NULL CONSTRAINT fk_redemption_buyer REFERENCES users(id),
      product_id UUID NOT NULL CONSTRAINT fk_redemption_product REFERENCES products(id),
      code VARCHAR(32) NOT NULL,
      base_credits BIGINT NOT NULL,
      discount_credits BIGINT NOT NULL,
      total_credits BIGINT NOT NULL,
      redeemed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      CONSTRAINT ck_redemption_snapshot CHECK(base_credits BETWEEN 1 AND 999999999999999
        AND discount_credits>0 AND total_credits>0
        AND total_credits=base_credits-discount_credits))""")
    op.execute("CREATE INDEX ix_redemption_coupon_buyer ON coupon_redemptions(coupon_id,buyer_id)")
    op.execute("""CREATE FUNCTION guard_coupon_config() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (to_jsonb(NEW)-'active') IS DISTINCT FROM (to_jsonb(OLD)-'active') THEN
        RAISE EXCEPTION 'Coupon configuration is immutable' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER coupon_config_guard BEFORE UPDATE ON coupons "
        "FOR EACH ROW EXECUTE FUNCTION guard_coupon_config()"
    )
    op.execute(
        "CREATE TRIGGER coupon_no_delete BEFORE DELETE ON coupons "
        "FOR EACH ROW EXECUTE FUNCTION guard_payment_history()"
    )
    op.execute(
        "CREATE TRIGGER coupon_no_truncate BEFORE TRUNCATE ON coupons "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()"
    )
    op.execute(
        "CREATE TRIGGER redemption_immutable BEFORE UPDATE OR DELETE ON coupon_redemptions "
        "FOR EACH ROW EXECUTE FUNCTION guard_payment_history()"
    )
    op.execute(
        "CREATE TRIGGER redemption_no_truncate BEFORE TRUNCATE ON coupon_redemptions "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()"
    )
    op.execute("""CREATE FUNCTION check_coupon_redemption() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE c coupons%ROWTYPE;
    BEGIN
      SELECT * INTO c FROM coupons WHERE id=NEW.coupon_id FOR UPDATE;
      IF NOT FOUND OR NOT c.active OR clock_timestamp()>=c.expires_at OR c.code<>NEW.code
         OR NEW.discount_credits<>(CASE WHEN c.kind='PERCENT'
              THEN NEW.base_credits*c.value/100 ELSE c.value END)
         OR NOT EXISTS (
           SELECT 1 FROM products p JOIN categories g ON g.id=p.category_id
           JOIN product_credit_prices pc ON pc.product_id=p.id
           WHERE p.id=NEW.product_id AND pc.price_credits=NEW.base_credits
             AND (c.product_id IS NULL OR c.product_id=p.id)
             AND (c.category_code IS NULL OR c.category_code=g.code))
         OR (SELECT count(*) FROM coupon_redemptions WHERE coupon_id=c.id)>=c.usage_cap
         OR (SELECT count(*) FROM coupon_redemptions
               WHERE coupon_id=c.id AND buyer_id=NEW.buyer_id)>=c.per_user_limit THEN
        RAISE EXCEPTION 'Coupon eligibility or usage unavailable' USING ERRCODE='23514';
      END IF;
      NEW.redeemed_at:=clock_timestamp();
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER redemption_eligibility BEFORE INSERT ON coupon_redemptions "
        "FOR EACH ROW EXECUTE FUNCTION check_coupon_redemption()"
    )
    op.execute("""CREATE FUNCTION check_committed_coupon() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM orders o JOIN order_items i ON i.order_id=o.id
        JOIN payments p ON p.order_id=o.id
        WHERE o.id=NEW.order_id AND o.buyer_id=NEW.buyer_id AND i.product_id=NEW.product_id
          AND o.payment_method='WALLET_CREDIT' AND o.status='PAID'
          AND i.currency='CRD' AND i.price_credits=NEW.total_credits
          AND p.payment_method='WALLET_CREDIT' AND p.amount=NEW.total_credits)
        OR NOT EXISTS (
          SELECT 1 FROM audit_logs a WHERE a.action='coupon.redeemed'
            AND a.target_id=NEW.order_id AND a.actor_id=NEW.buyer_id
            AND a.changes->>'coupon_id'=NEW.coupon_id::text
            AND a.changes->>'base_credits'=NEW.base_credits::text
            AND a.changes->>'discount_credits'=NEW.discount_credits::text
            AND a.changes->>'total_credits'=NEW.total_credits::text) THEN
        RAISE EXCEPTION 'Coupon, net payment and audit must commit together' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER committed_coupon AFTER INSERT ON coupon_redemptions "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_committed_coupon()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM coupons) OR EXISTS(SELECT 1 FROM coupon_redemptions) THEN
        RAISE EXCEPTION 'Coupon history requires a forward migration';
      END IF;
    END $$""")
    op.drop_table("coupon_redemptions")
    op.drop_table("coupons")
    for function in ("check_committed_coupon", "check_coupon_redemption", "guard_coupon_config"):
        op.execute(f"DROP FUNCTION {function}()")
