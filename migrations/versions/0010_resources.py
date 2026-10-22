"""Immutable encrypted resource versions and nonfinite paid entitlements."""

from alembic import op

revision = "0010_resources"
down_revision = "0009_fulfillment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE resource_versions (
        id UUID PRIMARY KEY,
        product_id UUID NOT NULL CONSTRAINT fk_resource_product REFERENCES products(id),
        sku VARCHAR(32) NOT NULL,
        product_type VARCHAR(32) NOT NULL,
        metadata JSONB NOT NULL,
        actor_id UUID NOT NULL CONSTRAINT fk_resource_actor REFERENCES users(id),
        product_version INTEGER NOT NULL,
        expected_resource_id UUID CONSTRAINT fk_resource_previous REFERENCES resource_versions(id),
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        state VARCHAR(16) NOT NULL DEFAULT 'PENDING',
        key_version INTEGER NOT NULL,
        nonce BYTEA NOT NULL,
        ciphertext BYTEA NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_resource_type CHECK (product_type IN
            ('DIGITAL_FILE', 'REUSABLE_RESOURCE', 'MANUAL_FULFILLMENT')),
        CONSTRAINT ck_resource_state CHECK (state IN ('PENDING','ACTIVE','RETIRED','CANCELLED')),
        CONSTRAINT ck_resource_versions CHECK (product_version >= 0 AND key_version > 0),
        CONSTRAINT ck_resource_envelope CHECK (octet_length(nonce) = 12 AND
            octet_length(ciphertext) BETWEEN 17 AND 8320),
        CONSTRAINT ck_resource_metadata CHECK (jsonb_typeof(metadata) = 'object'),
        CONSTRAINT uq_resource_nonce UNIQUE (key_version, nonce)
    )""")
    op.execute(
        "CREATE UNIQUE INDEX uq_resource_active ON resource_versions(product_id) "
        "WHERE state = 'ACTIVE'"
    )
    op.execute("CREATE INDEX ix_resource_product ON resource_versions(product_id, id)")
    op.execute("""CREATE TABLE order_resources (
        order_item_id UUID PRIMARY KEY CONSTRAINT fk_order_resource_item REFERENCES order_items(id),
        resource_id UUID NOT NULL CONSTRAINT fk_order_resource_version
            REFERENCES resource_versions(id),
        CONSTRAINT uq_order_resource_binding UNIQUE (order_item_id, resource_id)
    )""")
    op.execute("""ALTER TABLE order_items ALTER COLUMN inventory_unit_id DROP NOT NULL,
        DROP CONSTRAINT ck_order_items_type,
        ADD CONSTRAINT ck_order_items_type CHECK (
            (product_type IN ('UNIQUE_KEY','LICENSE') AND inventory_unit_id IS NOT NULL) OR
            (product_type IN ('DIGITAL_FILE','REUSABLE_RESOURCE','MANUAL_FULFILLMENT')
             AND inventory_unit_id IS NULL))""")
    op.execute("""ALTER TABLE orders DROP CONSTRAINT ck_orders_attention,
        ADD CONSTRAINT ck_orders_attention CHECK (attention_reason IS NULL OR (status = 'PAID'
            AND attention_reason IN ('RESERVATION_LOST','ENTITLEMENT_UNAVAILABLE')))""")
    op.execute("""ALTER TABLE fulfillments ALTER COLUMN inventory_unit_id DROP NOT NULL,
        ADD COLUMN resource_id UUID CONSTRAINT fk_fulfillments_resource
            REFERENCES resource_versions(id),
        ADD CONSTRAINT fk_fulfillments_item_resource FOREIGN KEY (order_item_id, resource_id)
            REFERENCES order_resources(order_item_id, resource_id),
        DROP CONSTRAINT ck_fulfillments_type,
        ADD CONSTRAINT ck_fulfillments_type CHECK (
            (product_type IN ('UNIQUE_KEY','LICENSE')
             AND inventory_unit_id IS NOT NULL AND resource_id IS NULL) OR
            (product_type IN ('DIGITAL_FILE','REUSABLE_RESOURCE','MANUAL_FULFILLMENT')
             AND inventory_unit_id IS NULL AND resource_id IS NOT NULL))""")
    op.execute("""CREATE FUNCTION guard_resource_version() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Resource history is immutable' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.state <> 'PENDING' OR NOT EXISTS (SELECT 1 FROM products p
                WHERE p.id = NEW.product_id AND p.sku = NEW.sku
                  AND p.product_type = NEW.product_type AND p.version = NEW.product_version
                  AND p.public_metadata = NEW.metadata) THEN
                RAISE EXCEPTION 'Invalid resource source' USING ERRCODE = '23514';
            END IF;
        ELSIF (to_jsonb(NEW) - 'state') IS DISTINCT FROM (to_jsonb(OLD) - 'state')
           OR NOT ((OLD.state = 'PENDING' AND NEW.state IN ('ACTIVE','CANCELLED'))
               OR (OLD.state = 'ACTIVE' AND NEW.state = 'RETIRED')) THEN
            RAISE EXCEPTION 'Resource version is immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER resource_version_guard BEFORE INSERT OR UPDATE OR DELETE
        ON resource_versions FOR EACH ROW EXECUTE FUNCTION guard_resource_version()""")
    op.execute("""CREATE TRIGGER resource_truncate BEFORE TRUNCATE ON resource_versions
        FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE FUNCTION guard_order_resource() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Resource entitlement is immutable' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM order_items i JOIN orders o ON o.id = i.order_id
            JOIN products p ON p.id = i.product_id
            JOIN resource_versions r ON r.id = NEW.resource_id
            WHERE i.id = NEW.order_item_id AND i.inventory_unit_id IS NULL
              AND o.status = 'AWAITING_PAYMENT' AND r.state = 'ACTIVE'
              AND r.product_id = i.product_id AND r.product_type = i.product_type
              AND r.sku = i.sku AND r.metadata = p.public_metadata) THEN
            RAISE EXCEPTION 'Invalid resource entitlement' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER order_resource_guard BEFORE INSERT OR UPDATE OR DELETE
        ON order_resources FOR EACH ROW EXECUTE FUNCTION guard_order_resource()""")
    op.execute("""CREATE TRIGGER order_resource_truncate BEFORE TRUNCATE ON order_resources
        FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE FUNCTION check_order_resource() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.inventory_unit_id IS NULL AND NOT EXISTS (SELECT 1 FROM order_resources r
            WHERE r.order_item_id = NEW.id) THEN
            RAISE EXCEPTION 'Order requires resource snapshot' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE CONSTRAINT TRIGGER order_resource_commit AFTER INSERT ON order_items
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_order_resource()""")
    op.execute("""CREATE FUNCTION fulfillment_entitlement_matches(f fulfillments) RETURNS boolean
        LANGUAGE sql STABLE AS $$
        SELECT EXISTS (SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.id
            JOIN users b ON b.id = o.buyer_id
            JOIN payments p ON p.id = f.payment_id AND p.order_id = o.id AND p.status = 'MATCHED'
            LEFT JOIN inventory_units u ON u.id = f.inventory_unit_id
            LEFT JOIN order_resources r ON r.order_item_id = i.id AND r.resource_id = f.resource_id
            LEFT JOIN resource_versions v ON v.id = r.resource_id
            WHERE o.id = f.order_id AND i.id = f.order_item_id AND o.attention_reason IS NULL
              AND b.id = f.buyer_id AND b.telegram_id = f.destination_telegram_id
              AND i.product_type = f.product_type AND (
                (f.product_type IN ('UNIQUE_KEY','LICENSE') AND f.resource_id IS NULL
                    AND i.inventory_unit_id = u.id AND u.status = 'ALLOCATED'
                    AND u.allocated_item_id = i.id AND u.product_id = i.product_id
                    AND u.product_type = i.product_type) OR
                (f.product_type IN ('DIGITAL_FILE','REUSABLE_RESOURCE','MANUAL_FULFILLMENT')
                    AND i.inventory_unit_id IS NULL AND f.inventory_unit_id IS NULL
                    AND v.product_id = i.product_id AND v.product_type = i.product_type
                    AND v.sku = i.sku AND v.state IN ('ACTIVE','RETIRED'))))
    $$""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_fulfillment() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.state <> 'READY' OR NOT fulfillment_entitlement_matches(NEW) OR NOT EXISTS
            (SELECT 1 FROM orders o WHERE o.id = NEW.order_id AND o.status = 'PAID') THEN
            RAISE EXCEPTION 'Fulfillment ownership mismatch' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE OR REPLACE FUNCTION check_committed_payment() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status = 'MATCHED' AND NOT EXISTS (
            SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.id
            JOIN invoice_intents v ON v.order_id = o.id JOIN users b ON b.id = o.buyer_id
            WHERE o.id = NEW.order_id AND o.status = 'PAID' AND b.telegram_id = NEW.payer_id
              AND v.payload = NEW.payload AND NEW.currency = 'XTR'
              AND NEW.amount = i.price_stars * i.quantity
              AND NOT NEW.is_recurring AND NOT NEW.is_first_recurring AND NOT NEW.has_order_info
              AND NEW.subscription_expiration_date IS NULL AND NEW.shipping_option_id IS NULL
              AND ((o.attention_reason IS NOT NULL AND NOT EXISTS
                    (SELECT 1 FROM fulfillments f WHERE f.payment_id = NEW.id)) OR
                   (o.attention_reason IS NULL AND EXISTS (SELECT 1 FROM fulfillments f
                    WHERE f.payment_id = NEW.id AND f.order_id = o.id AND f.order_item_id = i.id
                      AND fulfillment_entitlement_matches(f))))) THEN
            RAISE EXCEPTION 'Payment requires atomic paid entitlement or attention'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$""")
    upgrade_manual()
    op.execute(DELIVERY_COMMIT)
    op.execute("""ALTER TABLE fulfillments DROP CONSTRAINT ck_fulfillments_state,
        ADD CONSTRAINT ck_fulfillments_state CHECK (state IN
        ('READY','RETRY_WAIT','CLAIMED','SENDING','SENT','UNCERTAIN','FAILED','AWAITING_MANUAL'))""")
    op.execute(DELIVERY_WORK_GUARD)


DELIVERY_COMMIT = """CREATE OR REPLACE FUNCTION check_delivery_commit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE delivery_work_id UUID := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM fulfillments f JOIN orders o ON o.id = f.order_id
        LEFT JOIN delivery_attempts a ON a.id = f.active_attempt_id AND a.work_id = f.id
        WHERE f.id = delivery_work_id AND fulfillment_entitlement_matches(f)
          AND f.send_count = (SELECT count(*) FROM delivery_attempts h
              WHERE h.work_id = f.id AND h.sending_at IS NOT NULL)
          AND ((f.state = 'READY' AND f.active_attempt_id IS NULL
                    AND o.status IN ('PAID','FULFILLING')) OR
               (f.state = 'CLAIMED' AND a.state = 'CLAIMED'
                    AND o.status IN ('PAID','FULFILLING')) OR
               (f.state = 'SENDING' AND a.state = 'SENDING' AND o.status = 'FULFILLING') OR
               (f.state = 'RETRY_WAIT' AND a.state = 'RETRYABLE' AND o.status = 'FULFILLING') OR
               (f.state = 'SENT' AND a.state = 'SENT' AND o.status = 'DELIVERED'
                    AND (f.product_type <> 'MANUAL_FULFILLMENT' OR EXISTS (
                        SELECT 1 FROM manual_actions m WHERE m.work_id = f.id
                        AND m.attempt_id = a.id AND m.status = 'applied'))) OR
               (f.state = 'AWAITING_MANUAL' AND a.state = 'SENT' AND o.status = 'FULFILLING'
                    AND f.product_type = 'MANUAL_FULFILLMENT') OR
               (f.state = 'UNCERTAIN' AND a.state = 'UNCERTAIN' AND o.status = 'FULFILLING') OR
               (f.state = 'FAILED' AND a.state = 'FAILED' AND o.status = 'FAILED'))) THEN
        RAISE EXCEPTION 'Delivery work, attempt and paid order must commit together'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END; $$"""


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM resource_versions) OR EXISTS (SELECT 1 FROM order_resources)
           OR EXISTS (SELECT 1 FROM order_items WHERE inventory_unit_id IS NULL) THEN
            RAISE EXCEPTION 'Resource history requires an explicit retention/migration decision';
        END IF;
    END; $$""")
    downgrade_manual()
    for statement in LEGACY_FUNCTIONS:
        op.execute(statement)
    op.execute("DROP FUNCTION fulfillment_entitlement_matches(fulfillments)")
    op.execute("""ALTER TABLE fulfillments DROP CONSTRAINT ck_fulfillments_state,
        ADD CONSTRAINT ck_fulfillments_state CHECK (state IN
        ('READY','RETRY_WAIT','CLAIMED','SENDING','SENT','UNCERTAIN','FAILED'))""")
    op.execute("""ALTER TABLE fulfillments DROP CONSTRAINT fk_fulfillments_item_resource,
        DROP CONSTRAINT fk_fulfillments_resource, DROP CONSTRAINT ck_fulfillments_type,
        DROP COLUMN resource_id, ALTER COLUMN inventory_unit_id SET NOT NULL,
        ADD CONSTRAINT ck_fulfillments_type CHECK (product_type IN ('UNIQUE_KEY','LICENSE'))""")
    op.execute("""ALTER TABLE order_items DROP CONSTRAINT ck_order_items_type,
        ALTER COLUMN inventory_unit_id SET NOT NULL,
        ADD CONSTRAINT ck_order_items_type CHECK (product_type IN ('UNIQUE_KEY','LICENSE'))""")
    op.execute("""ALTER TABLE orders DROP CONSTRAINT ck_orders_attention,
        ADD CONSTRAINT ck_orders_attention CHECK (attention_reason IS NULL OR
            (status = 'PAID' AND attention_reason = 'RESERVATION_LOST'))""")
    op.execute("DROP TRIGGER order_resource_commit ON order_items")
    op.drop_table("order_resources")
    op.drop_table("resource_versions")
    op.execute("DROP FUNCTION guard_resource_version()")
    op.execute("DROP FUNCTION guard_order_resource()")
    op.execute("DROP FUNCTION check_order_resource()")


