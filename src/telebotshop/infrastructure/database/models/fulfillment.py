import sqlalchemy as sa

from telebotshop.infrastructure.database.models.payments import metadata

delivery_attempts = sa.Table(
    "delivery_attempts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "work_id", sa.Uuid, sa.ForeignKey("fulfillments.id", name="fk_attempt_work"), nullable=False
    ),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("sending_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("message_id", sa.BigInteger),
    sa.Column("error_code", sa.String(32)),
    sa.Column("retry_after", sa.Integer),
    sa.CheckConstraint("lease_expires_at > claimed_at", name="ck_attempt_lease"),
    sa.CheckConstraint(
        "sending_at IS NULL OR lease_expires_at > sending_at", name="ck_attempt_send_lease"
    ),
    sa.CheckConstraint(
        "(state = 'CLAIMED' AND sending_at IS NULL AND completed_at IS NULL "
        "AND message_id IS NULL "
        "AND error_code IS NULL AND retry_after IS NULL) OR "
        "(state = 'SENDING' AND sending_at IS NOT NULL AND completed_at IS NULL "
        "AND message_id IS NULL "
        "AND error_code IS NULL AND retry_after IS NULL) OR "
        "(state = 'SENT' AND sending_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND message_id > 0 "
        "AND message_id IS NOT NULL AND error_code IS NULL AND retry_after IS NULL) OR "
        "(state IN ('RETRYABLE', 'UNCERTAIN', 'FAILED', 'ABANDONED') AND completed_at IS NOT NULL "
        "AND message_id IS NULL AND error_code IS NOT NULL AND "
        "((state = 'RETRYABLE' AND sending_at IS NOT NULL AND retry_after BETWEEN 1 AND 86400 "
        "AND retry_after IS NOT NULL) OR (state <> 'RETRYABLE' AND retry_after IS NULL)))",
        name="ck_attempt_state",
    ),
    sa.CheckConstraint(
        "error_code IS NULL OR error_code IN ('RATE_LIMIT', 'REJECTED', 'NETWORK', "
        "'LEASE_EXPIRED', "
        "'INVALID_ENTITLEMENT', 'CRYPTO', 'RETRY_EXHAUSTED', 'HELD')",
        name="ck_attempt_code",
    ),
    sa.CheckConstraint(
        "(sending_at IS NULL OR sending_at >= claimed_at) AND "
        "(completed_at IS NULL OR completed_at >= COALESCE(sending_at, claimed_at))",
        name="ck_attempt_timestamps",
    ),
)
sa.Index(
    "ix_attempt_expiry",
    delivery_attempts.c.lease_expires_at,
    postgresql_where=sa.text("state IN ('CLAIMED', 'SENDING')"),
)
sa.Index("ix_delivery_attempt_work", delivery_attempts.c.work_id)

delivery_observations = sa.Table(
    "delivery_observations",
    metadata,
    sa.Column(
        "attempt_id",
        sa.Uuid,
        sa.ForeignKey("delivery_attempts.id", name="fk_observation_attempt"),
        primary_key=True,
    ),
    sa.Column("digest", sa.LargeBinary, primary_key=True),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("message_id", sa.BigInteger),
    sa.Column("error_code", sa.String(32)),
    sa.Column("retry_after", sa.Integer),
    sa.Column(
        "observed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.CheckConstraint("octet_length(digest) = 32", name="ck_delivery_observation_digest"),
    sa.CheckConstraint(
        "state IN ('SENT', 'RETRYABLE', 'UNCERTAIN', 'FAILED')",
        name="ck_delivery_observation_state",
    ),
    sa.CheckConstraint(
        "(state = 'SENT' AND message_id IS NOT NULL AND message_id > 0 AND error_code IS NULL "
        "AND retry_after IS NULL) OR (state = 'RETRYABLE' AND message_id IS NULL "
        "AND error_code = 'RATE_LIMIT' AND error_code IS NOT NULL AND retry_after IS NOT NULL "
        "AND retry_after BETWEEN 1 AND 86400) OR (state IN ('UNCERTAIN', 'FAILED') "
        "AND message_id IS NULL AND retry_after IS NULL AND error_code IS NOT NULL "
        "AND error_code IN ('RATE_LIMIT', 'REJECTED', 'NETWORK', 'LEASE_EXPIRED', "
        "'INVALID_ENTITLEMENT', 'CRYPTO', 'RETRY_EXHAUSTED', 'HELD'))",
        name="ck_delivery_observation_result",
    ),
)
