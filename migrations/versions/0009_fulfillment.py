"""Fenced finite delivery attempts, recovery and retained result observations."""

from alembic import op

revision = "0009_fulfillment"
down_revision = "0008_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE orders DROP CONSTRAINT ck_orders_status, DROP CONSTRAINT ck_orders_close"""
    )
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK (status IN
    ('AWAITING_PAYMENT', 'EXPIRED', 'PAID', 'FULFILLING', 'DELIVERED', 'FAILED'))""")
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_close CHECK ((status IN
    ('AWAITING_PAYMENT', 'PAID', 'FULFILLING', 'DELIVERED', 'FAILED') AND closed_at
    IS NULL AND close_reason IS NULL) OR (status IN ('EXPIRED', 'PAID',
    'FULFILLING', 'DELIVERED', 'FAILED') AND closed_at IS NOT NULL AND close_reason
    IS NOT NULL AND close_reason IN ('CUSTOMER_CANCELLED', 'TTL_EXPIRED',
    'INVOICE_REJECTED')))""")
    op.execute("""ALTER TABLE fulfillments DROP CONSTRAINT ck_fulfillments_state,
    ADD COLUMN active_attempt_id UUID,
    ADD COLUMN due_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
    ADD COLUMN send_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN hold BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN attention_code VARCHAR(32)""")
    op.execute("""CREATE TABLE delivery_attempts (
    id UUID NOT NULL,
    work_id UUID NOT NULL,
    state VARCHAR(16) NOT NULL,
    claimed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    lease_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    sending_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    message_id BIGINT,
    error_code VARCHAR(32),
    retry_after INTEGER,
    PRIMARY KEY (id),
    CONSTRAINT ck_attempt_lease CHECK (lease_expires_at > claimed_at),
    CONSTRAINT ck_attempt_state CHECK ((state = 'CLAIMED' AND sending_at IS NULL AND
        completed_at IS NULL AND message_id IS NULL AND error_code IS NULL AND
        retry_after IS NULL) OR (state = 'SENDING' AND sending_at IS NOT NULL AND
        completed_at IS NULL AND message_id IS NULL AND error_code IS NULL AND
        retry_after IS NULL) OR (state = 'SENT' AND sending_at IS NOT NULL AND
        completed_at IS NOT NULL AND message_id > 0 AND message_id IS NOT NULL AND
        error_code IS NULL AND retry_after IS NULL) OR (state IN ('RETRYABLE',
        'UNCERTAIN', 'FAILED', 'ABANDONED') AND completed_at IS NOT NULL AND
        message_id IS NULL AND error_code IS NOT NULL AND ((state = 'RETRYABLE' AND
        sending_at IS NOT NULL AND retry_after BETWEEN 1 AND 86400 AND retry_after
        IS NOT NULL) OR (state <> 'RETRYABLE' AND retry_after IS NULL)))),
    CONSTRAINT ck_attempt_code CHECK (error_code IS NULL OR error_code IN
        ('RATE_LIMIT', 'REJECTED', 'NETWORK', 'LEASE_EXPIRED',
        'INVALID_ENTITLEMENT', 'CRYPTO', 'RETRY_EXHAUSTED', 'HELD')),
    CONSTRAINT ck_attempt_timestamps CHECK ((sending_at IS NULL OR sending_at >=
        claimed_at) AND (completed_at IS NULL OR completed_at >=
        COALESCE(sending_at, claimed_at))),
    CONSTRAINT fk_attempt_work FOREIGN KEY(work_id) REFERENCES fulfillments (id)
)""")
    op.execute("""CREATE INDEX ix_attempt_expiry ON delivery_attempts (lease_expires_at) WHERE state
    IN ('CLAIMED', 'SENDING')""")
    op.execute("""CREATE TABLE delivery_observations (
    attempt_id UUID NOT NULL,
    digest BYTEA NOT NULL,
    state VARCHAR(16) NOT NULL,
    message_id BIGINT,
    error_code VARCHAR(32),
    retry_after INTEGER,
    observed_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
    PRIMARY KEY (attempt_id, digest),
    CONSTRAINT ck_delivery_observation_digest CHECK (octet_length(digest) = 32),
    CONSTRAINT ck_delivery_observation_state CHECK (state IN ('SENT', 'RETRYABLE',
        'UNCERTAIN', 'FAILED')),
    CONSTRAINT fk_observation_attempt FOREIGN KEY(attempt_id) REFERENCES
        delivery_attempts (id)
)""")
    op.execute("CREATE INDEX ix_delivery_attempt_work ON delivery_attempts (work_id)")
    op.execute("CREATE INDEX ix_fulfillments_order ON fulfillments (order_id)")
    op.execute("CREATE INDEX ix_fulfillments_attempt ON fulfillments (active_attempt_id)")
    op.execute("""ALTER TABLE delivery_attempts ADD CONSTRAINT ck_attempt_send_lease
    CHECK (sending_at IS NULL OR lease_expires_at > sending_at)""")
    op.execute("""ALTER TABLE delivery_observations ADD CONSTRAINT ck_delivery_observation_result
    CHECK ((state = 'SENT' AND message_id IS NOT NULL AND message_id > 0 AND error_code IS NULL
      AND retry_after IS NULL) OR (state = 'RETRYABLE' AND message_id IS NULL
      AND error_code = 'RATE_LIMIT' AND error_code IS NOT NULL AND retry_after IS NOT NULL
      AND retry_after BETWEEN 1 AND 86400) OR (state IN ('UNCERTAIN', 'FAILED')
      AND message_id IS NULL AND retry_after IS NULL AND error_code IS NOT NULL
      AND error_code IN ('RATE_LIMIT', 'REJECTED', 'NETWORK', 'LEASE_EXPIRED',
      'INVALID_ENTITLEMENT', 'CRYPTO', 'RETRY_EXHAUSTED', 'HELD')))""")
    op.execute("""ALTER TABLE fulfillments ADD CONSTRAINT fk_fulfillments_attempt FOREIGN
    KEY(active_attempt_id) REFERENCES delivery_attempts (id)""")
    op.execute("""ALTER TABLE fulfillments ADD CONSTRAINT ck_fulfillments_state CHECK (state IN
    ('READY', 'RETRY_WAIT', 'CLAIMED', 'SENDING', 'SENT', 'UNCERTAIN', 'FAILED'))""")
    op.execute("""ALTER TABLE fulfillments ADD CONSTRAINT ck_fulfillments_send_count CHECK (
    send_count
    BETWEEN 0 AND 5)""")
    op.execute("""ALTER TABLE fulfillments ADD CONSTRAINT ck_fulfillments_active CHECK ((state =
    'READY') = (active_attempt_id IS NULL))""")
    op.execute("""ALTER TABLE fulfillments ADD CONSTRAINT ck_fulfillments_attention CHECK ((state IN
    ('FAILED', 'UNCERTAIN') AND attention_code IS NOT NULL AND attention_code IN
    ('REJECTED', 'NETWORK', 'LEASE_EXPIRED', 'INVALID_ENTITLEMENT', 'CRYPTO',
    'RETRY_EXHAUSTED')) OR (state NOT IN ('FAILED', 'UNCERTAIN') AND attention_code
    IS NULL))""")
    op.execute("""CREATE INDEX ix_fulfillments_due ON fulfillments (due_at, id) WHERE state IN
    ('READY', 'RETRY_WAIT') AND NOT hold""")
    op.execute("""DROP TRIGGER fulfillments_immutable ON fulfillments""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_order() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Order history cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF ROW(NEW.id, NEW.buyer_id, NEW.checkout_action_id, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.buyer_id, OLD.checkout_action_id,
           OLD.created_at)
     OR (ROW(NEW.closed_at, NEW.close_reason) IS DISTINCT FROM ROW(OLD.closed_at,
         OLD.close_reason)
         AND NOT (OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'EXPIRED'))
     OR (OLD.status IN ('PAID', 'FULFILLING', 'DELIVERED', 'FAILED') AND
         ROW(NEW.expires_at, NEW.attention_reason) IS DISTINCT FROM
             ROW(OLD.expires_at, OLD.attention_reason))
     OR (NEW.attention_reason IS DISTINCT FROM OLD.attention_reason AND NEW.status
         <> 'PAID') THEN
    RAISE EXCEPTION 'Order identity and payment history are immutable' USING ERRCODE
        = '23514';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
      (OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'EXPIRED') OR
      (OLD.status IN ('AWAITING_PAYMENT', 'EXPIRED') AND NEW.status = 'PAID' AND
          EXISTS (
        SELECT 1 FROM payments p WHERE p.order_id = OLD.id AND p.status =
            'MATCHED')) OR
      (OLD.status IN ('PAID', 'FULFILLING') AND NEW.status = 'FULFILLING' AND EXISTS
          (
        SELECT 1 FROM fulfillments f WHERE f.order_id = OLD.id AND f.state =
            'SENDING')) OR
      (OLD.status = 'FULFILLING' AND NEW.status = 'DELIVERED' AND EXISTS (
        SELECT 1 FROM fulfillments f WHERE f.order_id = OLD.id AND f.state =
            'SENT')) OR
      (OLD.status IN ('PAID', 'FULFILLING') AND NEW.status = 'FAILED' AND EXISTS (
        SELECT 1 FROM fulfillments f WHERE f.order_id = OLD.id AND f.state =
            'FAILED'))) THEN
    RAISE EXCEPTION 'Invalid order payment or delivery transition' USING ERRCODE =
        '23514';
  END IF;
  IF NEW.expires_at IS DISTINCT FROM OLD.expires_at AND NOT (
      OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'AWAITING_PAYMENT'
      AND NEW.expires_at >= OLD.expires_at AND EXISTS (
        SELECT 1 FROM pre_checkout_approvals a WHERE a.order_id = OLD.id
          AND NEW.expires_at = GREATEST(OLD.expires_at, a.approved_at + interval
              '15 minutes'))) THEN
    RAISE EXCEPTION 'Invalid approval extension' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END; $$""")
    op.execute("""CREATE FUNCTION guard_delivery_work() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
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
END; $$""")
    op.execute("""CREATE TRIGGER delivery_work_guard BEFORE UPDATE OR DELETE ON fulfillments
    FOR EACH
    ROW EXECUTE FUNCTION guard_delivery_work()""")
    op.execute("""CREATE FUNCTION guard_delivery_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Delivery attempts cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.state <> 'CLAIMED' THEN
      RAISE EXCEPTION 'Delivery attempt requires claim' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;
  IF ROW(NEW.id, NEW.work_id, NEW.claimed_at) IS DISTINCT FROM ROW(OLD.id,
      OLD.work_id, OLD.claimed_at)
     OR (OLD.state NOT IN ('CLAIMED', 'SENDING') AND NEW IS DISTINCT FROM OLD)
     OR (OLD.state = 'SENDING' AND NEW.sending_at IS DISTINCT FROM OLD.sending_at)
     OR (NEW IS DISTINCT FROM OLD AND NEW.state = OLD.state)
     OR (NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at AND NOT (
         OLD.state = 'CLAIMED' AND NEW.state = 'SENDING'))
     OR (NEW.state IS DISTINCT FROM OLD.state AND NOT (
         (OLD.state = 'CLAIMED' AND NEW.state IN ('SENDING', 'FAILED', 'ABANDONED'))
             OR
         (OLD.state = 'SENDING' AND NEW.state IN ('SENT', 'RETRYABLE', 'UNCERTAIN',
             'FAILED')))) THEN
    RAISE EXCEPTION 'Invalid attempt transition or fencing' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END; $$""")
    op.execute("""CREATE TRIGGER delivery_attempt_guard BEFORE INSERT OR UPDATE OR DELETE ON
    delivery_attempts FOR EACH ROW EXECUTE FUNCTION guard_delivery_attempt()""")
    op.execute("""CREATE FUNCTION check_delivery_commit() RETURNS trigger LANGUAGE plpgsql AS $$
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
END; $$""")
    op.execute("""CREATE CONSTRAINT TRIGGER fulfillments_commit AFTER INSERT OR UPDATE
    ON fulfillments
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
    check_delivery_commit('id')""")
    op.execute("""CREATE CONSTRAINT TRIGGER delivery_attempts_commit AFTER INSERT OR UPDATE ON
    delivery_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
    check_delivery_commit('work_id')""")
    op.execute("""CREATE TRIGGER attempt_truncate BEFORE TRUNCATE ON delivery_attempts FOR EACH
    STATEMENT EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE TRIGGER delivery_observation_immutable BEFORE UPDATE OR DELETE ON
    delivery_observations FOR EACH ROW EXECUTE FUNCTION guard_payment_history()""")
    op.execute("""CREATE TRIGGER delivery_observation_truncate BEFORE TRUNCATE ON
    delivery_observations FOR EACH STATEMENT EXECUTE FUNCTION
    guard_payment_history()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM delivery_attempts) OR EXISTS (SELECT 1 FROM
      delivery_observations)
     OR EXISTS (SELECT 1 FROM fulfillments WHERE state <> 'READY' OR hold OR
         send_count <> 0)
     OR EXISTS (SELECT 1 FROM orders WHERE status IN ('FULFILLING', 'DELIVERED',
         'FAILED')) THEN
    RAISE EXCEPTION
        'Delivery history requires an explicit retention/migration decision';
  END IF;
END; $$""")
    op.execute("""DROP TRIGGER fulfillments_commit ON fulfillments""")
    op.execute("""DROP TRIGGER delivery_work_guard ON fulfillments""")
    op.execute("""DROP INDEX ix_fulfillments_due""")
    op.execute("DROP INDEX ix_fulfillments_order")
    op.execute("DROP INDEX ix_fulfillments_attempt")
    op.execute("""ALTER TABLE fulfillments DROP CONSTRAINT fk_fulfillments_attempt, DROP CONSTRAINT
    ck_fulfillments_active, DROP CONSTRAINT ck_fulfillments_send_count, DROP
    CONSTRAINT ck_fulfillments_attention, DROP CONSTRAINT ck_fulfillments_state""")
    op.drop_table("delivery_observations")
    op.drop_table("delivery_attempts")
    op.execute("""ALTER TABLE fulfillments DROP COLUMN active_attempt_id, DROP COLUMN due_at, DROP
    COLUMN send_count, DROP COLUMN hold, DROP COLUMN attention_code, ADD CONSTRAINT
    ck_fulfillments_state CHECK (state = 'READY')""")
    op.execute("""DROP FUNCTION guard_delivery_work()""")
    op.execute("""DROP FUNCTION guard_delivery_attempt()""")
    op.execute("""DROP FUNCTION check_delivery_commit()""")
    op.execute("""CREATE TRIGGER fulfillments_immutable BEFORE UPDATE OR DELETE ON fulfillments FOR
    EACH ROW EXECUTE FUNCTION guard_payment_history()""")
    op.execute(
        """ALTER TABLE orders DROP CONSTRAINT ck_orders_status, DROP CONSTRAINT ck_orders_close"""
    )
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK (status IN
    ('AWAITING_PAYMENT', 'EXPIRED', 'PAID'))""")
    op.execute("""ALTER TABLE orders ADD CONSTRAINT ck_orders_close CHECK (
    (status IN ('AWAITING_PAYMENT', 'PAID') AND closed_at IS NULL AND close_reason
        IS NULL) OR
    (status IN ('EXPIRED', 'PAID') AND closed_at IS NOT NULL AND close_reason IS NOT
        NULL
     AND close_reason IN ('CUSTOMER_CANCELLED', 'TTL_EXPIRED', 'INVOICE_REJECTED')))""")
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