def upgrade_manual() -> None:
    op.execute("""ALTER TABLE permissions DROP CONSTRAINT ck_permissions_name,
        ADD CONSTRAINT ck_permissions_name CHECK (name IN ('access.manage','settings.manage',
            'catalog.manage','inventory.manage','fulfillment.manage','orders.read',
            'payments.refund','tickets.manage'))""")
    op.execute("INSERT INTO permissions(name) VALUES ('fulfillment.manage')")
    op.execute(
        "INSERT INTO role_permissions(role, permission) "
        "VALUES ('OWNER','fulfillment.manage'), ('ADMIN','fulfillment.manage')"
    )
    op.execute("""CREATE TABLE manual_actions (
        id UUID PRIMARY KEY,
        actor_id UUID NOT NULL CONSTRAINT fk_manual_actor REFERENCES users(id),
        work_id UUID NOT NULL CONSTRAINT fk_manual_work REFERENCES fulfillments(id),
        attempt_id UUID NOT NULL CONSTRAINT fk_manual_attempt REFERENCES delivery_attempts(id),
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        completed_at TIMESTAMP WITH TIME ZONE,
        key_version INTEGER NOT NULL, nonce BYTEA NOT NULL, ciphertext BYTEA NOT NULL,
        CONSTRAINT ck_manual_status CHECK (status IN ('pending','applied','cancelled')),
        CONSTRAINT ck_manual_completion CHECK ((status = 'pending') = (completed_at IS NULL)),
        CONSTRAINT ck_manual_envelope CHECK (key_version > 0 AND octet_length(nonce) = 12
            AND octet_length(ciphertext) BETWEEN 17 AND 8320),
        CONSTRAINT uq_manual_nonce UNIQUE (key_version, nonce)
    )""")
    op.execute(
        "CREATE UNIQUE INDEX uq_manual_completed ON manual_actions(work_id) "
        "WHERE status = 'applied'"
    )
    op.execute("""CREATE FUNCTION guard_manual_action() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Manual history is immutable' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' AND NEW.status <> 'pending' THEN
            RAISE EXCEPTION 'Manual action requires confirmation' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'UPDATE' AND (
            (to_jsonb(NEW) - ARRAY['status','completed_at']) IS DISTINCT FROM
            (to_jsonb(OLD) - ARRAY['status','completed_at'])
            OR OLD.status <> 'pending' OR NEW.status NOT IN ('applied','cancelled')) THEN
            RAISE EXCEPTION 'Manual action is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> 'cancelled' AND NOT EXISTS (SELECT 1 FROM fulfillments f
            JOIN orders o ON o.id = f.order_id
            JOIN delivery_attempts a ON a.id = f.active_attempt_id AND a.work_id = f.id
            WHERE f.id = NEW.work_id AND f.state = 'AWAITING_MANUAL' AND NOT f.hold
              AND f.product_type = 'MANUAL_FULFILLMENT' AND a.id = NEW.attempt_id
              AND a.state = 'SENT' AND o.status = 'FULFILLING'
              AND fulfillment_entitlement_matches(f)) THEN
            RAISE EXCEPTION 'Manual work cannot be completed' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER manual_action_guard BEFORE INSERT OR UPDATE OR DELETE
        ON manual_actions
        FOR EACH ROW EXECUTE FUNCTION guard_manual_action()""")
    op.execute("""CREATE FUNCTION check_manual_commit() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status = 'applied' AND NOT EXISTS (SELECT 1 FROM fulfillments f
            JOIN orders o ON o.id = f.order_id WHERE f.id = NEW.work_id AND f.state = 'SENT'
              AND f.active_attempt_id = NEW.attempt_id AND o.status = 'DELIVERED'
              AND f.product_type = 'MANUAL_FULFILLMENT') THEN
            RAISE EXCEPTION 'Manual completion and order must commit together'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute("""CREATE CONSTRAINT TRIGGER manual_completion_commit AFTER INSERT OR UPDATE
        ON manual_actions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_manual_commit()""")
    op.execute("""CREATE TRIGGER manual_truncate BEFORE TRUNCATE ON manual_actions
        FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()""")


def downgrade_manual() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM manual_actions) OR EXISTS (SELECT 1 FROM user_permissions
            WHERE permission = 'fulfillment.manage') OR EXISTS (SELECT 1 FROM admin_actions
            WHERE permission = 'fulfillment.manage') THEN
            RAISE EXCEPTION
                'Manual permission/history requires an explicit retention/migration decision';
        END IF;
    END; $$""")
    op.drop_table("manual_actions")
    op.execute("DROP FUNCTION guard_manual_action()")
    op.execute("DROP FUNCTION check_manual_commit()")
    op.execute("DELETE FROM role_permissions WHERE permission = 'fulfillment.manage'")
    op.execute("DELETE FROM permissions WHERE name = 'fulfillment.manage'")
    op.execute("""ALTER TABLE permissions DROP CONSTRAINT ck_permissions_name,
        ADD CONSTRAINT ck_permissions_name CHECK (name IN ('access.manage','settings.manage',
            'catalog.manage','inventory.manage','orders.read','payments.refund','tickets.manage'))""")


