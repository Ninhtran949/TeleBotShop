"""Durable full refunds, received refund evidence and owned delivery holds."""

import sqlalchemy as sa
from alembic import op

revision = "0013_refunds"
down_revision = "0012_support"
branch_labels = None
depends_on = None


def rewrite(name, before, after, expected=1, *, reverse=False):
    if reverse:
        before, after = after, before
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef(to_regproc(:name))"), {"name": name}
    )
    if definition is None or definition.count(before) != expected:
        raise RuntimeError("Unexpected predecessor function: " + name)
    op.execute(definition.replace(before, after))


CHANGES = [
    (
        "guard_order",
        "(OLD.status = 'AWAITING_PAYMENT' AND NEW.status = 'EXPIRED') OR",
        "(OLD.status IN ('PAID','FULFILLING','DELIVERED','FAILED') AND NEW.status='REFUNDED' "
        "AND refund_confirmed(OLD.id)) OR\n      (OLD.status = "
        "'AWAITING_PAYMENT' AND NEW.status = 'EXPIRED') OR",
    ),
    (
        "check_committed_payment",
        "o.status = 'PAID' AND b.telegram_id = NEW.payer_id",
        "(o.status = 'PAID' OR (o.status='REFUNDED' AND "
        "refund_confirmed(o.id))) AND b.telegram_id = NEW.payer_id",
    ),
    (
        "check_delivery_commit",
        "o.status IN ('PAID','FULFILLING')",
        "(o.status IN ('PAID','FULFILLING') OR (o.status='REFUNDED' AND "
        "f.hold AND refund_confirmed(o.id)))",
        2,
    ),
    (
        "check_delivery_commit",
        "o.status = 'FULFILLING'",
        "(o.status = 'FULFILLING' OR (o.status='REFUNDED' AND f.hold AND refund_confirmed(o.id)))",
        4,
    ),
    (
        "check_delivery_commit",
        "o.status = 'DELIVERED'",
        "(o.status = 'DELIVERED' OR (o.status='REFUNDED' AND f.hold AND refund_confirmed(o.id)))",
    ),
    (
        "check_delivery_commit",
        "o.status = 'FAILED'",
        "(o.status = 'FAILED' OR (o.status='REFUNDED' AND f.hold AND refund_confirmed(o.id)))",
    ),
    (
        "guard_delivery_work",
        "IF OLD.state IN ('SENDING', 'UNCERTAIN') AND NEW.hold IS DISTINCT FROM OLD.hold",
        "IF OLD.state IN ('SENDING', 'UNCERTAIN') AND NEW.hold IS DISTINCT FROM OLD.hold "
        "AND NOT ((NEW.hold AND refund_authorizes_hold(NEW.refund_intent_id, OLD.id)) OR "
        "(NOT NEW.hold AND OLD.refund_intent_id IS NOT NULL AND EXISTS "
        "(SELECT 1 FROM refund_intents r WHERE r.id=OLD.refund_intent_id AND r.status='FAILED')))",
    ),
    (
        "guard_resend_hold",
        "IF NEW.hold AND NOT OLD.hold AND EXISTS",
        "IF NEW.hold AND NOT OLD.hold AND NOT "
        "refund_authorizes_hold(NEW.refund_intent_id, OLD.id) AND EXISTS",
    ),
    (
        "guard_delivery_work",
        "IF NEW.state = 'SENDING' AND NEW.hold THEN",
        "IF NEW.state = 'SENDING' AND NEW.hold AND NOT "
        "(OLD.state='SENDING' AND refund_confirmed(OLD.order_id)) THEN",
    ),
]


