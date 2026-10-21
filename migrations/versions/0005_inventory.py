"""Encrypted finite inventory and durable import/retire confirmations."""

from alembic import op

revision = "0005_inventory"
down_revision = "0004_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE inventory_crypto_state (
    id SERIAL NOT NULL,
    dedup_verifier BYTEA NOT NULL,
    key_verifiers JSONB NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_inventory_crypto_singleton CHECK (id = 1),
    CONSTRAINT ck_inventory_crypto_dedup CHECK (octet_length(dedup_verifier) = 32),
    CONSTRAINT ck_inventory_crypto_keys CHECK (jsonb_typeof(key_verifiers) = 'object')
)""")
    op.execute("""CREATE TABLE inventory_units (
    id UUID NOT NULL,
    product_id UUID NOT NULL,
    product_type VARCHAR(32) NOT NULL,
    fingerprint BYTEA NOT NULL,
    key_version INTEGER NOT NULL,
    nonce BYTEA NOT NULL,
    ciphertext BYTEA NOT NULL,
    status VARCHAR(16) DEFAULT 'AVAILABLE' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_inventory_product_fingerprint UNIQUE (product_id, fingerprint),
    CONSTRAINT uq_inventory_key_nonce UNIQUE (key_version, nonce),
    CONSTRAINT uq_inventory_unit_product UNIQUE (id, product_id),
    CONSTRAINT ck_inventory_type CHECK (product_type IN ('UNIQUE_KEY', 'LICENSE')),
    CONSTRAINT ck_inventory_fingerprint CHECK (octet_length(fingerprint) = 32),
    CONSTRAINT ck_inventory_key_version CHECK (key_version > 0),
    CONSTRAINT ck_inventory_nonce CHECK (octet_length(nonce) = 12),
    CONSTRAINT ck_inventory_ciphertext CHECK (octet_length(ciphertext) BETWEEN 17 AND 8320),
    CONSTRAINT ck_inventory_status CHECK (status IN ('AVAILABLE', 'RETIRED')),
    CONSTRAINT fk_inventory_units_product FOREIGN KEY(product_id) REFERENCES products (id)
)""")
    op.execute(
        "CREATE INDEX ix_inventory_product_status_id ON inventory_units (product_id, status, id)"
    )
    op.execute("""CREATE TABLE inventory_actions (
    id UUID NOT NULL,
    actor_id UUID NOT NULL,
    product_id UUID NOT NULL,
    unit_id UUID,
    kind VARCHAR(32) NOT NULL,
    quantity INTEGER NOT NULL,
    envelopes JSONB,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(16) DEFAULT 'pending' NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT fk_inventory_actions_unit_product FOREIGN KEY(unit_id, product_id) REFERENCES
        inventory_units (id, product_id),
    CONSTRAINT ck_inventory_actions_quantity CHECK (quantity BETWEEN 1 AND 1000),
    CONSTRAINT ck_inventory_actions_status CHECK (status IN ('pending', 'applied',
        'cancelled')),
    CONSTRAINT ck_inventory_actions_completion CHECK ((status = 'pending') = (completed_at IS
        NULL)),
    CONSTRAINT ck_inventory_actions_shape CHECK ((kind = 'inventory.import' AND unit_id IS NULL)
        OR (kind = 'inventory.retire' AND unit_id IS NOT NULL AND quantity = 1)),
    CONSTRAINT ck_inventory_actions_envelopes CHECK (COALESCE(CASE WHEN kind =
        'inventory.import' AND status = 'pending' THEN CASE WHEN jsonb_typeof(envelopes) =
        'array' THEN jsonb_array_length(envelopes) = quantity ELSE FALSE END ELSE envelopes IS
        NULL END, FALSE)),
    CONSTRAINT fk_inventory_actions_actor FOREIGN KEY(actor_id) REFERENCES users (id),
    CONSTRAINT fk_inventory_actions_product FOREIGN KEY(product_id) REFERENCES products (id)
)""")
    op.execute("""CREATE FUNCTION guard_inventory_unit() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Inventory units must be retired' USING ERRCODE = '23514';
      END IF;
      IF TG_OP = 'UPDATE' THEN
        IF ROW(NEW.id, NEW.product_id, NEW.product_type, NEW.fingerprint,
               NEW.key_version, NEW.nonce, NEW.ciphertext, NEW.created_at)
           IS DISTINCT FROM
           ROW(OLD.id, OLD.product_id, OLD.product_type, OLD.fingerprint,
               OLD.key_version, OLD.nonce, OLD.ciphertext, OLD.created_at) THEN
          RAISE EXCEPTION 'Inventory identity and payload are immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status AND
           NOT (OLD.status = 'AVAILABLE' AND NEW.status = 'RETIRED') THEN
          RAISE EXCEPTION 'Invalid inventory transition' USING ERRCODE = '23514';
        END IF;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM products
                     WHERE id = NEW.product_id AND product_type = NEW.product_type) THEN
        RAISE EXCEPTION 'Inventory product type mismatch' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER inventory_unit_guard BEFORE INSERT OR UPDATE OR DELETE
    ON inventory_units FOR EACH ROW EXECUTE FUNCTION guard_inventory_unit()""")


def downgrade() -> None:
    op.drop_table("inventory_actions")
    op.drop_table("inventory_units")
    op.drop_table("inventory_crypto_state")
    op.execute("DROP FUNCTION guard_inventory_unit()")