DELIVERY_WORK_GUARD = """CREATE OR REPLACE FUNCTION guard_delivery_work() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Delivery history cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF ROW(NEW.id, NEW.order_id, NEW.order_item_id, NEW.payment_id,
      NEW.inventory_unit_id, NEW.resource_id,
         NEW.buyer_id, NEW.destination_telegram_id, NEW.product_type,
             NEW.created_at)
      IS DISTINCT FROM ROW(OLD.id, OLD.order_id, OLD.order_item_id, OLD.payment_id,
          OLD.inventory_unit_id, OLD.resource_id,
         OLD.buyer_id, OLD.destination_telegram_id, OLD.product_type,
             OLD.created_at) THEN
    RAISE EXCEPTION 'Delivery ownership is immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.state IN ('SENDING', 'UNCERTAIN') AND NEW.hold IS DISTINCT FROM OLD.hold
      THEN
    RAISE EXCEPTION 'In-flight or uncertain delivery requires reconciliation' USING
        ERRCODE = '23514';
  END IF;
  IF OLD.state IN ('SENT', 'UNCERTAIN', 'FAILED') AND
      ROW(NEW.state, NEW.active_attempt_id, NEW.send_count, NEW.due_at,
          NEW.attention_code)
        IS DISTINCT FROM ROW(OLD.state, OLD.active_attempt_id, OLD.send_count,
            OLD.due_at, OLD.attention_code) THEN
    RAISE EXCEPTION 'Terminal delivery requires an authorized decision' USING
        ERRCODE = '23514';
  END IF;
  IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
      (OLD.state IN ('READY', 'RETRY_WAIT') AND NEW.state = 'CLAIMED' AND NOT
          NEW.hold) OR
      (OLD.state = 'CLAIMED' AND NEW.state IN ('READY', 'SENDING', 'FAILED')) OR
      (OLD.state = 'SENDING' AND NEW.state IN ('SENT', 'RETRY_WAIT', 'UNCERTAIN',
          'FAILED', 'AWAITING_MANUAL')) OR
      (OLD.state = 'AWAITING_MANUAL' AND NEW.state = 'SENT'
       AND NEW.product_type = 'MANUAL_FULFILLMENT' AND NOT NEW.hold AND EXISTS (
         SELECT 1 FROM manual_actions m WHERE m.work_id = NEW.id
           AND m.attempt_id = NEW.active_attempt_id AND m.status = 'applied'))) THEN
    RAISE EXCEPTION 'Invalid delivery transition' USING ERRCODE = '23514';
  END IF;
  IF NEW.state = 'AWAITING_MANUAL' AND NEW.product_type <> 'MANUAL_FULFILLMENT' THEN
    RAISE EXCEPTION 'Only manual work can await staff' USING ERRCODE = '23514';
  END IF;
  IF NEW.state = 'SENDING' AND NEW.hold THEN
    RAISE EXCEPTION 'Held delivery cannot send' USING ERRCODE = '23514';
  END IF;
  IF NEW.active_attempt_id IS DISTINCT FROM OLD.active_attempt_id AND NOT (
      (OLD.state IN ('READY', 'RETRY_WAIT') AND NEW.state = 'CLAIMED') OR
      (OLD.state = 'CLAIMED' AND NEW.state = 'READY')) THEN
    RAISE EXCEPTION 'Delivery fencing token is immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.send_count IS DISTINCT FROM OLD.send_count AND NOT (
      OLD.state = 'CLAIMED' AND NEW.state = 'SENDING' AND NEW.send_count =
          OLD.send_count + 1) THEN
    RAISE EXCEPTION 'Invalid delivery send count' USING ERRCODE = '23514';
  END IF;
  IF NEW.due_at IS DISTINCT FROM OLD.due_at AND NEW.state = OLD.state THEN
    RAISE EXCEPTION 'Retry schedule is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END; $$"""

