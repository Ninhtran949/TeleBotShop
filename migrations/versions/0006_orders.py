"""Immutable order snapshots, finite reservations and durable Stars invoice intents."""

from alembic import op

revision = "0006_orders"
down_revision = "0005_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE orders (
    id UUID NOT NULL,
    buyer_id UUID NOT NULL,
    checkout_action_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    closed_at TIMESTAMP WITH TIME ZONE,
    close_reason VARCHAR(32),
    PRIMARY KEY (id),
    CONSTRAINT uq_orders_buyer_action UNIQUE (buyer_id, checkout_action_id),
    CONSTRAINT ck_orders_status CHECK (status IN ('AWAITING_PAYMENT', 'EXPIRED')),
    CONSTRAINT ck_orders_ttl CHECK (expires_at >= created_at + interval '15 minutes'),
    CONSTRAINT ck_orders_close CHECK ((status = 'AWAITING_PAYMENT' AND closed_at IS NULL AND
        close_reason IS NULL) OR (status = 'EXPIRED' AND closed_at IS NOT NULL AND close_reason IS
        NOT NULL AND close_reason IN ('CUSTOMER_CANCELLED', 'TTL_EXPIRED', 'INVOICE_REJECTED'))),
    CONSTRAINT fk_orders_buyer FOREIGN KEY(buyer_id) REFERENCES users (id)
)""")
    op.execute(
        """CREATE INDEX ix_orders_expiry ON orders (expires_at, id) WHERE status =
        'AWAITING_PAYMENT'"""
    )
    op.execute("""CREATE TABLE order_items (
    id UUID NOT NULL,
    order_id UUID NOT NULL,
    product_id UUID NOT NULL,
    inventory_unit_id UUID NOT NULL,
    sku VARCHAR(32) NOT NULL,
    product_type VARCHAR(32) NOT NULL,
    title VARCHAR(80) NOT NULL,
    description VARCHAR(1200) NOT NULL,
    price_stars BIGINT NOT NULL,
    quantity INTEGER NOT NULL,
    currency VARCHAR(3) DEFAULT 'XTR' NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_order_items_order UNIQUE (order_id),
    CONSTRAINT uq_order_item_unit UNIQUE (id, inventory_unit_id),
    CONSTRAINT fk_order_items_unit_product FOREIGN KEY(inventory_unit_id, product_id) REFERENCES
        inventory_units (id, product_id),
    CONSTRAINT ck_order_items_quantity CHECK (quantity = 1),
    CONSTRAINT ck_order_items_price CHECK (currency = 'XTR' AND price_stars > 0),
    CONSTRAINT ck_order_items_type CHECK (product_type IN ('UNIQUE_KEY', 'LICENSE')),
    CONSTRAINT ck_order_items_title CHECK (length(title) BETWEEN 1 AND 80),
    CONSTRAINT fk_order_items_order FOREIGN KEY(order_id) REFERENCES orders (id),
    CONSTRAINT fk_order_items_product FOREIGN KEY(product_id) REFERENCES products (id)
)""")
    op.execute("""CREATE TABLE invoice_intents (
    order_id UUID NOT NULL,
    payload VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL,
    attempt_id UUID,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    message_id BIGINT,
    PRIMARY KEY (order_id),
    CONSTRAINT uq_invoice_payload UNIQUE (payload),
    CONSTRAINT uq_invoice_attempt UNIQUE (attempt_id),
    CONSTRAINT ck_invoice_payload CHECK (payload ~ '^[A-Za-z0-9_-]{32}$'),
    CONSTRAINT ck_invoice_state CHECK ((status IN ('READY', 'CANCELLED') AND attempt_id IS NULL
        AND started_at IS NULL AND completed_at IS NULL AND message_id IS NULL) OR (status =
        'SENDING' AND attempt_id IS NOT NULL AND started_at IS NOT NULL AND completed_at IS NULL
        AND message_id IS NULL) OR (status IN ('SENT', 'REJECTED', 'UNCERTAIN') AND attempt_id IS
        NOT NULL AND started_at IS NOT NULL AND completed_at IS NOT NULL AND ((status = 'SENT' AND
        message_id IS NOT NULL AND message_id > 0) OR (status <> 'SENT' AND message_id IS
        NULL)))),
    CONSTRAINT fk_invoice_order FOREIGN KEY(order_id) REFERENCES orders (id)
)""")
    op.execute(
        """ALTER TABLE inventory_units ADD COLUMN reservation_item_id UUID, ADD COLUMN
        reservation_expires_at TIMESTAMP WITH TIME ZONE, DROP CONSTRAINT ck_inventory_status"""
    )
    op.execute(
        """ALTER TABLE inventory_units ADD CONSTRAINT ck_inventory_reservation CHECK ((status =
        'RESERVED' AND reservation_item_id IS NOT NULL AND reservation_expires_at IS NOT NULL) OR
        (status <> 'RESERVED' AND reservation_item_id IS NULL AND reservation_expires_at IS
        NULL))"""
    )
    op.execute(
        """ALTER TABLE inventory_units ADD CONSTRAINT ck_inventory_status CHECK (status IN
        ('AVAILABLE', 'RESERVED', 'RETIRED'))"""
    )
    op.execute(
        """ALTER TABLE inventory_units ADD CONSTRAINT fk_inventory_reservation_item FOREIGN
        KEY(reservation_item_id, id) REFERENCES order_items (id, inventory_unit_id)"""
    )
    op.execute(
        """ALTER TABLE inventory_units ADD CONSTRAINT uq_inventory_reservation UNIQUE
        (reservation_item_id)"""
    )
    op.execute("""CREATE INDEX ix_invoice_stale ON invoice_intents (started_at, order_id)
        WHERE status = 'SENDING'""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_inventory_unit() RETURNS trigger
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
        IF NEW.status = OLD.status AND
           ROW(NEW.reservation_item_id, NEW.reservation_expires_at) IS DISTINCT FROM
           ROW(OLD.reservation_item_id, OLD.reservation_expires_at) THEN
          RAISE EXCEPTION 'Reservation binding is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status THEN
          IF OLD.status = 'RESERVED' AND NEW.status = 'AVAILABLE' THEN
            IF NOT EXISTS (SELECT 1 FROM order_items i JOIN orders o ON o.id = i.order_id
                WHERE i.id = OLD.reservation_item_id AND o.status = 'EXPIRED') THEN
              RAISE EXCEPTION 'Only closed unpaid reservations can be released'
                USING ERRCODE = '23514';
            END IF;
          ELSIF NOT (OLD.status = 'AVAILABLE' AND NEW.status IN ('RESERVED', 'RETIRED')) THEN
            RAISE EXCEPTION 'Invalid inventory transition' USING ERRCODE = '23514';
          END IF;
        END IF;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM products
                     WHERE id = NEW.product_id AND product_type = NEW.product_type) THEN
        RAISE EXCEPTION 'Inventory product type mismatch' USING ERRCODE = '23514';
      END IF;
      IF NEW.status = 'RESERVED' AND NOT EXISTS (
          SELECT 1 FROM order_items i JOIN orders o ON o.id = i.order_id
          WHERE i.id = NEW.reservation_item_id AND i.inventory_unit_id = NEW.id
            AND i.product_id = NEW.product_id AND o.status = 'AWAITING_PAYMENT'
            AND o.expires_at = NEW.reservation_expires_at) THEN
        RAISE EXCEPTION 'Invalid reservation owner' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute("""CREATE FUNCTION guard_order() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Order history cannot be deleted' USING ERRCODE = '23514';
      END IF;
      IF ROW(NEW.id, NEW.buyer_id, NEW.checkout_action_id, NEW.created_at, NEW.expires_at)
         IS DISTINCT FROM
         ROW(OLD.id, OLD.buyer_id, OLD.checkout_action_id, OLD.created_at, OLD.expires_at)
         OR (OLD.status = 'EXPIRED' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Order identity and closed state are immutable' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute(
        """CREATE TRIGGER order_guard BEFORE UPDATE OR DELETE ON orders FOR EACH ROW EXECUTE
        FUNCTION guard_order()"""
    )
    op.execute("""CREATE FUNCTION guard_order_item() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION 'Order snapshots cannot be changed' USING ERRCODE = '23514';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM products WHERE id = NEW.product_id
                     AND sku = NEW.sku AND product_type = NEW.product_type) THEN
        RAISE EXCEPTION 'Order product snapshot mismatch' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute(
        """CREATE TRIGGER order_item_guard BEFORE INSERT OR UPDATE OR DELETE ON order_items FOR
        EACH ROW EXECUTE FUNCTION guard_order_item()"""
    )
    op.execute("""CREATE FUNCTION guard_invoice_intent() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Invoice history cannot be deleted' USING ERRCODE = '23514';
      END IF;
      IF ROW(NEW.order_id, NEW.payload) IS DISTINCT FROM ROW(OLD.order_id, OLD.payload)
         OR (OLD.attempt_id IS NOT NULL AND ROW(NEW.attempt_id, NEW.started_at)
             IS DISTINCT FROM ROW(OLD.attempt_id, OLD.started_at))
         OR (NEW.status IS DISTINCT FROM OLD.status AND NOT (
             (OLD.status = 'READY' AND NEW.status IN ('SENDING', 'CANCELLED')) OR
             (OLD.status = 'SENDING' AND NEW.status IN ('SENT', 'REJECTED', 'UNCERTAIN')) OR
             (OLD.status = 'UNCERTAIN' AND NEW.status IN ('SENT', 'REJECTED'))))
         OR (OLD.status IN ('SENT', 'REJECTED', 'CANCELLED') AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Invalid invoice transition' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute(
        """CREATE TRIGGER invoice_intent_guard BEFORE UPDATE OR DELETE ON invoice_intents FOR EACH
        ROW EXECUTE FUNCTION guard_invoice_intent()"""
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM orders) THEN
        RAISE EXCEPTION 'Order history requires an explicit retention/migration decision';
      END IF;
    END; $$""")
    op.execute("ALTER TABLE inventory_units DROP CONSTRAINT fk_inventory_reservation_item")
    op.execute("ALTER TABLE inventory_units DROP CONSTRAINT ck_inventory_reservation")
    op.execute("ALTER TABLE inventory_units DROP CONSTRAINT uq_inventory_reservation")
    op.execute("ALTER TABLE inventory_units DROP CONSTRAINT ck_inventory_status")
    op.execute("""ALTER TABLE inventory_units
        ADD CONSTRAINT ck_inventory_status CHECK (status IN ('AVAILABLE', 'RETIRED')),
        DROP COLUMN reservation_item_id, DROP COLUMN reservation_expires_at""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_inventory_unit() RETURNS trigger
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
    op.drop_table("invoice_intents")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.execute("DROP FUNCTION guard_order()")
    op.execute("DROP FUNCTION guard_order_item()")
    op.execute("DROP FUNCTION guard_invoice_intent()")
