import sqlalchemy as sa

from telebotshop.infrastructure.database.models.manual import metadata
from telebotshop.infrastructure.database.models.orders import orders

resend_actions = sa.Table(
    "resend_actions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_resend_actor"), nullable=False
    ),
    sa.Column(
        "work_id", sa.Uuid, sa.ForeignKey("fulfillments.id", name="fk_resend_work"), nullable=False
    ),
    sa.Column(
        "attempt_id",
        sa.Uuid,
        sa.ForeignKey("delivery_attempts.id", name="fk_resend_attempt"),
        nullable=False,
    ),
    sa.Column("staff", sa.Boolean, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("deadline", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("observed_state", sa.String(16)),
    sa.Column("message_id", sa.BigInteger),
    sa.Column("error_code", sa.String(32)),
    sa.CheckConstraint(
        "status IN ('PENDING','SENDING','SENT','FAILED','UNCERTAIN','CANCELLED')",
        name="ck_resend_status",
    ),
    sa.CheckConstraint(
        "((status IN ('PENDING','CANCELLED') AND started_at IS NULL AND deadline IS NULL) "
        "OR (status IN ('SENDING','SENT','FAILED','UNCERTAIN') AND started_at IS NOT NULL "
        "AND deadline IS NOT NULL AND deadline > started_at)) AND "
        "((status IN ('PENDING','SENDING')) = (completed_at IS NULL))",
        name="ck_resend_times",
    ),
    sa.CheckConstraint(
        "((observed_state IS NULL AND message_id IS NULL) OR "
        "(observed_state = 'SENT' AND message_id > 0) OR "
        "(observed_state IN ('FAILED','UNCERTAIN','RETRYABLE') AND message_id IS NULL)) IS TRUE",
        name="ck_resend_observation",
    ),
    sa.CheckConstraint(
        "((status IN ('PENDING','SENDING','CANCELLED') "
        "AND observed_state IS NULL AND error_code IS NULL) OR "
        "(status = 'SENT' AND observed_state = 'SENT' AND error_code IS NULL) OR "
        "(status = 'FAILED' AND observed_state IN ('FAILED','RETRYABLE') "
        "AND error_code IS NOT NULL) OR "
        "(status = 'UNCERTAIN' AND error_code IS NOT NULL)) IS TRUE",
        name="ck_resend_result",
    ),
)
sa.Index(
    "uq_resend_active",
    resend_actions.c.work_id,
    unique=True,
    postgresql_where=sa.text("status IN ('PENDING','SENDING','UNCERTAIN')"),
)
sa.Index(
    "ix_resend_work_created",
    resend_actions.c.work_id,
    resend_actions.c.created_at,
    resend_actions.c.id,
)
sa.Index("ix_orders_history", orders.c.created_at, orders.c.id)
sa.Index("ix_orders_buyer_history", orders.c.buyer_id, orders.c.created_at, orders.c.id)
