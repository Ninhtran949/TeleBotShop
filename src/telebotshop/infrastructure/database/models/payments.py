import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from telebotshop.infrastructure.database.models.orders import metadata

payments = sa.Table(
    "payments",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("charge_id", sa.String(1024), nullable=False),
    sa.Column("payer_id", sa.BigInteger),
    sa.Column("currency", sa.String(16), nullable=False),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("payload", sa.String(128), nullable=False),
    sa.Column("provider_charge_id", sa.String(1024), nullable=False),
    sa.Column("is_recurring", sa.Boolean, nullable=False),
    sa.Column("is_first_recurring", sa.Boolean, nullable=False),
    sa.Column("subscription_expiration_date", sa.BigInteger),
    sa.Column("has_order_info", sa.Boolean, nullable=False),
    sa.Column("shipping_option_id", sa.String(1024)),
    sa.Column("order_id", sa.Uuid, sa.ForeignKey("orders.id", name="fk_payments_order")),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("review_reason", sa.String(32)),
    sa.Column(
        "received_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("charge_id", name="uq_payments_charge"),
    sa.CheckConstraint("octet_length(charge_id) BETWEEN 1 AND 1024", name="ck_payments_charge"),
    sa.CheckConstraint("octet_length(payload) <= 128", name="ck_payments_payload"),
    sa.CheckConstraint("payer_id IS NULL OR payer_id > 0", name="ck_payments_payer"),
    sa.CheckConstraint(
        "(status = 'MATCHED' AND order_id IS NOT NULL AND review_reason IS NULL) OR "
        "(status = 'REQUIRES_REVIEW' AND review_reason IN ('UNKNOWN_ORDER', 'MISSING_PAYER', "
        "'WRONG_BUYER', 'WRONG_AMOUNT', 'WRONG_CURRENCY', 'EXTRA_CHARGE', 'UNSUPPORTED', "
        "'INVALID_ORDER_STATE') AND review_reason IS NOT NULL)",
        name="ck_payments_state",
    ),
)
sa.Index(
    "uq_payments_primary_order",
    payments.c.order_id,
    unique=True,
    postgresql_where=sa.text("status = 'MATCHED'"),
)

payment_conflicts = sa.Table(
    "payment_conflicts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "payment_id",
        sa.Uuid,
        sa.ForeignKey("payments.id", name="fk_conflicts_payment"),
        nullable=False,
    ),
    sa.Column("digest", sa.LargeBinary, nullable=False),
    sa.Column("evidence", JSONB, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("payment_id", "digest", name="uq_payment_conflict"),
    sa.CheckConstraint("octet_length(digest) = 32", name="ck_conflict_digest"),
    sa.CheckConstraint("jsonb_typeof(evidence) = 'object'", name="ck_conflict_evidence"),
)

fulfillments = sa.Table(
    "fulfillments",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "order_id",
        sa.Uuid,
        sa.ForeignKey("orders.id", name="fk_fulfillments_order"),
        nullable=False,
    ),
    sa.Column(
        "order_item_id",
        sa.Uuid,
        sa.ForeignKey("order_items.id", name="fk_fulfillments_item"),
        nullable=False,
    ),
    sa.Column(
        "payment_id",
        sa.Uuid,
        sa.ForeignKey("payments.id", name="fk_fulfillments_payment"),
        nullable=False,
    ),
    sa.Column(
        "inventory_unit_id",
        sa.Uuid,
        sa.ForeignKey("inventory_units.id", name="fk_fulfillments_unit"),
        nullable=False,
    ),
    sa.Column(
        "buyer_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_fulfillments_buyer"), nullable=False
    ),
    sa.Column("destination_telegram_id", sa.BigInteger, nullable=False),
    sa.Column("product_type", sa.String(32), nullable=False),
    sa.Column("state", sa.String(16), nullable=False, server_default="READY"),
    sa.Column("active_attempt_id", sa.Uuid),
    sa.Column(
        "due_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.Column("send_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("hold", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("attention_code", sa.String(32)),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("order_item_id", name="uq_fulfillments_item"),
    sa.ForeignKeyConstraint(
        ["order_item_id", "inventory_unit_id"],
        ["order_items.id", "order_items.inventory_unit_id"],
        name="fk_fulfillments_item_unit",
    ),
    sa.ForeignKeyConstraint(
        ["active_attempt_id"],
        ["delivery_attempts.id"],
        name="fk_fulfillments_attempt",
        use_alter=True,
    ),
    sa.CheckConstraint(
        "state IN ('READY', 'RETRY_WAIT', 'CLAIMED', 'SENDING', 'SENT', 'UNCERTAIN', 'FAILED')",
        name="ck_fulfillments_state",
    ),
    sa.CheckConstraint("send_count BETWEEN 0 AND 5", name="ck_fulfillments_send_count"),
    sa.CheckConstraint(
        "(state = 'READY') = (active_attempt_id IS NULL)", name="ck_fulfillments_active"
    ),
    sa.CheckConstraint(
        "(state IN ('FAILED', 'UNCERTAIN') AND attention_code IS NOT NULL AND attention_code IN "
        "('REJECTED', 'NETWORK', 'LEASE_EXPIRED', 'INVALID_ENTITLEMENT', "
        "'CRYPTO', 'RETRY_EXHAUSTED')) "
        "OR (state NOT IN ('FAILED', 'UNCERTAIN') AND attention_code IS NULL)",
        name="ck_fulfillments_attention",
    ),
    sa.CheckConstraint("product_type IN ('UNIQUE_KEY', 'LICENSE')", name="ck_fulfillments_type"),
    sa.CheckConstraint("destination_telegram_id > 0", name="ck_fulfillments_destination"),
)
sa.Index(
    "ix_fulfillments_due",
    fulfillments.c.due_at,
    fulfillments.c.id,
    postgresql_where=sa.text("state IN ('READY', 'RETRY_WAIT') AND NOT hold"),
)
sa.Index("ix_fulfillments_order", fulfillments.c.order_id)
sa.Index("ix_fulfillments_attempt", fulfillments.c.active_attempt_id)
