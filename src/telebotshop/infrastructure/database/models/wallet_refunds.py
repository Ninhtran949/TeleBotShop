import sqlalchemy as sa

from telebotshop.infrastructure.database.models.wallet import metadata

wallet_refund_actions = sa.Table(
    "wallet_refund_actions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "order_id",
        sa.Uuid,
        sa.ForeignKey("orders.id", name="fk_wallet_refund_order"),
        nullable=False,
    ),
    sa.Column(
        "actor_id",
        sa.Uuid,
        sa.ForeignKey("users.id", name="fk_wallet_refund_actor"),
        nullable=False,
    ),
    sa.Column("reason", sa.String(300), nullable=False),
    sa.Column("source_digest", sa.LargeBinary, nullable=False),
    sa.Column("expected", sa.LargeBinary, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "journal_id", sa.Uuid, sa.ForeignKey("wallet_journals.id", name="fk_wallet_refund_journal")
    ),
    sa.UniqueConstraint("journal_id", name="uq_wallet_refund_journal"),
    sa.CheckConstraint(
        "octet_length(source_digest)=32 AND octet_length(expected)=32",
        name="ck_wallet_refund_evidence",
    ),
)
sa.Index(
    "uq_wallet_refund_order",
    wallet_refund_actions.c.order_id,
    unique=True,
    postgresql_where=sa.text("journal_id IS NOT NULL"),
)
topup_reversals = sa.Table(
    "topup_reversals",
    metadata,
    sa.Column(
        "topup_id",
        sa.Uuid,
        sa.ForeignKey("topup_requests.id", name="fk_reversal_topup"),
        primary_key=True,
    ),
    sa.Column(
        "journal_id",
        sa.Uuid,
        sa.ForeignKey("wallet_journals.id", name="fk_reversal_journal"),
        nullable=False,
    ),
    sa.Column(
        "actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_reversal_actor"), nullable=False
    ),
    sa.Column("reason", sa.String(300), nullable=False),
    sa.Column("evidence", sa.LargeBinary, nullable=False),
    sa.UniqueConstraint("journal_id", name="uq_topup_reversal_journal"),
)

external_refund_records = sa.Table(
    "external_refund_records",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "topup_id",
        sa.Uuid,
        sa.ForeignKey("topup_requests.id", name="fk_cash_refund_topup"),
        nullable=False,
    ),
    sa.Column(
        "actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_cash_refund_actor"), nullable=False
    ),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("reason", sa.String(300), nullable=False),
    sa.Column("source_digest", sa.LargeBinary, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    ),
    sa.CheckConstraint(
        "state IN ('REQUESTED','UNCERTAIN','CONFIRMED','REJECTED')", name="ck_cash_refund_state"
    ),
    sa.CheckConstraint("octet_length(source_digest)=32", name="ck_cash_refund_source"),
    sa.UniqueConstraint("topup_id", "state", name="uq_cash_refund_state"),
)
