import sqlalchemy as sa

from telebotshop.infrastructure.database.models.credit_orders import metadata

coupons = sa.Table(
    "coupons",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("code", sa.String(32), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("value", sa.BigInteger, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("usage_cap", sa.Integer, nullable=False),
    sa.Column("per_user_limit", sa.Integer, nullable=False),
    sa.Column("product_id", sa.Uuid, sa.ForeignKey("products.id", name="fk_coupon_product")),
    sa.Column(
        "category_code", sa.String(32), sa.ForeignKey("categories.code", name="fk_coupon_category")
    ),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column(
        "created_by", sa.Uuid, sa.ForeignKey("users.id", name="fk_coupon_creator"), nullable=False
    ),
    sa.UniqueConstraint("code", name="uq_coupon_code"),
    sa.CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9_-]{2,31}$'", name="ck_coupon_code"),
    sa.CheckConstraint(
        "(kind='PERCENT' AND value BETWEEN 1 AND 99) OR "
        "(kind='FIXED' AND value BETWEEN 1 AND 999999999999999)",
        name="ck_coupon_discount",
    ),
    sa.CheckConstraint(
        "usage_cap BETWEEN 1 AND 1000000000 AND per_user_limit BETWEEN 1 AND usage_cap",
        name="ck_coupon_caps",
    ),
    sa.CheckConstraint("product_id IS NULL OR category_code IS NULL", name="ck_coupon_scope"),
)

coupon_redemptions = sa.Table(
    "coupon_redemptions",
    metadata,
    sa.Column(
        "order_id",
        sa.Uuid,
        sa.ForeignKey("orders.id", name="fk_redemption_order"),
        primary_key=True,
    ),
    sa.Column(
        "coupon_id",
        sa.Uuid,
        sa.ForeignKey("coupons.id", name="fk_redemption_coupon"),
        nullable=False,
    ),
    sa.Column(
        "buyer_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_redemption_buyer"), nullable=False
    ),
    sa.Column(
        "product_id",
        sa.Uuid,
        sa.ForeignKey("products.id", name="fk_redemption_product"),
        nullable=False,
    ),
    sa.Column("code", sa.String(32), nullable=False),
    sa.Column("base_credits", sa.BigInteger, nullable=False),
    sa.Column("discount_credits", sa.BigInteger, nullable=False),
    sa.Column("total_credits", sa.BigInteger, nullable=False),
    sa.Column(
        "redeemed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.CheckConstraint(
        "base_credits BETWEEN 1 AND 999999999999999 AND discount_credits>0 "
        "AND total_credits>0 AND total_credits=base_credits-discount_credits",
        name="ck_redemption_snapshot",
    ),
)
sa.Index(
    "ix_redemption_coupon_buyer", coupon_redemptions.c.coupon_id, coupon_redemptions.c.buyer_id
)