def order_constraints(refunds):
    terminal = "'PAID','FULFILLING','DELIVERED','FAILED'" + (",'REFUNDED'" if refunds else "")
    attention = "status IN ('PAID','REFUNDED')" if refunds else "status='PAID'"
    for name in ["status", "close", "attention"]:
        op.drop_constraint("ck_orders_" + name, "orders", type_="check")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK "
        f"(status IN ('AWAITING_PAYMENT','EXPIRED',{terminal}))"
    )
    op.execute(f"""ALTER TABLE orders ADD CONSTRAINT ck_orders_close CHECK (
        (status IN ('AWAITING_PAYMENT',{terminal}) AND closed_at IS NULL AND close_reason IS NULL)
        OR (status IN ('EXPIRED',{terminal}) AND closed_at IS NOT NULL AND close_reason IS NOT NULL
        AND close_reason IN ('CUSTOMER_CANCELLED','TTL_EXPIRED','INVOICE_REJECTED')))""")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_attention CHECK "
        f"(attention_reason IS NULL OR ({attention} AND attention_reason IN "
        "('RESERVATION_LOST','ENTITLEMENT_UNAVAILABLE')))"
    )


def upgrade():
    order_constraints(True)
    op.execute("""CREATE TABLE payment_verifications (
        payment_id UUID PRIMARY KEY REFERENCES payments(id),
        actor_id UUID NOT NULL REFERENCES users(id), payer_id BIGINT NOT NULL,
        amount BIGINT NOT NULL, transaction_at TIMESTAMPTZ NOT NULL,
        verified_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_payment_verification_amount CHECK (payer_id > 0 AND amount > 0))""")
    op.execute("""CREATE TABLE refund_receipts (
        id UUID PRIMARY KEY, charge_id VARCHAR(1024) NOT NULL, currency VARCHAR(16) NOT NULL,
        amount BIGINT NOT NULL, payload VARCHAR(128) NOT NULL,
        provider_charge_id VARCHAR(1024) NOT NULL, source VARCHAR(16) NOT NULL,
        digest BYTEA NOT NULL,
        received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT uq_refund_receipt UNIQUE(charge_id,digest),
        CONSTRAINT ck_refund_receipt_source CHECK (source IN ('EVENT','TRANSACTION')),
        CONSTRAINT ck_refund_receipt CHECK (octet_length(charge_id) BETWEEN 1 AND 1024
            AND amount > 0 AND octet_length(digest)=32))""")
    op.execute("""CREATE TABLE refund_intents (
        id UUID PRIMARY KEY, payment_id UUID NOT NULL REFERENCES payments(id),
        actor_id UUID REFERENCES users(id), expected JSONB NOT NULL,
        accept_uncertain BOOLEAN NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
        status VARCHAR(16) NOT NULL, started_at TIMESTAMPTZ, deadline TIMESTAMPTZ,
        completed_at TIMESTAMPTZ, confirmed_by VARCHAR(16), observed_state VARCHAR(16),
        key_version INTEGER, nonce BYTEA, ciphertext BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT ck_refund_status CHECK (status IN
            ('PENDING','SENDING','SUCCEEDED','FAILED','UNCERTAIN','CANCELLED')),
        CONSTRAINT ck_refund_window CHECK (
            ((status IN ('PENDING','CANCELLED') AND started_at IS NULL AND deadline IS NULL)
            OR (status IN ('SENDING','FAILED','UNCERTAIN','SUCCEEDED') AND started_at IS NOT NULL
                AND deadline IS NOT NULL AND deadline > started_at)
            OR (actor_id IS NULL AND status='SUCCEEDED' AND started_at IS NULL
                AND deadline IS NULL)) IS TRUE),
        CONSTRAINT ck_refund_completed CHECK
            ((completed_at IS NULL) = (status IN ('PENDING','SENDING'))),
        CONSTRAINT ck_refund_confirmation CHECK (((status='SUCCEEDED'
            AND confirmed_by IN ('API','EVENT','TRANSACTION')) OR
            (status<>'SUCCEEDED' AND confirmed_by IS NULL)) IS TRUE),
        CONSTRAINT ck_refund_observed CHECK (observed_state IS NULL OR
            observed_state IN ('SUCCEEDED','FAILED','UNCERTAIN')),
        CONSTRAINT ck_refund_expected CHECK (jsonb_typeof(expected)='object'),
        CONSTRAINT ck_refund_evidence CHECK (((actor_id IS NULL AND status='SUCCEEDED'
            AND key_version IS NULL AND nonce IS NULL AND ciphertext IS NULL) OR
            (actor_id IS NOT NULL AND key_version>0 AND octet_length(nonce)=12
            AND octet_length(ciphertext) BETWEEN 17 AND 3200)) IS TRUE),
        CONSTRAINT uq_refund_nonce UNIQUE(key_version,nonce))""")
    op.execute(
        "CREATE UNIQUE INDEX uq_refund_active_payment ON refund_intents(payment_id) "
        "WHERE status IN ('PENDING','SENDING','UNCERTAIN','SUCCEEDED')"
    )
    op.create_index("ix_refund_queue", "refund_intents", ["status", "created_at", "id"])
    op.create_index(
        "ix_refund_payment_history", "refund_intents", ["payment_id", "created_at", "id"]
    )
    op.create_index("ix_fulfillments_payment", "fulfillments", ["payment_id"])
    op.add_column("fulfillments", sa.Column("refund_intent_id", sa.Uuid))
    op.create_foreign_key(
        "fk_fulfillments_refund", "fulfillments", "refund_intents", ["refund_intent_id"], ["id"]
    )
    op.execute("""CREATE FUNCTION refund_snapshot(payment_uuid UUID) RETURNS jsonb
        LANGUAGE sql STABLE AS $$ SELECT jsonb_build_object(
            'order_status', CASE WHEN p.status='MATCHED' THEN o.status ELSE NULL END,
            'work_id', f.id::text, 'work_state', f.state,
            'attempt_id', f.active_attempt_id::text, 'send_count', f.send_count, 'hold', f.hold,
            'resend_id', (SELECT a.id::text FROM resend_actions a WHERE a.work_id=f.id
                AND a.status IN ('PENDING','SENDING','UNCERTAIN')
                ORDER BY a.created_at DESC,a.id DESC LIMIT 1),
            'resend_state', (SELECT a.status FROM resend_actions a WHERE a.work_id=f.id
                AND a.status IN ('PENDING','SENDING','UNCERTAIN')
                ORDER BY a.created_at DESC,a.id DESC LIMIT 1))
        FROM payments p LEFT JOIN orders o ON o.id=p.order_id
        LEFT JOIN fulfillments f ON f.payment_id=p.id WHERE p.id=payment_uuid $$""")
    op.execute("""CREATE FUNCTION refund_confirmed(order_uuid UUID) RETURNS boolean
        LANGUAGE sql STABLE AS $$ SELECT EXISTS (SELECT 1 FROM refund_intents r
        JOIN payments p ON p.id=r.payment_id WHERE p.order_id=order_uuid
        AND p.status='MATCHED' AND r.status='SUCCEEDED') $$""")
    op.execute("""CREATE FUNCTION refund_authorizes_hold(intent_uuid UUID, work_uuid UUID)
        RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT EXISTS (
        SELECT 1 FROM refund_intents r JOIN fulfillments f ON f.payment_id=r.payment_id
        WHERE r.id=intent_uuid AND f.id=work_uuid
        AND r.status IN ('SENDING','UNCERTAIN','SUCCEEDED') AND
        (r.confirmed_by IS NOT NULL OR (r.accept_uncertain
            AND f.state <> 'SENDING' AND r.expected->>'work_id'=f.id::text
            AND r.expected->>'attempt_id' IS NOT DISTINCT FROM f.active_attempt_id::text
            AND NOT EXISTS (SELECT 1 FROM resend_actions a WHERE a.work_id=f.id
                AND a.status IN ('SENDING','UNCERTAIN') AND
                (a.status='SENDING' OR r.expected->>'resend_id'
                    IS DISTINCT FROM a.id::text))))) $$""")
    for args in CHANGES:
        rewrite(*args)
    op.execute("""CREATE FUNCTION received_refund(payment_uuid UUID, proof_source VARCHAR)
        RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT EXISTS (
        SELECT 1 FROM payments p JOIN refund_receipts e ON e.charge_id=p.charge_id
        WHERE p.id=payment_uuid AND p.currency='XTR' AND e.currency=p.currency
            AND e.amount=p.amount AND e.payload=p.payload AND e.source=proof_source
            AND (e.provider_charge_id='' OR e.provider_charge_id=p.provider_charge_id)) $$""")
    op.execute("""CREATE FUNCTION guard_refund_intent() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN
            RAISE EXCEPTION 'refund history is immutable'; END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status <> 'PENDING' AND NOT (NEW.actor_id IS NULL AND NEW.status='SUCCEEDED'
                AND NEW.confirmed_by IN ('EVENT','TRANSACTION')
                AND received_refund(NEW.payment_id,NEW.confirmed_by)) THEN
                RAISE EXCEPTION 'invalid initial refund';
            END IF;
        ELSE
            IF (to_jsonb(NEW)-ARRAY['status','started_at','deadline','completed_at',
                'confirmed_by','observed_state']) IS DISTINCT FROM
                (to_jsonb(OLD)-ARRAY['status','started_at','deadline','completed_at',
                    'confirmed_by','observed_state']) THEN
                RAISE EXCEPTION 'refund identity is immutable';
            END IF;
            IF NOT ((OLD.status='PENDING' AND NEW.status IN ('SENDING','CANCELLED','SUCCEEDED'))
                OR (OLD.status='SENDING' AND NEW.status IN ('SUCCEEDED','FAILED','UNCERTAIN'))
                OR (OLD.status='UNCERTAIN' AND NEW.status='SUCCEEDED')
                OR (OLD.status=NEW.status AND OLD.observed_state IS NULL
                    AND NEW.observed_state IS NOT NULL
                    AND (to_jsonb(NEW)-'observed_state')=(to_jsonb(OLD)-'observed_state'))) THEN
                RAISE EXCEPTION 'invalid refund transition';
            END IF;
            IF OLD.status <> 'PENDING' AND ROW(NEW.started_at,NEW.deadline)
                IS DISTINCT FROM ROW(OLD.started_at,OLD.deadline) THEN
                RAISE EXCEPTION 'refund send window is immutable';
            END IF;
            IF OLD.status='PENDING' AND NEW.status='SENDING'
                AND NEW.expires_at<=clock_timestamp() THEN
                RAISE EXCEPTION 'refund confirmation expired';
            END IF;
            IF OLD.status='PENDING' AND NEW.status='SENDING'
                AND NEW.expected IS DISTINCT FROM refund_snapshot(NEW.payment_id) THEN
                RAISE EXCEPTION 'refund confirmation changed';
            END IF;
            IF OLD.status='PENDING' AND NEW.status='SUCCEEDED' AND NOT
                (NEW.confirmed_by IN ('EVENT','TRANSACTION') AND
                    received_refund(NEW.payment_id,NEW.confirmed_by)) THEN
                RAISE EXCEPTION 'refund must begin before API success';
            END IF;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM payments p WHERE p.id=NEW.payment_id AND p.currency='XTR'
            AND p.amount>0 AND (p.status='MATCHED' OR EXISTS
            (SELECT 1 FROM payment_verifications v WHERE v.payment_id=p.id))
            OR received_refund(NEW.payment_id,NEW.confirmed_by)) THEN
            RAISE EXCEPTION 'verified Stars charge required';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER refund_intent_guard BEFORE INSERT OR UPDATE OR DELETE ON refund_intents "
        "FOR EACH ROW EXECUTE FUNCTION guard_refund_intent()"
    )
    op.execute(
        "CREATE TRIGGER refund_intent_truncate BEFORE TRUNCATE ON refund_intents "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_refund_intent()"
    )
    for table in ["refund_receipts", "payment_verifications"]:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_payment_history()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION guard_payment_history()"
        )
    op.execute("""CREATE FUNCTION guard_payment_verification() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM payments p WHERE p.id=NEW.payment_id
            AND p.currency='XTR' AND p.amount=NEW.amount
            AND (p.payer_id IS NULL OR p.payer_id=NEW.payer_id)) THEN
            RAISE EXCEPTION 'verification must match stored charge';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER payment_verification_guard BEFORE INSERT ON payment_verifications "
        "FOR EACH ROW EXECUTE FUNCTION guard_payment_verification()"
    )
    op.execute("""CREATE FUNCTION check_refund_commit() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (SELECT 1 FROM refund_intents r JOIN payments p ON p.id=r.payment_id
            JOIN orders o ON o.id=p.order_id
            WHERE r.id=NEW.id AND r.status IN ('SENDING','UNCERTAIN','SUCCEEDED')
            AND p.status='MATCHED' AND
            ((r.status='SUCCEEDED' AND o.status<>'REFUNDED') OR EXISTS (
                SELECT 1 FROM fulfillments f WHERE f.payment_id=p.id
                AND (NOT f.hold OR f.refund_intent_id IS DISTINCT FROM r.id)))) THEN
            RAISE EXCEPTION 'refund hold and primary order must commit atomically';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER refund_commit AFTER INSERT OR UPDATE ON refund_intents "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_refund_commit()"
    )
    op.execute("""CREATE FUNCTION guard_refund_hold() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.refund_intent_id IS DISTINCT FROM OLD.refund_intent_id OR
            (OLD.refund_intent_id IS NOT NULL AND NOT NEW.hold) THEN
            IF NOT ((NEW.hold AND EXISTS (SELECT 1 FROM refund_intents r
                WHERE r.id=NEW.refund_intent_id AND r.payment_id=NEW.payment_id
                AND r.status IN ('SENDING','UNCERTAIN','SUCCEEDED'))) OR
                (NOT NEW.hold AND NEW.refund_intent_id IS NULL AND EXISTS
                (SELECT 1 FROM refund_intents r WHERE r.id=OLD.refund_intent_id
                    AND r.status='FAILED'))) THEN
                RAISE EXCEPTION 'refund hold ownership mismatch';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER refund_hold_guard BEFORE UPDATE ON fulfillments FOR EACH ROW "
        "EXECUTE FUNCTION guard_refund_hold()"
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM refund_intents) OR
        EXISTS (SELECT 1 FROM refund_receipts) OR EXISTS (SELECT 1 FROM payment_verifications)
        THEN RAISE EXCEPTION 'refund evidence prevents downgrade'; END IF; END $$""")
    order_constraints(False)
    op.execute("DROP TRIGGER refund_hold_guard ON fulfillments")
    op.execute("DROP FUNCTION guard_refund_hold()")
    for args in reversed(CHANGES):
        rewrite(*args, reverse=True)
    op.execute("DROP FUNCTION refund_authorizes_hold(UUID,UUID)")
    op.execute("DROP FUNCTION refund_confirmed(UUID)")
    op.execute("DROP FUNCTION refund_snapshot(UUID)")
    op.execute("DROP TRIGGER refund_commit ON refund_intents")
    op.execute("DROP FUNCTION check_refund_commit()")
    op.execute("DROP TRIGGER refund_intent_guard ON refund_intents")
    op.execute("DROP TRIGGER refund_intent_truncate ON refund_intents")
    op.execute("DROP FUNCTION guard_refund_intent()")
    op.execute("DROP FUNCTION received_refund(UUID,VARCHAR)")
    op.execute("DROP TRIGGER payment_verification_guard ON payment_verifications")
    op.execute("DROP FUNCTION guard_payment_verification()")
    op.drop_constraint("fk_fulfillments_refund", "fulfillments", type_="foreignkey")
    op.drop_column("fulfillments", "refund_intent_id")
    op.drop_index("ix_fulfillments_payment", "fulfillments")
    for table in ["refund_intents", "refund_receipts", "payment_verifications"]:
        op.drop_table(table)
