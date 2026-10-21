"""Public catalog, immutable SKU/type and durable catalog confirmations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_catalog"
down_revision = "0003_access"
branch_labels = None
depends_on = None

PUBLIC_METADATA_CHECK = """
jsonb_typeof(public_metadata) = 'object' AND COALESCE(CASE product_type
WHEN 'UNIQUE_KEY' THEN public_metadata = '{}'::jsonb
WHEN 'LICENSE' THEN jsonb_typeof(public_metadata->'instructions') = 'string'
    AND length(btrim(public_metadata->>'instructions')) BETWEEN 1 AND 500
    AND public_metadata - 'instructions' = '{}'::jsonb
WHEN 'DIGITAL_FILE' THEN jsonb_typeof(public_metadata->'file_name') = 'string'
    AND jsonb_typeof(public_metadata->'mime_type') = 'string'
    AND length(btrim(public_metadata->>'file_name')) BETWEEN 1 AND 128
    AND position('/' in public_metadata->>'file_name') = 0
    AND position(chr(92) in public_metadata->>'file_name') = 0
    AND public_metadata->>'file_name' NOT IN ('.', '..')
    AND public_metadata->>'mime_type' ~ '^[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}$'
    AND public_metadata - ARRAY['file_name', 'mime_type'] = '{}'::jsonb
WHEN 'REUSABLE_RESOURCE' THEN public_metadata->>'format' IN ('TEXT', 'DOCUMENT')
    AND public_metadata - 'format' = '{}'::jsonb
WHEN 'MANUAL_FULFILLMENT' THEN jsonb_typeof(public_metadata->'customer_notice') = 'string'
    AND length(btrim(public_metadata->>'customer_notice')) BETWEEN 1 AND 500
    AND public_metadata - 'customer_notice' = '{}'::jsonb
ELSE FALSE END, FALSE)
"""


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column(
            "parent_id", sa.Uuid, sa.ForeignKey("categories.id", name="fk_categories_parent")
        ),
        sa.UniqueConstraint("code", name="uq_categories_code"),
        sa.CheckConstraint("code ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$'", name="ck_categories_code"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_categories_name"),
    )
    op.create_index("ix_categories_parent_code", "categories", ["parent_id", "code"])
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("sku", sa.String(32), nullable=False),
        sa.Column(
            "category_id",
            sa.Uuid,
            sa.ForeignKey("categories.id", name="fk_products_category"),
            nullable=False,
        ),
        sa.Column("product_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("price_stars", sa.BigInteger, nullable=False),
        sa.Column("public_metadata", JSONB, nullable=False),
        sa.Column("sale_state", sa.String(16), nullable=False, server_default="HIDDEN"),
        sa.Column("image_file_id", sa.String(512)),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("sku", name="uq_products_sku"),
        sa.CheckConstraint("sku ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$'", name="ck_products_sku"),
        sa.CheckConstraint(
            "product_type IN ('UNIQUE_KEY', 'LICENSE', 'DIGITAL_FILE', "
            "'REUSABLE_RESOURCE', 'MANUAL_FULFILLMENT')",
            name="ck_products_type",
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_products_name"),
        sa.CheckConstraint("length(description) <= 1200", name="ck_products_description"),
        sa.CheckConstraint("price_stars > 0", name="ck_products_price"),
        sa.CheckConstraint(PUBLIC_METADATA_CHECK, name="ck_products_public_metadata"),
        sa.CheckConstraint("sale_state IN ('HIDDEN', 'ACTIVE')", name="ck_products_sale_state"),
        sa.CheckConstraint(
            "image_file_id ~ '^[A-Za-z0-9_-]+$' AND length(image_file_id) BETWEEN 16 AND 512",
            name="ck_products_image",
        ),
        sa.CheckConstraint("version >= 0", name="ck_products_version"),
    )
    op.create_index("ix_products_state_sku", "products", ["sale_state", "sku"])
    op.create_index("ix_products_category_sku", "products", ["category_id", "sku"])
    op.create_table(
        "catalog_actions",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "actor_id",
            sa.Uuid,
            sa.ForeignKey("users.id", name="fk_catalog_actions_actor"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("expected_version", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind IN ('category.create', 'product.create', 'product.edit', "
            "'product.price', 'product.sale', 'product.image')",
            name="ck_catalog_actions_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 16384",
            name="ck_catalog_actions_payload",
        ),
        sa.CheckConstraint("expected_version >= 0", name="ck_catalog_actions_version"),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'cancelled')", name="ck_catalog_actions_status"
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (completed_at IS NULL)", name="ck_catalog_actions_completion"
        ),
    )
    op.execute("""
        CREATE FUNCTION enforce_category_parent() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND (NEW.code IS DISTINCT FROM OLD.code
                OR NEW.parent_id IS DISTINCT FROM OLD.parent_id) THEN
                RAISE EXCEPTION 'Category identity is immutable' USING ERRCODE = '23514';
            END IF;
            IF NEW.parent_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM categories WHERE id = NEW.parent_id AND parent_id IS NULL
            ) THEN RAISE EXCEPTION 'Parent must be a root category' USING ERRCODE = '23514'; END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""CREATE TRIGGER category_parent_guard BEFORE INSERT OR UPDATE ON categories
                  FOR EACH ROW EXECUTE FUNCTION enforce_category_parent()""")
    op.execute("""
        CREATE FUNCTION keep_product_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.sku IS DISTINCT FROM OLD.sku
                OR NEW.product_type IS DISTINCT FROM OLD.product_type
            THEN RAISE EXCEPTION 'Product identity is immutable' USING ERRCODE = '23514'; END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("""CREATE TRIGGER product_identity_guard BEFORE UPDATE ON products
                  FOR EACH ROW EXECUTE FUNCTION keep_product_identity()""")


def downgrade() -> None:
    op.drop_table("catalog_actions")
    op.drop_table("products")
    op.drop_table("categories")
    op.execute("DROP FUNCTION keep_product_identity()")
    op.execute("DROP FUNCTION enforce_category_parent()")