# Frozen predecessor definitions preserve finite history on a safe downgrade.
LEGACY_FUNCTIONS = (
    """CREATE OR REPLACE FUNCTION guard_fulfillment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
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
END; $$""",
    """CREATE OR REPLACE FUNCTION check_committed_payment() RETURNS trigger LANGUAGE plpgsql AS $$
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
END; $$""",
    """CREATE OR REPLACE FUNCTION guard_delivery_work() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Delivery history cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF ROW(NEW.id, NEW.order_id, NEW.order_item_id, NEW.payment_id,
      NEW.inventory_unit_id,
         NEW.buyer_id, NEW.destination_telegram_id, NEW.product_type,
             NEW.created_at)
      IS DISTINCT FROM ROW(OLD.id, OLD.order_id, OLD.order_item_id, OLD.payment_id,
          OLD.inventory_unit_id,
         OLD.buyer_id, OLD.destination_telegram_id, OLD.product_type,
             OLD.created_at) THEN
    RAISE EXCEPTION 'Delivery ownership is immutable' USING ERRCODE = '23514';
  END IF;
  IF OLD.state IN ('SENDING', 'UNCERTAIN') AND NEW.hold IS DISTINCT FROM OLD.hold
      THEN
    RAISE EXCEPTION 'In-flight or uncertain delivery requires reconciliation' USING
        ERRCODE = '23514';
  END IF;
  IF OLD.state IN ('SENT', 'UNCERTAIN', 'FAILED') AND
      ROW(NEW.state, NEW.active_attempt_id, NEW.send_count, NEW.due_at,
          NEW.attention_code)
        IS DISTINCT FROM ROW(OLD.state, OLD.active_attempt_id, OLD.send_count,
            OLD.due_at, OLD.attention_code) THEN
    RAISE EXCEPTION 'Terminal delivery requires an authorized decision' USING
        ERRCODE = '23514';
  END IF;
  IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
      (OLD.state IN ('READY', 'RETRY_WAIT') AND NEW.state = 'CLAIMED' AND NOT
          NEW.hold) OR
      (OLD.state = 'CLAIMED' AND NEW.state IN ('READY', 'SENDING', 'FAILED')) OR
      (OLD.state = 'SENDING' AND NEW.state IN ('SENT', 'RETRY_WAIT', 'UNCERTAIN',
          'FAILED'))) THEN
    RAISE EXCEPTION 'Invalid delivery transition' USING ERRCODE = '23514';
  END IF;
  IF NEW.state = 'SENDING' AND NEW.hold THEN
    RAISE EXCEPTION 'Held delivery cannot send' USING ERRCODE = '23514';
  END IF;
  IF NEW.active_attempt_id IS DISTINCT FROM OLD.active_attempt_id AND NOT (
      (OLD.state IN ('READY', 'RETRY_WAIT') AND NEW.state = 'CLAIMED') OR
      (OLD.state = 'CLAIMED' AND NEW.state = 'READY')) THEN
    RAISE EXCEPTION 'Delivery fencing token is immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.send_count IS DISTINCT FROM OLD.send_count AND NOT (
      OLD.state = 'CLAIMED' AND NEW.state = 'SENDING' AND NEW.send_count =
          OLD.send_count + 1) THEN
    RAISE EXCEPTION 'Invalid delivery send count' USING ERRCODE = '23514';
  END IF;
  IF NEW.due_at IS DISTINCT FROM OLD.due_at AND NEW.state = OLD.state THEN
    RAISE EXCEPTION 'Retry schedule is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END; $$""",
    """CREATE OR REPLACE FUNCTION check_delivery_commit() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
  delivery_work_id UUID := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM fulfillments f JOIN orders o ON o.id = f.order_id
    JOIN payments p ON p.id = f.payment_id AND p.order_id = o.id AND p.status =
        'MATCHED'
    JOIN inventory_units u ON u.id = f.inventory_unit_id AND u.status = 'ALLOCATED'
    LEFT JOIN delivery_attempts a ON a.id = f.active_attempt_id AND a.work_id = f.id
    WHERE f.id = delivery_work_id AND u.allocated_item_id = f.order_item_id
      AND f.send_count = (SELECT count(*) FROM delivery_attempts h
                         WHERE h.work_id = f.id AND h.sending_at IS NOT NULL)
      AND (
        (f.state = 'READY' AND f.active_attempt_id IS NULL AND o.status IN ('PAID',
            'FULFILLING')) OR
        (f.state = 'CLAIMED' AND a.state = 'CLAIMED' AND o.status IN ('PAID',
            'FULFILLING')) OR
        (f.state = 'SENDING' AND a.state = 'SENDING' AND o.status = 'FULFILLING') OR
        (f.state = 'RETRY_WAIT' AND a.state = 'RETRYABLE' AND o.status =
            'FULFILLING') OR
        (f.state = 'SENT' AND a.state = 'SENT' AND o.status = 'DELIVERED') OR
        (f.state = 'UNCERTAIN' AND a.state = 'UNCERTAIN' AND o.status =
            'FULFILLING') OR
        (f.state = 'FAILED' AND a.state = 'FAILED' AND o.status = 'FAILED'))) THEN
    RAISE EXCEPTION 'Delivery work, attempt and paid order must commit together'
        USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END; $$""",
)
