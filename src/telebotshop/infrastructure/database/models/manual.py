import sqlalchemy as sa

from telebotshop.infrastructure.database.models.resources import metadata

manual_actions = sa.Table(
    "manual_actions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_manual_actor"), nullable=False
    ),
    sa.Column(
        "work_id", sa.Uuid, sa.ForeignKey("fulfillments.id", name="fk_manual_work"), nullable=False
    ),
    sa.Column(
        "attempt_id",
        sa.Uuid,
        sa.ForeignKey("delivery_attempts.id", name="fk_manual_attempt"),
        nullable=False,
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("key_version", sa.Integer, nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.CheckConstraint("status IN ('pending', 'applied', 'cancelled')", name="ck_manual_status"),
    sa.CheckConstraint(
        "(status = 'pending') = (completed_at IS NULL)", name="ck_manual_completion"
    ),
    sa.CheckConstraint(
        "key_version > 0 AND octet_length(nonce) = 12 "
        "AND octet_length(ciphertext) BETWEEN 17 AND 8320",
        name="ck_manual_envelope",
    ),
    sa.UniqueConstraint("key_version", "nonce", name="uq_manual_nonce"),
)
sa.Index(
    "uq_manual_completed",
    manual_actions.c.work_id,
    unique=True,
    postgresql_where=sa.text("status = 'applied'"),
)
