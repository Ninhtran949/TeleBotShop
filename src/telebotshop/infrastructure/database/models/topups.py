import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from telebotshop.infrastructure.database.models.wallet import metadata

deposit_routes = sa.Table(
    "deposit_routes",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "buyer_id",
        sa.Uuid,
        sa.ForeignKey("users.id", name="fk_deposit_route_buyer"),
        nullable=False,
    ),
    sa.Column("network", sa.String(200), nullable=False),
    sa.Column("token_contract", sa.String(200), nullable=False),
    sa.Column("recipient", sa.String(200), nullable=False),
    sa.Column("minimum_confirmations", sa.Integer, nullable=False, server_default="3"),
    sa.UniqueConstraint("buyer_id", name="uq_deposit_route_buyer"),
    sa.UniqueConstraint("network", "token_contract", "recipient", name="uq_deposit_route_address"),
    sa.CheckConstraint(
        "minimum_confirmations BETWEEN 1 AND 1000000", name="ck_deposit_route_finality"
    ),
)
settlement_events = sa.Table(
    "settlement_events",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("provider", sa.String(16), nullable=False),
    sa.Column("transaction_id", sa.String(300), nullable=False),
    sa.Column("facts", JSONB, nullable=False),
    sa.Column("digest", sa.LargeBinary, nullable=False),
    sa.Column("confirmed", sa.Boolean, nullable=False),
    sa.Column("confirmations", sa.Integer, nullable=False),
    sa.Column("required_confirmations", sa.Integer, nullable=False),
    sa.Column("token_verified", sa.Boolean, nullable=False),
    sa.Column("source", sa.String(32), nullable=False),
    sa.Column("proof_digest", sa.LargeBinary, nullable=False),
    sa.Column("conflict", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("state", sa.String(32), nullable=False, server_default="DETECTED"),
    sa.Column("reason", sa.String(64)),
    sa.Column(
        "intent_id",
        sa.Uuid,
        sa.ForeignKey("topup_requests.id", name="fk_settlement_intent", use_alter=True),
    ),
    sa.UniqueConstraint("provider", "transaction_id", name="uq_settlement_identity"),
    sa.CheckConstraint(
        "provider IN ('SEPAY','BINANCE') AND octet_length(digest)=32 "
        "AND octet_length(proof_digest)=32",
        name="ck_settlement_proof",
    ),
    sa.CheckConstraint(
        "state IN ('DETECTED','PENDING_CONFIRMATION','CONFIRMED',"
        "'REVIEW_REQUIRED','CREDITED','REJECTED')",
        name="ck_settlement_state",
    ),
)
topup_requests = sa.Table(
    "topup_requests",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "buyer_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_topup_buyer"), nullable=False
    ),
    sa.Column("action_id", sa.Uuid, nullable=False),
    sa.Column("rail", sa.String(32), nullable=False),
    sa.Column("reference", sa.String(100), nullable=False),
    sa.Column("credit_amount", sa.BigInteger, nullable=False),
    sa.Column("amount", sa.Numeric(36, 18), nullable=False),
    sa.Column("asset", sa.String(16), nullable=False),
    sa.Column("recipient", sa.String(200), nullable=False),
    sa.Column("network", sa.String(200), nullable=False),
    sa.Column("token_contract", sa.String(200), nullable=False),
    sa.Column("route_id", sa.Uuid, sa.ForeignKey("deposit_routes.id", name="fk_topup_route")),
    sa.Column("rate", sa.Numeric(36, 18), nullable=False),
    sa.Column("rate_source", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("state", sa.String(32), nullable=False, server_default="DETECTED"),
    sa.Column("event_id", sa.Uuid, sa.ForeignKey("settlement_events.id", name="fk_topup_event")),
    sa.Column("journal_id", sa.Uuid, sa.ForeignKey("wallet_journals.id", name="fk_topup_journal")),
    sa.UniqueConstraint("buyer_id", "action_id", name="uq_topup_action"),
    sa.UniqueConstraint("reference", name="uq_topup_reference"),
    sa.UniqueConstraint("event_id", name="uq_topup_settlement"),
    sa.UniqueConstraint("journal_id", name="uq_topup_journal"),
    sa.CheckConstraint("rail IN ('BANK_TRANSFER','USDT_BINANCE')", name="ck_topup_rail"),
    sa.CheckConstraint(
        "credit_amount BETWEEN 1 AND 999999999999999 AND amount>0 AND rate>0",
        name="ck_topup_amount",
    ),
    sa.CheckConstraint(
        "expires_at>created_at AND expires_at<=created_at+interval '30 minutes'",
        name="ck_topup_ttl",
    ),
    sa.CheckConstraint(
        "state IN ('DETECTED','PENDING_CONFIRMATION','CONFIRMED',"
        "'REVIEW_REQUIRED','CREDITED','REJECTED')",
        name="ck_topup_state",
    ),
    sa.CheckConstraint(
        "(state='CREDITED')=(journal_id IS NOT NULL) "
        "AND (journal_id IS NULL OR event_id IS NOT NULL)",
        name="ck_topup_credit",
    ),
)
sa.Index(
    "uq_topup_active_route",
    topup_requests.c.route_id,
    unique=True,
    postgresql_where=sa.text("state NOT IN ('CREDITED','REJECTED')"),
)
settlement_observations = sa.Table(
    "settlement_observations",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "event_id",
        sa.Uuid,
        sa.ForeignKey("settlement_events.id", name="fk_settlement_observation"),
        nullable=False,
    ),
    sa.Column("digest", sa.LargeBinary, nullable=False),
    sa.Column("source", sa.String(32), nullable=False),
    sa.Column("conflict", sa.Boolean, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("event_id", "digest", name="uq_settlement_observation"),
)
topup_review_actions = sa.Table(
    "topup_review_actions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_topup_review_actor"), nullable=False
    ),
    sa.Column(
        "event_id",
        sa.Uuid,
        sa.ForeignKey("settlement_events.id", name="fk_topup_review_event"),
        nullable=False,
    ),
    sa.Column(
        "intent_id",
        sa.Uuid,
        sa.ForeignKey("topup_requests.id", name="fk_topup_review_intent"),
    ),
    sa.Column("decision", sa.String(16), nullable=False, server_default="APPROVE"),
    sa.Column("reason", sa.String(300), nullable=False),
    sa.Column("source_digest", sa.LargeBinary, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("applied", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.CheckConstraint(
        "decision IN ('APPROVE','REJECT','ESCALATE') AND "
        "(decision<>'APPROVE' OR intent_id IS NOT NULL)",
        name="ck_topup_review_decision",
    ),
)
reconciliation_checkpoints = sa.Table(
    "reconciliation_checkpoints",
    metadata,
    sa.Column("provider", sa.String(16), primary_key=True),
    sa.Column("completed_until", sa.DateTime(timezone=True), nullable=False),
)
