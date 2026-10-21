import sqlalchemy as sa

from telebotshop.infrastructure.database.models.inventory import metadata

orders = sa.Table(
    "orders",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "buyer_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_orders_buyer"), nullable=False
    ),
    sa.Column("checkout_action_id", sa.Uuid, nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("closed_at", sa.DateTime(timezone=True)),
    sa.Column("close_reason", sa.String(32)),
    sa.Column("attention_reason", sa.String(32)),
    sa.UniqueConstraint("buyer_id", "checkout_action_id", name="uq_orders_buyer_action"),
    sa.CheckConstraint(
        "status IN ('AWAITING_PAYMENT', 'EXPIRED', 'PAID', 'FULFILLING', 'DELIVERED', 'FAILED')",
        name="ck_orders_status",
    ),
    sa.CheckConstraint(
        "attention_reason IS NULL OR (status = 'PAID' AND attention_reason = 'RESERVATION_LOST')",
        name="ck_orders_attention",
    ),
    sa.CheckConstraint("expires_at >= created_at + interval '15 minutes'", name="ck_orders_ttl"),
    sa.CheckConstraint(
        "(status IN ('AWAITING_PAYMENT', 'PAID', 'FULFILLING', 'DELIVERED', 'FAILED') "
        "AND closed_at IS NULL "
        "AND close_reason IS NULL) OR "
        "(status IN ('EXPIRED', 'PAID', 'FULFILLING', 'DELIVERED', 'FAILED') "
        "AND closed_at IS NOT NULL "
        "AND close_reason IS NOT NULL "
        "AND close_reason IN ('CUSTOMER_CANCELLED', 'TTL_EXPIRED', 'INVOICE_REJECTED'))",
        name="ck_orders_close",
    ),
)
sa.Index(
    "ix_orders_expiry",
    orders.c.expires_at,
    orders.c.id,
    postgresql_where=sa.text("status = 'AWAITING_PAYMENT'"),
)
order_items = sa.Table(
    "order_items",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "order_id", sa.Uuid, sa.ForeignKey("orders.id", name="fk_order_items_order"), nullable=False
    ),
    sa.Column(
        "product_id",
        sa.Uuid,
        sa.ForeignKey("products.id", name="fk_order_items_product"),
        nullable=False,
    ),
    sa.Column("inventory_unit_id", sa.Uuid, nullable=False),
    sa.Column("sku", sa.String(32), nullable=False),
    sa.Column("product_type", sa.String(32), nullable=False),
    sa.Column("title", sa.String(80), nullable=False),
    sa.Column("description", sa.String(1200), nullable=False),
    sa.Column("price_stars", sa.BigInteger, nullable=False),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("currency", sa.String(3), nullable=False, server_default="XTR"),
    sa.UniqueConstraint("order_id", name="uq_order_items_order"),
    sa.UniqueConstraint("id", "inventory_unit_id", name="uq_order_item_unit"),
    sa.ForeignKeyConstraint(
        ["inventory_unit_id", "product_id"],
        ["inventory_units.id", "inventory_units.product_id"],
        name="fk_order_items_unit_product",
    ),
    sa.CheckConstraint("quantity = 1", name="ck_order_items_quantity"),
    sa.CheckConstraint("currency = 'XTR' AND price_stars > 0", name="ck_order_items_price"),
    sa.CheckConstraint("product_type IN ('UNIQUE_KEY', 'LICENSE')", name="ck_order_items_type"),
    sa.CheckConstraint("length(title) BETWEEN 1 AND 80", name="ck_order_items_title"),
)
invoice_intents = sa.Table(
    "invoice_intents",
    metadata,
    sa.Column(
        "order_id", sa.Uuid, sa.ForeignKey("orders.id", name="fk_invoice_order"), primary_key=True
    ),
    sa.Column("payload", sa.String(32), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("attempt_id", sa.Uuid),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("message_id", sa.BigInteger),
    sa.UniqueConstraint("payload", name="uq_invoice_payload"),
    sa.UniqueConstraint("attempt_id", name="uq_invoice_attempt"),
    sa.CheckConstraint("payload ~ '^[A-Za-z0-9_-]{32}$'", name="ck_invoice_payload"),
    sa.CheckConstraint(
        "(status IN ('READY', 'CANCELLED') AND attempt_id IS NULL AND started_at IS NULL "
        "AND completed_at IS NULL AND message_id IS NULL) OR "
        "(status = 'SENDING' AND attempt_id IS NOT NULL AND started_at IS NOT NULL "
        "AND completed_at IS NULL AND message_id IS NULL) OR "
        "(status IN ('SENT', 'REJECTED', 'UNCERTAIN') AND attempt_id IS NOT NULL "
        "AND started_at IS NOT NULL AND completed_at IS NOT NULL AND "
        "((status = 'SENT' AND message_id IS NOT NULL AND message_id > 0) OR "
        "(status <> 'SENT' AND message_id IS NULL)))",
        name="ck_invoice_state",
    ),
)
sa.Index(
    "ix_invoice_stale",
    invoice_intents.c.started_at,
    invoice_intents.c.order_id,
    postgresql_where=sa.text("status = 'SENDING'"),
)
pre_checkout_approvals = sa.Table(
    "pre_checkout_approvals",
    metadata,
    sa.Column(
        "order_id",
        sa.Uuid,
        sa.ForeignKey("orders.id", name="fk_pre_checkout_order"),
        primary_key=True,
    ),
    sa.Column("query_id", sa.String(256), nullable=False),
    sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("query_id", name="uq_pre_checkout_query"),
    sa.CheckConstraint(
        "length(btrim(query_id)) > 0 AND query_id !~ '[[:cntrl:]]'", name="ck_pre_checkout_query"
    ),
)
