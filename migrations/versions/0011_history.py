"""Durable resend actions and bounded order history indexes."""

from alembic import op

revision = "0011_history"
down_revision = "0010_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE resend_actions (
        id UUID PRIMARY KEY,
        actor_id UUID NOT NULL CONSTRAINT fk_resend_actor REFERENCES users(id),
        work_id UUID NOT NULL CONSTRAINT fk_resend_work REFERENCES fulfillments(id),
        attempt_id UUID NOT NULL CONSTRAINT fk_resend_attempt REFERENCES delivery_attempts(id),
        staff BOOLEAN NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        started_at TIMESTAMPTZ, deadline TIMESTAMPTZ, completed_at TIMESTAMPTZ,
        observed_state VARCHAR(16), message_id BIGINT, error_code VARCHAR(32),
        CONSTRAINT ck_resend_status CHECK
            (status IN ('PENDING','SENDING','SENT','FAILED','UNCERTAIN','CANCELLED')),
        CONSTRAINT ck_resend_times CHECK (
            ((status IN ('PENDING','CANCELLED') AND started_at IS NULL AND deadline IS NULL)
            OR (status IN ('SENDING','SENT','FAILED','UNCERTAIN') AND started_at IS NOT NULL
                AND deadline IS NOT NULL AND deadline > started_at))
            AND ((status IN ('PENDING','SENDING')) = (completed_at IS NULL))),
        CONSTRAINT ck_resend_observation CHECK ((
            (observed_state IS NULL AND message_id IS NULL) OR
            (observed_state = 'SENT' AND message_id > 0) OR
            (observed_state IN ('FAILED','UNCERTAIN','RETRYABLE') AND message_id IS NULL)) IS TRUE),
        CONSTRAINT ck_resend_result CHECK ((
            (status IN ('PENDING','SENDING','CANCELLED')
                AND observed_state IS NULL AND error_code IS NULL) OR
            (status = 'SENT' AND observed_state = 'SENT' AND error_code IS NULL) OR
            (status = 'FAILED' AND observed_state IN ('FAILED','RETRYABLE')
                AND error_code IS NOT NULL) OR
            (status = 'UNCERTAIN' AND error_code IS NOT NULL)) IS TRUE)
    )""")
    op.execute(
        "CREATE UNIQUE INDEX uq_resend_active ON resend_actions(work_id) "
        "WHERE status IN ('PENDING','SENDING','UNCERTAIN')"
    )
    op.execute("CREATE INDEX ix_resend_work_created ON resend_actions(work_id, created_at, id)")
    op.execute("CREATE INDEX ix_orders_history ON orders(created_at, id)")
    op.execute("CREATE INDEX ix_orders_buyer_history ON orders(buyer_id, created_at, id)")
    op.execute("""CREATE FUNCTION guard_resend_action() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN
            RAISE EXCEPTION 'resend history is immutable';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'PENDING' OR NEW.expires_at <= clock_timestamp() THEN
                RAISE EXCEPTION 'invalid resend confirmation';
            END IF;
        ELSE
            IF (to_jsonb(NEW) - ARRAY['status','started_at','deadline','completed_at',
                'observed_state','message_id','error_code']) IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['status','started_at','deadline','completed_at',
                'observed_state','message_id','error_code']) THEN
                RAISE EXCEPTION 'resend identity is immutable';
            END IF;
            IF NOT ((OLD.status = 'PENDING' AND NEW.status IN ('SENDING','CANCELLED')) OR
                    (OLD.status = 'SENDING' AND NEW.status IN ('SENT','FAILED','UNCERTAIN')) OR
                    (OLD.status = 'UNCERTAIN' AND NEW.status = 'UNCERTAIN'
                        AND OLD.observed_state IS NULL AND NEW.observed_state IS NOT NULL
                        AND (to_jsonb(NEW) - ARRAY['observed_state','message_id']) =
                            (to_jsonb(OLD) - ARRAY['observed_state','message_id']))) THEN
                RAISE EXCEPTION 'invalid resend transition';
            END IF;
            IF OLD.status <> 'PENDING' AND
                ROW(NEW.started_at,NEW.deadline) IS DISTINCT FROM
                ROW(OLD.started_at,OLD.deadline) THEN
                RAISE EXCEPTION 'resend send window is immutable';
            END IF;
        END IF;
        IF TG_OP = 'INSERT' OR NEW.status = 'SENDING' THEN
            PERFORM 1 FROM fulfillments WHERE id = NEW.work_id FOR UPDATE;
            IF NOT EXISTS (SELECT 1 FROM fulfillments f JOIN orders o ON o.id = f.order_id
                JOIN delivery_attempts a ON a.id = f.active_attempt_id
                WHERE f.id = NEW.work_id AND f.active_attempt_id = NEW.attempt_id
                    AND a.work_id = f.id AND a.state = 'SENT'
                    AND f.state = 'SENT' AND o.status = 'DELIVERED' AND NOT f.hold
                    AND f.product_type <> 'MANUAL_FULFILLMENT'
                    AND (NEW.staff OR NEW.actor_id = f.buyer_id)
                    AND fulfillment_entitlement_matches(f)) THEN
                RAISE EXCEPTION 'delivered entitlement required';
            END IF;
            IF NEW.expires_at <= clock_timestamp() THEN
                RAISE EXCEPTION 'resend confirmation expired';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER resend_action_guard BEFORE INSERT OR UPDATE OR DELETE ON "
        "resend_actions FOR EACH ROW EXECUTE FUNCTION guard_resend_action()"
    )
    op.execute(
        "CREATE TRIGGER resend_action_truncate_guard BEFORE TRUNCATE ON resend_actions "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_resend_action()"
    )
    op.execute("""CREATE FUNCTION guard_resend_hold() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.hold AND NOT OLD.hold AND EXISTS (SELECT 1 FROM resend_actions r
            WHERE r.work_id = NEW.id AND r.status IN ('SENDING','UNCERTAIN')) THEN
            RAISE EXCEPTION 'resend in flight or uncertain requires reconciliation';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER resend_hold_guard BEFORE UPDATE OF hold ON fulfillments "
        "FOR EACH ROW EXECUTE FUNCTION guard_resend_hold()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM resend_actions) THEN
            RAISE EXCEPTION 'resend history prevents downgrade';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER resend_hold_guard ON fulfillments")
    op.execute("DROP FUNCTION guard_resend_hold()")
    op.execute("DROP TABLE resend_actions")
    op.execute("DROP FUNCTION guard_resend_action()")
    op.execute("DROP INDEX ix_orders_buyer_history")
    op.execute("DROP INDEX ix_orders_history")
