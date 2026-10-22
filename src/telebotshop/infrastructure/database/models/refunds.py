import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from telebotshop.infrastructure.database.models.support import metadata

payment_verifications = sa.Table(
    "payment_verifications",
    metadata,
    sa.Column("payment_id", sa.Uuid, sa.ForeignKey("payments.id"), primary_key=True),
    sa.Column("actor_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False),
    sa.Column("payer_id", sa.BigInteger, nullable=False),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("transaction_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "verified_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.CheckConstraint("payer_id > 0 AND amount > 0", name="ck_payment_verification_amount"),
)

refund_receipts = sa.Table(
    "refund_receipts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("charge_id", sa.String(1024), nullable=False),
    sa.Column("currency", sa.String(16), nullable=False),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("payload", sa.String(128), nullable=False),
    sa.Column("provider_charge_id", sa.String(1024), nullable=False),
    sa.Column("source", sa.String(16), nullable=False),
    sa.Column("digest", sa.LargeBinary, nullable=False),
    sa.Column(
        "received_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("charge_id", "digest", name="uq_refund_receipt"),
    sa.CheckConstraint(
        "octet_length(charge_id) BETWEEN 1 AND 1024 AND amount > 0 AND octet_length(digest) = 32",
        name="ck_refund_receipt",
    ),
    sa.CheckConstraint("source IN ('EVENT','TRANSACTION')", name="ck_refund_receipt_source"),
)

refund_intents = sa.Table(
    "refund_intents",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("payment_id", sa.Uuid, sa.ForeignKey("payments.id"), nullable=False),
    sa.Column("actor_id", sa.Uuid, sa.ForeignKey("users.id")),
    sa.Column("expected", JSONB, nullable=False),
    sa.Column("accept_uncertain", sa.Boolean, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("deadline", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("confirmed_by", sa.String(16)),
    sa.Column("observed_state", sa.String(16)),
    sa.Column("key_version", sa.Integer),
    sa.Column("nonce", sa.LargeBinary),
    sa.Column("ciphertext", sa.LargeBinary),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.CheckConstraint(
        "status IN ('PENDING','SENDING','SUCCEEDED','FAILED','UNCERTAIN','CANCELLED')",
        name="ck_refund_status",
    ),
    sa.CheckConstraint(
        "((status IN ('PENDING','CANCELLED') AND started_at IS NULL AND deadline IS NULL) OR "
        "(status IN ('SENDING','FAILED','UNCERTAIN','SUCCEEDED') AND started_at IS NOT NULL "
        "AND deadline IS NOT NULL AND deadline > started_at) OR "
        "(actor_id IS NULL AND status='SUCCEEDED' AND started_at IS NULL "
        "AND deadline IS NULL)) IS TRUE",
        name="ck_refund_window",
    ),
    sa.CheckConstraint(
        "(completed_at IS NULL) = (status IN ('PENDING','SENDING'))", name="ck_refund_completed"
    ),
    sa.CheckConstraint(
        "((status='SUCCEEDED' AND confirmed_by IN "
        "('API','EVENT','TRANSACTION')) OR (status<>'SUCCEEDED' AND "
        "confirmed_by IS NULL)) IS TRUE",
        name="ck_refund_confirmation",
    ),
    sa.CheckConstraint(
        "observed_state IS NULL OR observed_state IN ('SUCCEEDED','FAILED','UNCERTAIN')",
        name="ck_refund_observed",
    ),
    sa.CheckConstraint("jsonb_typeof(expected)='object'", name="ck_refund_expected"),
    sa.CheckConstraint(
        "((actor_id IS NULL AND status='SUCCEEDED' AND key_version IS "
        "NULL AND nonce IS NULL AND ciphertext IS NULL) OR (actor_id IS "
        "NOT NULL AND key_version > 0 AND octet_length(nonce)=12 AND "
        "octet_length(ciphertext) BETWEEN 17 AND 3200)) IS TRUE",
        name="ck_refund_evidence",
    ),
    sa.UniqueConstraint("key_version", "nonce", name="uq_refund_nonce"),
)
sa.Index(
    "uq_refund_active_payment",
    refund_intents.c.payment_id,
    unique=True,
    postgresql_where=sa.text("status IN ('PENDING','SENDING','UNCERTAIN','SUCCEEDED')"),
)
sa.Index(
    "ix_refund_queue", refund_intents.c.status, refund_intents.c.created_at, refund_intents.c.id
)
sa.Index(
    "ix_refund_payment_history",
    refund_intents.c.payment_id,
    refund_intents.c.created_at,
    refund_intents.c.id,
)
