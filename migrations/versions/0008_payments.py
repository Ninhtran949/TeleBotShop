"""Append-only receipts, permanent allocation and atomic fulfillment intents."""

from alembic import op

revision = "0008_payments"
down_revision = "0007_pre_checkout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE orders ADD COLUMN attention_reason VARCHAR(32), DROP CONSTRAINT
    ck_orders_status, DROP CONSTRAINT ck_orders_close""")
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK (status IN
    ('AWAITING_PAYMENT', 'EXPIRED', 'PAID'))""")
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_close CHECK ((status IN
    ('AWAITING_PAYMENT', 'PAID') AND closed_at IS NULL AND close_reason IS NULL)
    OR (status IN ('EXPIRED', 'PAID') AND closed_at IS NOT NULL AND close_reason
    IS NOT NULL AND close_reason IN ('CUSTOMER_CANCELLED', 'TTL_EXPIRED',
    'INVOICE_REJECTED')))""")
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_attention CHECK (attention_reason IS
    NULL OR (status = 'PAID' AND attention_reason = 'RESERVATION_LOST'))""")
    op.execute("""ALTER TABLE inventory_units ADD COLUMN allocated_item_id UUID, ADD COLUMN
    allocated_at TIMESTAMP WITH TIME ZONE, DROP CONSTRAINT ck_inventory_status""")
    op.execute("""ALTER TABLE inventory_units ADD CONSTRAINT ck_inventory_status CHECK (status IN
    ('AVAILABLE', 'RESERVED', 'RETIRED', 'ALLOCATED'))""")
    op.execute("""ALTER TABLE inventory_units ADD CONSTRAINT uq_inventory_allocation UNIQUE
    (allocated_item_id)""")
    op.execute("""ALTER TABLE inventory_units ADD CONSTRAINT fk_inventory_allocation_item FOREIGN
    KEY(allocated_item_id, id) REFERENCES order_items (id, inventory_unit_id)""")
    op.execute("""ALTER TABLE inventory_units ADD CONSTRAINT ck_inventory_allocation CHECK ((status
    = 'ALLOCATED' AND allocated_item_id IS NOT NULL AND allocated_at IS NOT NULL)
    OR (status <> 'ALLOCATED' AND allocated_item_id IS NULL AND allocated_at IS
    NULL))""")
    op.execute("""CREATE TABLE payments (
id UUID NOT NULL,
charge_id VARCHAR(1024) NOT NULL,
payer_id BIGINT,
currency VARCHAR(16) NOT NULL,
amount BIGINT NOT NULL,
payload VARCHAR(128) NOT NULL,
provider_charge_id VARCHAR(1024) NOT NULL,
is_recurring BOOLEAN NOT NULL,
is_first_recurring BOOLEAN NOT NULL,
subscription_expiration_date BIGINT,
has_order_info BOOLEAN NOT NULL,
shipping_option_id VARCHAR(1024),
order_id UUID,
status VARCHAR(32) NOT NULL,
review_reason VARCHAR(32),
received_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_payments_charge UNIQUE (charge_id),
CONSTRAINT ck_payments_charge CHECK (octet_length(charge_id) BETWEEN 1 AND 1024),
CONSTRAINT ck_payments_payload CHECK (octet_length(payload) <= 128),
CONSTRAINT ck_payments_payer CHECK (payer_id IS NULL OR payer_id > 0),
CONSTRAINT ck_payments_state CHECK ((status = 'MATCHED' AND order_id IS NOT NULL
    AND review_reason IS NULL) OR (status = 'REQUIRES_REVIEW' AND review_reason IN
    ('UNKNOWN_ORDER', 'MISSING_PAYER', 'WRONG_BUYER', 'WRONG_AMOUNT',
    'WRONG_CURRENCY', 'EXTRA_CHARGE', 'UNSUPPORTED', 'INVALID_ORDER_STATE') AND
    review_reason IS NOT NULL)),
CONSTRAINT fk_payments_order FOREIGN KEY(order_id) REFERENCES orders (id)
)""")
    op.execute("""CREATE UNIQUE INDEX uq_payments_primary_order ON payments (order_id) WHERE status
    = 'MATCHED'""")
    op.execute("""CREATE TABLE payment_conflicts (
id UUID NOT NULL,
payment_id UUID NOT NULL,
digest BYTEA NOT NULL,
evidence JSONB NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_payment_conflict UNIQUE (payment_id, digest),
CONSTRAINT ck_conflict_digest CHECK (octet_length(digest) = 32),
CONSTRAINT ck_conflict_evidence CHECK (jsonb_typeof(evidence) = 'object'),
CONSTRAINT fk_conflicts_payment FOREIGN KEY(payment_id) REFERENCES payments (id)
)""")
    op.execute("""CREATE TABLE fulfillments (
id UUID NOT NULL,
order_id UUID NOT NULL,
order_item_id UUID NOT NULL,
payment_id UUID NOT NULL,
inventory_unit_id UUID NOT NULL,
buyer_id UUID NOT NULL,
destination_telegram_id BIGINT NOT NULL,
product_type VARCHAR(32) NOT NULL,
state VARCHAR(16) DEFAULT 'READY' NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_fulfillments_item UNIQUE (order_item_id),
CONSTRAINT fk_fulfillments_item_unit FOREIGN KEY(order_item_id, inventory_unit_id)
    REFERENCES order_items (id, inventory_unit_id),
CONSTRAINT ck_fulfillments_state CHECK (state = 'READY'),
CONSTRAINT ck_fulfillments_type CHECK (product_type IN ('UNIQUE_KEY', 'LICENSE')),
CONSTRAINT ck_fulfillments_destination CHECK (destination_telegram_id > 0),
CONSTRAINT fk_fulfillments_order FOREIGN KEY(order_id) REFERENCES orders (id),
CONSTRAINT fk_fulfillments_item FOREIGN KEY(order_item_id) REFERENCES order_items
    (id),
CONSTRAINT fk_fulfillments_payment FOREIGN KEY(payment_id) REFERENCES payments
    (id),
CONSTRAINT fk_fulfillments_unit FOREIGN KEY(inventory_unit_id) REFERENCES
    inventory_units (id),
CONSTRAINT fk_fulfillments_buyer FOREIGN KEY(buyer_id) REFERENCES users (id)
)""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_order() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
IF TG_OP = 'DELETE' THEN
RAISE EXCEPTION 'Order history cannot be deleted' USING ERRCODE = '23514';
END IF;
IF ROW(NEW.id, NEW.buyer_id, NEW.checkout_action_id, NEW.created_at)
IS DISTINCT FROM ROW(OLD.id, OLD.buyer_id, OLD.checkout_action_id, OLD.created_at)
OR (OLD.status = 'PAID' AND NEW IS DISTINCT FROM OLD)
OR (OLD.status = 'EXPIRED' AND NEW.status <> 'PAID' AND NEW IS DISTINCT FROM OLD)
OR (ROW(NEW.closed_at, NEW.close_reason) IS DISTINCT FROM ROW(OLD.closed_at,
    OLD.close_reason)
AND NOT (OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'EXPIRED'))
OR (NEW.attention_reason IS DISTINCT FROM OLD.attention_reason AND NEW.status <>
    'PAID') THEN
RAISE EXCEPTION 'Order identity and payment history are immutable' USING ERRCODE =
    '23514';
END IF;
IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
(OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'EXPIRED') OR
(OLD.status IN ('AWAITING_PAYMENT', 'EXPIRED') AND NEW.status = 'PAID' AND EXISTS
    (
SELECT 1 FROM payments p WHERE p.order_id = OLD.id AND p.status = 'MATCHED')))
    THEN
RAISE EXCEPTION 'Invalid order payment transition' USING ERRCODE = '23514';
END IF;
IF NEW.expires_at IS DISTINCT FROM OLD.expires_at AND NOT (
OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'AWAITING_PAYMENT'
AND NEW.expires_at >= OLD.expires_at AND EXISTS (
SELECT 1 FROM pre_checkout_approvals a WHERE a.order_id = OLD.id
AND NEW.expires_at = GREATEST(OLD.expires_at, a.approved_at + interval
    '15 minutes')
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
IF OLD.status = 'ALLOCATED' AND NEW IS DISTINCT FROM OLD THEN
RAISE EXCEPTION 'Allocated inventory is immutable' USING ERRCODE = '23514';
END IF;
IF NEW.status = OLD.status AND
ROW(NEW.allocated_item_id, NEW.allocated_at) IS DISTINCT FROM
ROW(OLD.allocated_item_id, OLD.allocated_at) THEN
RAISE EXCEPTION 'Allocation binding is immutable' USING ERRCODE = '23514';
END IF;
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
ELSIF OLD.status = 'RESERVED' AND NEW.status = 'ALLOCATED' THEN
IF NEW.allocated_item_id IS DISTINCT FROM OLD.reservation_item_id OR NOT EXISTS (
SELECT 1 FROM order_items i JOIN orders o ON o.id = i.order_id
JOIN payments p ON p.order_id = o.id AND p.status = 'MATCHED'
WHERE i.id = OLD.reservation_item_id AND o.status = 'PAID'
AND o.attention_reason IS NULL) THEN
RAISE EXCEPTION 'Invalid paid allocation' USING ERRCODE = '23514';
END IF;
ELSIF NOT (OLD.status = 'AVAILABLE' AND NEW.status IN ('RESERVED', 'RETIRED'))
    THEN
RAISE EXCEPTION 'Invalid inventory transition' USING ERRCODE = '23514';
END IF;
END IF;
END IF;
IF TG_OP = 'INSERT' AND NEW.status = 'ALLOCATED' THEN
RAISE EXCEPTION 'Allocation requires a reservation' USING ERRCODE = '23514';
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
    op.execute("""CREATE FUNCTION guard_payment_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
RAISE EXCEPTION 'Financial history is immutable' USING ERRCODE = '23514';
END; $$""")
    op.execute("""CREATE TRIGGER payments_immutable BEFORE UPDATE OR DELETE ON payments FOR EACH ROW
    EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE TRIGGER payments_truncate BEFORE TRUNCATE ON payments FOR EACH STATEMENT
    EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE TRIGGER payment_conflicts_immutable BEFORE UPDATE OR DELETE ON
    payment_conflicts FOR EACH ROW EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE TRIGGER payment_conflicts_truncate BEFORE TRUNCATE ON payment_conflicts FOR
    EACH STATEMENT EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE TRIGGER fulfillments_immutable BEFORE UPDATE OR DELETE ON fulfillments FOR
    EACH ROW EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE TRIGGER fulfillments_truncate BEFORE TRUNCATE ON fulfillments FOR EACH
    STATEMENT EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE FUNCTION guard_fulfillment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
IF NOT EXISTS (
SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.id
JOIN payments p ON p.order_id = o.id JOIN inventory_units u ON u.id =
    i.inventory_unit_id
JOIN users b ON b.id = o.buyer_id
WHERE o.id = NEW.order_id AND o.status = 'PAID' AND o.attention_reason IS NULL
AND i.id = NEW.order_item_id AND i.inventory_unit_id = NEW.inventory_unit_id
AND p.id = NEW.payment_id AND p.status = 'MATCHED'
AND u.status = 'ALLOCATED' AND u.allocated_item_id = i.id
AND NEW.buyer_id = b.id AND NEW.destination_telegram_id = b.telegram_id
AND NEW.product_type = i.product_type) THEN
RAISE EXCEPTION 'Fulfillment ownership mismatch' USING ERRCODE = '23514';
END IF;
RETURN NEW;
END; $$""")
    op.execute("""CREATE TRIGGER fulfillment_guard BEFORE INSERT ON fulfillments FOR EACH ROW
    EXECUTE FUNCTION guard_fulfillment()""")
    op.execute("""CREATE FUNCTION check_committed_payment() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
IF NEW.status = 'MATCHED' AND NOT EXISTS (
SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.id
JOIN invoice_intents v ON v.order_id = o.id JOIN users b ON b.id = o.buyer_id
WHERE o.id = NEW.order_id AND o.status = 'PAID' AND b.telegram_id = NEW.payer_id
AND v.payload = NEW.payload AND NEW.currency = 'XTR' AND NEW.amount =
    i.price_stars * i.quantity
AND NOT NEW.is_recurring AND NOT NEW.is_first_recurring AND NOT NEW.has_order_info
AND NEW.subscription_expiration_date IS NULL AND NEW.shipping_option_id IS NULL
AND (
(o.attention_reason = 'RESERVATION_LOST' AND NOT EXISTS (
SELECT 1 FROM fulfillments f WHERE f.payment_id = NEW.id)) OR
(o.attention_reason IS NULL AND EXISTS (
SELECT 1 FROM fulfillments f JOIN inventory_units u ON u.id = f.inventory_unit_id
WHERE f.payment_id = NEW.id AND f.order_id = o.id AND f.order_item_id = i.id
AND u.status = 'ALLOCATED' AND u.allocated_item_id = i.id)))) THEN
RAISE EXCEPTION 'Payment requires atomic paid allocation or attention' USING
    ERRCODE = '23514';
END IF;
RETURN NEW;
END; $$""")
    op.execute("""CREATE CONSTRAINT TRIGGER committed_payment AFTER INSERT ON payments DEFERRABLE
    INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_committed_payment()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
IF EXISTS (SELECT 1 FROM payments) OR EXISTS (SELECT 1 FROM payment_conflicts)
OR EXISTS (SELECT 1 FROM fulfillments) OR EXISTS (SELECT 1 FROM orders WHERE
    status = 'PAID')
OR EXISTS (SELECT 1 FROM inventory_units WHERE status = 'ALLOCATED') THEN
RAISE EXCEPTION
    'Payment history requires an explicit retention/migration decision';
END IF;
END; $$""")
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
ELSIF NOT (OLD.status = 'AVAILABLE' AND NEW.status IN ('RESERVED', 'RETIRED'))
    THEN
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
    op.drop_table("fulfillments")
    op.drop_table("payment_conflicts")
    op.drop_table("payments")
    op.execute("""DROP FUNCTION guard_payment_history()""")
    op.execute("""DROP FUNCTION guard_fulfillment()""")
    op.execute("""DROP FUNCTION check_committed_payment()""")
    op.execute("""ALTER TABLE inventory_units DROP CONSTRAINT fk_inventory_allocation_item, DROP
    CONSTRAINT uq_inventory_allocation, DROP CONSTRAINT ck_inventory_allocation,
    DROP CONSTRAINT ck_inventory_status, DROP COLUMN allocated_item_id, DROP
    COLUMN allocated_at""")
    op.execute("""ALTER TABLE inventory_units ADD CONSTRAINT ck_inventory_status CHECK (status IN
    ('AVAILABLE', 'RESERVED', 'RETIRED'))""")
    op.execute("""ALTER TABLE orders DROP CONSTRAINT ck_orders_attention, DROP CONSTRAINT
    ck_orders_status, DROP CONSTRAINT ck_orders_close, DROP COLUMN
    attention_reason""")
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK (status IN
    ('AWAITING_PAYMENT', 'EXPIRED'))""")
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_close CHECK (
(status = 'AWAITING_PAYMENT' AND closed_at IS NULL AND close_reason IS NULL) OR
(status = 'EXPIRED' AND closed_at IS NOT NULL AND close_reason IS NOT NULL AND
close_reason IN ('CUSTOMER_CANCELLED', 'TTL_EXPIRED', 'INVOICE_REJECTED')))""")
