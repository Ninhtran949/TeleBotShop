"""Durable metadata-only operational notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0014_notifications"
down_revision = "0013_refunds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE notifications (
	id UUID NOT NULL,
	kind VARCHAR(32) NOT NULL,
	target_id UUID NOT NULL,
	recipient_id UUID NOT NULL,
	revision VARCHAR(64) NOT NULL,
	state VARCHAR(16) DEFAULT 'READY' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	deadline TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	observed_state VARCHAR(16),
	observed_at TIMESTAMP WITH TIME ZONE,
	message_id BIGINT,
	PRIMARY KEY (id),
	CONSTRAINT uq_notification_event UNIQUE (kind, target_id, recipient_id, revision),
	CONSTRAINT ck_notification_kind CHECK (kind IN (
        'PAYMENT_REVIEW','INVOICE_UNCERTAIN','PAID_ATTENTION','DELIVERY_ATTENTION',
        'MANUAL_WAITING',
        'BACKLOG','LOW_STOCK','REFUND_UNCERTAIN','TICKET_ACTIVITY','TICKET_REPLY')),
	CONSTRAINT ck_notification_revision CHECK (revision ~ '^[A-Za-z0-9:_-]{1,64}$'),
	CONSTRAINT ck_notification_state CHECK (
        state IN ('READY','SENDING','SENT','FAILED','UNCERTAIN','SUPPRESSED')),
	CONSTRAINT ck_notification_window CHECK (
        (state IN ('READY','SUPPRESSED') AND started_at IS NULL AND deadline IS NULL) OR
        (state IN ('SENDING','SENT','FAILED','UNCERTAIN') AND started_at IS NOT NULL
        AND deadline IS NOT NULL AND deadline > started_at
        AND deadline <= started_at + interval '60 seconds')),
	CONSTRAINT ck_notification_completion CHECK (
        (state IN ('READY','SENDING')) = (finished_at IS NULL)),
	CONSTRAINT ck_notification_observed_state CHECK (
        observed_state IS NULL OR observed_state IN ('SENT','FAILED','UNCERTAIN')),
	CONSTRAINT ck_notification_observation CHECK (
        (observed_state IS NULL) = (observed_at IS NULL)
        AND ((observed_state='SENT') IS TRUE) = (message_id IS NOT NULL)
        AND (message_id IS NULL OR message_id > 0)),
	CONSTRAINT ck_notification_outcome CHECK (
        (state IN ('READY','SENDING','SUPPRESSED') AND observed_state IS NULL) OR
        (state='SENT' AND observed_state IS NOT NULL AND observed_state='SENT') OR
        (state='FAILED' AND observed_state IS NOT NULL AND observed_state='FAILED') OR
        state='UNCERTAIN'),
	CONSTRAINT fk_notification_recipient FOREIGN KEY(recipient_id) REFERENCES users (id)
)
        """
    )
    op.execute(
        """
CREATE INDEX ix_notification_expiry ON notifications (deadline) WHERE state = 'SENDING'
        """
    )
    op.execute(
        """
CREATE INDEX ix_notification_ready ON notifications (created_at, id) WHERE state = 'READY'
        """
    )
    op.execute(
        """
CREATE FUNCTION guard_notification() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('DELETE','TRUNCATE') THEN
    RAISE EXCEPTION 'notification history is immutable';
  END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'READY' THEN RAISE EXCEPTION 'notification must start ready'; END IF;
    RETURN NEW;
  END IF;
  IF ROW(OLD.id,OLD.kind,OLD.target_id,OLD.recipient_id,OLD.revision,OLD.created_at)
     IS DISTINCT FROM
     ROW(NEW.id,NEW.kind,NEW.target_id,NEW.recipient_id,NEW.revision,NEW.created_at)
  THEN RAISE EXCEPTION 'notification identity is immutable'; END IF;
  IF OLD.state<>'READY' AND
     ROW(OLD.started_at,OLD.deadline) IS DISTINCT FROM ROW(NEW.started_at,NEW.deadline)
  THEN RAISE EXCEPTION 'notification window is immutable'; END IF;
  IF OLD.finished_at IS NOT NULL AND OLD.finished_at IS DISTINCT FROM NEW.finished_at
  THEN RAISE EXCEPTION 'notification completion is immutable'; END IF;
  IF (OLD.observed_state IS NOT NULL OR OLD.state='SUPPRESSED') AND NEW IS DISTINCT FROM OLD
  THEN RAISE EXCEPTION 'notification result is immutable'; END IF;
  IF OLD.state='READY' AND NEW.state NOT IN ('SENDING','SUPPRESSED')
  THEN RAISE EXCEPTION 'invalid notification begin'; END IF;
  IF OLD.state='SENDING' AND NEW.state NOT IN ('SENT','FAILED','UNCERTAIN')
  THEN RAISE EXCEPTION 'invalid notification result'; END IF;
  IF OLD.state='UNCERTAIN' AND NEW.state<>'UNCERTAIN'
  THEN RAISE EXCEPTION 'uncertain notification cannot retry'; END IF;
  RETURN NEW;END $$;
        """
    )
    op.execute(
        """
CREATE TRIGGER notification_guard BEFORE INSERT OR UPDATE OR DELETE ON notifications
FOR EACH ROW EXECUTE FUNCTION guard_notification()
        """
    )
    op.execute(
        """
CREATE TRIGGER notification_truncate_guard BEFORE TRUNCATE ON notifications
FOR EACH STATEMENT EXECUTE FUNCTION guard_notification()
        """
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM notifications)")):
        raise RuntimeError("Cannot downgrade notification history")
    op.drop_table("notifications")
    op.execute("DROP FUNCTION guard_notification()")
