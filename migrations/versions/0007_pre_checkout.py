"""Durable serialized pre-checkout approval and one-time reservation extension."""

from alembic import op

revision = "0007_pre_checkout"
down_revision = "0006_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE pre_checkout_approvals (
    order_id UUID NOT NULL,
    query_id VARCHAR(256) NOT NULL,
    approved_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (order_id),
    CONSTRAINT uq_pre_checkout_query UNIQUE (query_id),
    CONSTRAINT ck_pre_checkout_query CHECK (length(btrim(query_id)) > 0 AND query_id !~
        '[[:cntrl:]]'),
    CONSTRAINT fk_pre_checkout_order FOREIGN KEY(order_id) REFERENCES orders (id)
)""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_order() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Order history cannot be deleted' USING ERRCODE = '23514';
      END IF;
      IF ROW(NEW.id, NEW.buyer_id, NEW.checkout_action_id, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.id, OLD.buyer_id, OLD.checkout_action_id, OLD.created_at)
         OR (OLD.status = 'EXPIRED' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Order identity and closed state are immutable' USING ERRCODE =
        '23514';
      END IF;
      IF NEW.expires_at IS DISTINCT FROM OLD.expires_at AND NOT (
          OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'AWAITING_PAYMENT'
          AND NEW.expires_at >= OLD.expires_at AND EXISTS (
            SELECT 1 FROM pre_checkout_approvals a WHERE a.order_id = OLD.id
              AND NEW.expires_at =
                GREATEST(OLD.expires_at, a.approved_at + interval '15 minutes')
          )) THEN
        RAISE EXCEPTION 'Invalid approval extension' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END; $$""")
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
          RAISE EXCEPTION 'Inventory identity and payload are immutable' USING ERRCODE =
        '23514';
        END IF;
        IF NEW.status = OLD.status AND
           ROW(NEW.reservation_item_id, NEW.reservation_expires_at) IS DISTINCT FROM
           ROW(OLD.reservation_item_id, OLD.reservation_expires_at) AND NOT (
             OLD.status = 'RESERVED'
             AND NEW.reservation_item_id IS NOT DISTINCT FROM OLD.reservation_item_id
             AND NEW.reservation_expires_at >= OLD.reservation_expires_at) THEN
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
    op.execute("""CREATE FUNCTION guard_pre_checkout_approval() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      RAISE EXCEPTION 'Pre-checkout approvals are immutable' USING ERRCODE = '23514';
    END; $$""")
    op.execute("""CREATE TRIGGER pre_checkout_approval_guard BEFORE UPDATE OR DELETE
    ON pre_checkout_approvals FOR EACH ROW EXECUTE FUNCTION guard_pre_checkout_approval()""")
    op.execute("""CREATE TRIGGER pre_checkout_approval_truncate BEFORE TRUNCATE
    ON pre_checkout_approvals FOR EACH STATEMENT EXECUTE FUNCTION guard_pre_checkout_approval()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pre_checkout_approvals) THEN
        RAISE EXCEPTION 'Approval history requires an explicit migration decision';
      END IF;
    END; $$""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_order() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Order history cannot be deleted' USING ERRCODE = '23514';
      END IF;
      IF ROW(NEW.id, NEW.buyer_id, NEW.checkout_action_id, NEW.created_at, NEW.expires_at)
         IS DISTINCT FROM
         ROW(OLD.id, OLD.buyer_id, OLD.checkout_action_id, OLD.created_at, OLD.expires_at)
         OR (OLD.status = 'EXPIRED' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Order identity and closed state are immutable' USING ERRCODE =
        '23514';
      END IF;
      RETURN NEW;
    END; $$""")
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
          RAISE EXCEPTION 'Inventory identity and payload are immutable' USING ERRCODE =
        '23514';
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
    op.drop_table("pre_checkout_approvals")
    op.execute("DROP FUNCTION guard_pre_checkout_approval()")
