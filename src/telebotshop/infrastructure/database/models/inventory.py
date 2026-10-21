import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from telebotshop.infrastructure.database.models.catalog import metadata

inventory_crypto_state = sa.Table(
    "inventory_crypto_state",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("dedup_verifier", sa.LargeBinary, nullable=False),
    sa.Column("key_verifiers", JSONB, nullable=False),
    sa.CheckConstraint("id = 1", name="ck_inventory_crypto_singleton"),
    sa.CheckConstraint("octet_length(dedup_verifier) = 32", name="ck_inventory_crypto_dedup"),
    sa.CheckConstraint("jsonb_typeof(key_verifiers) = 'object'", name="ck_inventory_crypto_keys"),
)
inventory_units = sa.Table(
    "inventory_units",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "product_id",
        sa.Uuid,
        sa.ForeignKey("products.id", name="fk_inventory_units_product"),
        nullable=False,
    ),
    sa.Column("product_type", sa.String(32), nullable=False),
    sa.Column("fingerprint", sa.LargeBinary, nullable=False),
    sa.Column("key_version", sa.Integer, nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="AVAILABLE"),
    sa.Column("reservation_item_id", sa.Uuid),
    sa.Column("reservation_expires_at", sa.DateTime(timezone=True)),
    sa.Column("allocated_item_id", sa.Uuid),
    sa.Column("allocated_at", sa.DateTime(timezone=True)),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.UniqueConstraint("product_id", "fingerprint", name="uq_inventory_product_fingerprint"),
    sa.UniqueConstraint("key_version", "nonce", name="uq_inventory_key_nonce"),
    sa.UniqueConstraint("id", "product_id", name="uq_inventory_unit_product"),
    sa.UniqueConstraint("reservation_item_id", name="uq_inventory_reservation"),
    sa.UniqueConstraint("allocated_item_id", name="uq_inventory_allocation"),
    sa.ForeignKeyConstraint(
        ["allocated_item_id", "id"],
        ["order_items.id", "order_items.inventory_unit_id"],
        name="fk_inventory_allocation_item",
        use_alter=True,
    ),
    sa.ForeignKeyConstraint(
        ["reservation_item_id", "id"],
        ["order_items.id", "order_items.inventory_unit_id"],
        name="fk_inventory_reservation_item",
        use_alter=True,
    ),
    sa.CheckConstraint("product_type IN ('UNIQUE_KEY', 'LICENSE')", name="ck_inventory_type"),
    sa.CheckConstraint("octet_length(fingerprint) = 32", name="ck_inventory_fingerprint"),
    sa.CheckConstraint("key_version > 0", name="ck_inventory_key_version"),
    sa.CheckConstraint("octet_length(nonce) = 12", name="ck_inventory_nonce"),
    sa.CheckConstraint(
        "octet_length(ciphertext) BETWEEN 17 AND 8320", name="ck_inventory_ciphertext"
    ),
    sa.CheckConstraint(
        "status IN ('AVAILABLE', 'RESERVED', 'RETIRED', 'ALLOCATED')", name="ck_inventory_status"
    ),
    sa.CheckConstraint(
        "(status = 'RESERVED' AND reservation_item_id IS NOT NULL "
        "AND reservation_expires_at IS NOT NULL) OR "
        "(status <> 'RESERVED' AND reservation_item_id IS NULL AND reservation_expires_at IS NULL)",
        name="ck_inventory_reservation",
    ),
    sa.CheckConstraint(
        "(status = 'ALLOCATED' AND allocated_item_id IS NOT NULL AND allocated_at IS NOT NULL) OR "
        "(status <> 'ALLOCATED' AND allocated_item_id IS NULL AND allocated_at IS NULL)",
        name="ck_inventory_allocation",
    ),
)
sa.Index(
    "ix_inventory_product_status_id",
    inventory_units.c.product_id,
    inventory_units.c.status,
    inventory_units.c.id,
)
inventory_actions = sa.Table(
    "inventory_actions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "actor_id",
        sa.Uuid,
        sa.ForeignKey("users.id", name="fk_inventory_actions_actor"),
        nullable=False,
    ),
    sa.Column(
        "product_id",
        sa.Uuid,
        sa.ForeignKey("products.id", name="fk_inventory_actions_product"),
        nullable=False,
    ),
    sa.Column("unit_id", sa.Uuid),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("envelopes", JSONB(none_as_null=True)),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(
        ["unit_id", "product_id"],
        ["inventory_units.id", "inventory_units.product_id"],
        name="fk_inventory_actions_unit_product",
    ),
    sa.CheckConstraint("quantity BETWEEN 1 AND 1000", name="ck_inventory_actions_quantity"),
    sa.CheckConstraint(
        "status IN ('pending', 'applied', 'cancelled')", name="ck_inventory_actions_status"
    ),
    sa.CheckConstraint(
        "(status = 'pending') = (completed_at IS NULL)", name="ck_inventory_actions_completion"
    ),
    sa.CheckConstraint(
        "(kind = 'inventory.import' AND unit_id IS NULL) OR "
        "(kind = 'inventory.retire' AND unit_id IS NOT NULL AND quantity = 1)",
        name="ck_inventory_actions_shape",
    ),
    sa.CheckConstraint(
        "COALESCE(CASE WHEN kind = 'inventory.import' AND status = 'pending' THEN "
        "CASE WHEN jsonb_typeof(envelopes) = 'array' THEN "
        "jsonb_array_length(envelopes) = quantity ELSE FALSE END "
        "ELSE envelopes IS NULL END, FALSE)",
        name="ck_inventory_actions_envelopes",
    ),
)
