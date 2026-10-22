import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from telebotshop.infrastructure.database.models.fulfillment import metadata

resource_versions = sa.Table(
    "resource_versions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "product_id",
        sa.Uuid,
        sa.ForeignKey("products.id", name="fk_resource_product"),
        nullable=False,
    ),
    sa.Column("sku", sa.String(32), nullable=False),
    sa.Column("product_type", sa.String(32), nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    sa.Column(
        "actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_resource_actor"), nullable=False
    ),
    sa.Column("product_version", sa.Integer, nullable=False),
    sa.Column(
        "expected_resource_id",
        sa.Uuid,
        sa.ForeignKey("resource_versions.id", name="fk_resource_previous"),
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
    sa.Column("key_version", sa.Integer, nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.CheckConstraint(
        "product_type IN ('DIGITAL_FILE', 'REUSABLE_RESOURCE', 'MANUAL_FULFILLMENT')",
        name="ck_resource_type",
    ),
    sa.CheckConstraint(
        "state IN ('PENDING', 'ACTIVE', 'RETIRED', 'CANCELLED')", name="ck_resource_state"
    ),
    sa.CheckConstraint("product_version >= 0 AND key_version > 0", name="ck_resource_versions"),
    sa.CheckConstraint(
        "octet_length(nonce) = 12 AND octet_length(ciphertext) BETWEEN 17 AND 8320",
        name="ck_resource_envelope",
    ),
    sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_resource_metadata"),
    sa.UniqueConstraint("key_version", "nonce", name="uq_resource_nonce"),
)
sa.Index(
    "uq_resource_active",
    resource_versions.c.product_id,
    unique=True,
    postgresql_where=sa.text("state = 'ACTIVE'"),
)
sa.Index("ix_resource_product", resource_versions.c.product_id, resource_versions.c.id)

order_resources = sa.Table(
    "order_resources",
    metadata,
    sa.Column(
        "order_item_id",
        sa.Uuid,
        sa.ForeignKey("order_items.id", name="fk_order_resource_item"),
        primary_key=True,
    ),
    sa.Column(
        "resource_id",
        sa.Uuid,
        sa.ForeignKey("resource_versions.id", name="fk_order_resource_version"),
        nullable=False,
    ),
    sa.UniqueConstraint("order_item_id", "resource_id", name="uq_order_resource_binding"),
)
