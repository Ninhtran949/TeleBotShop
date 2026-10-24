import sqlalchemy as sa

from telebotshop.infrastructure.database.models.identity import metadata

wallet_accounts = sa.Table(
    "wallet_accounts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_wallet_account_user")),
    sa.Column("system_code", sa.String(16)),
    sa.Column("balance", sa.BigInteger, nullable=False, server_default="0"),
    sa.UniqueConstraint("user_id", name="uq_wallet_account_user"),
    sa.UniqueConstraint("system_code", name="uq_wallet_account_system"),
    sa.CheckConstraint(
        "(user_id IS NULL) <> (system_code IS NULL)", name="ck_wallet_account_owner"
    ),
    sa.CheckConstraint(
        "system_code IS NULL OR system_code IN ('CLEARING','SALES','ADJUSTMENT')",
        name="ck_wallet_system",
    ),
    sa.CheckConstraint("user_id IS NULL OR balance >= 0", name="ck_wallet_nonnegative"),
    sa.CheckConstraint(
        "balance BETWEEN -999999999999999 AND 999999999999999", name="ck_wallet_balance_bound"
    ),
)
wallet_journals = sa.Table(
    "wallet_journals",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("operation_key", sa.String(128), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("subject_id", sa.Uuid, nullable=False),
    sa.Column("actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_wallet_journal_actor")),
    sa.Column("reason", sa.String(300), nullable=False),
    sa.Column("evidence", sa.LargeBinary, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("operation_key", name="uq_wallet_operation"),
    sa.CheckConstraint(
        "kind IN ('ADJUSTMENT','TOPUP','PURCHASE','REFUND','REVERSAL')",
        name="ck_wallet_journal_kind",
    ),
    sa.CheckConstraint(
        "octet_length(evidence)=32 AND length(reason) BETWEEN 1 AND 300",
        name="ck_wallet_journal_evidence",
    ),
)
wallet_postings = sa.Table(
    "wallet_postings",
    metadata,
    sa.Column(
        "journal_id",
        sa.Uuid,
        sa.ForeignKey("wallet_journals.id", name="fk_wallet_posting_journal"),
        primary_key=True,
    ),
    sa.Column(
        "account_id",
        sa.Uuid,
        sa.ForeignKey("wallet_accounts.id", name="fk_wallet_posting_account"),
        primary_key=True,
    ),
    sa.Column("delta", sa.BigInteger, nullable=False),
    sa.CheckConstraint(
        "delta<>0 AND delta BETWEEN -999999999999999 AND 999999999999999",
        name="ck_wallet_posting_delta",
    ),
)
wallet_permissions = sa.Table(
    "wallet_permissions",
    metadata,
    sa.Column(
        "user_id",
        sa.Uuid,
        sa.ForeignKey("users.id", name="fk_wallet_permission_user"),
        primary_key=True,
    ),
    sa.Column("permission", sa.String(16), primary_key=True),
    sa.CheckConstraint(
        "permission IN ('ADJUST','REVIEW','REFUND','AUDIT','PRICE')", name="ck_wallet_permission"
    ),
)
