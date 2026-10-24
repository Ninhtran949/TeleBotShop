import sqlalchemy as sa

from telebotshop.infrastructure.database.models.refunds import metadata

notifications = sa.Table(
    "notifications",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("target_id", sa.Uuid, nullable=False),
    sa.Column(
        "recipient_id",
        sa.Uuid,
        sa.ForeignKey("users.id", name="fk_notification_recipient"),
        nullable=False,
    ),
    sa.Column("revision", sa.String(64), nullable=False),
    sa.Column("state", sa.String(16), nullable=False, server_default="READY"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("deadline", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("observed_state", sa.String(16)),
    sa.Column("observed_at", sa.DateTime(timezone=True)),
    sa.Column("message_id", sa.BigInteger),
    sa.UniqueConstraint(
        "kind", "target_id", "recipient_id", "revision", name="uq_notification_event"
    ),
    sa.CheckConstraint(
        "kind IN ('PAYMENT_REVIEW','INVOICE_UNCERTAIN','PAID_ATTENTION','DELIVERY_ATTENTION',"
        "'MANUAL_WAITING','BACKLOG','LOW_STOCK','REFUND_UNCERTAIN',"
        "'TICKET_ACTIVITY','TICKET_REPLY','WALLET_TOPUP','TOPUP_REVIEW')",
        name="ck_notification_kind",
    ),
    sa.CheckConstraint("revision ~ '^[A-Za-z0-9:_-]{1,64}$'", name="ck_notification_revision"),
    sa.CheckConstraint(
        "state IN ('READY','SENDING','SENT','FAILED','UNCERTAIN','SUPPRESSED')",
        name="ck_notification_state",
    ),
    sa.CheckConstraint(
        "(state IN ('READY','SUPPRESSED') AND started_at IS NULL AND deadline IS NULL) OR "
        "(state IN ('SENDING','SENT','FAILED','UNCERTAIN') AND started_at IS NOT NULL "
        "AND deadline IS NOT NULL AND deadline > started_at "
        "AND deadline <= started_at + interval '60 seconds')",
        name="ck_notification_window",
    ),
    sa.CheckConstraint(
        "(state IN ('READY','SENDING')) = (finished_at IS NULL)", name="ck_notification_completion"
    ),
    sa.CheckConstraint(
        "observed_state IS NULL OR observed_state IN ('SENT','FAILED','UNCERTAIN')",
        name="ck_notification_observed_state",
    ),
    sa.CheckConstraint(
        "(observed_state IS NULL) = (observed_at IS NULL) "
        "AND ((observed_state='SENT') IS TRUE) = (message_id IS NOT NULL) "
        "AND (message_id IS NULL OR message_id > 0)",
        name="ck_notification_observation",
    ),
    sa.CheckConstraint(
        "(state IN ('READY','SENDING','SUPPRESSED') AND observed_state IS NULL) OR "
        "(state='SENT' AND observed_state IS NOT NULL AND observed_state='SENT') OR "
        "(state='FAILED' AND observed_state IS NOT NULL AND observed_state='FAILED') OR "
        "state='UNCERTAIN'",
        name="ck_notification_outcome",
    ),
)
sa.Index(
    "ix_notification_ready",
    notifications.c.created_at,
    notifications.c.id,
    postgresql_where=sa.text("state = 'READY'"),
)
sa.Index(
    "ix_notification_expiry",
    notifications.c.deadline,
    postgresql_where=sa.text("state = 'SENDING'"),
)
