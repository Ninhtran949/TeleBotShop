import sqlalchemy as sa

from telebotshop.infrastructure.database.models.history import metadata
from telebotshop.infrastructure.database.models.orders import orders

orders.append_constraint(sa.UniqueConstraint("id", "buyer_id", name="uq_orders_id_buyer"))

support_tickets = sa.Table(
    "support_tickets",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "customer_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_ticket_customer"), nullable=False
    ),
    sa.Column("request_id", sa.Uuid, nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("order_id", sa.Uuid),
    sa.Column("assignee_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_ticket_assignee")),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("closed_at", sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(
        ["order_id", "customer_id"],
        ["orders.id", "orders.buyer_id"],
        name="fk_ticket_order_customer",
    ),
    sa.UniqueConstraint("customer_id", "request_id", name="uq_ticket_request"),
    sa.CheckConstraint("kind IN ('GENERAL','PAYMENT','DELIVERY')", name="ck_ticket_kind"),
    sa.CheckConstraint(
        "state IN ('OPEN','CLOSED') AND (state = 'CLOSED') = (closed_at IS NOT NULL)",
        name="ck_ticket_state",
    ),
    sa.CheckConstraint("version >= 0 AND updated_at >= created_at", name="ck_ticket_version"),
)
sa.Index(
    "ix_ticket_customer_updated",
    support_tickets.c.customer_id,
    support_tickets.c.updated_at,
    support_tickets.c.id,
)
sa.Index(
    "ix_ticket_open_updated",
    support_tickets.c.updated_at,
    support_tickets.c.id,
    postgresql_where=sa.text("state = 'OPEN'"),
)
sa.Index(
    "ix_ticket_assignee_open",
    support_tickets.c.assignee_id,
    support_tickets.c.updated_at,
    support_tickets.c.id,
    postgresql_where=sa.text("state = 'OPEN'"),
)

support_messages = sa.Table(
    "support_messages",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "ticket_id",
        sa.Uuid,
        sa.ForeignKey("support_tickets.id", name="fk_support_message_ticket"),
        nullable=False,
    ),
    sa.Column(
        "author_id",
        sa.Uuid,
        sa.ForeignKey("users.id", name="fk_support_message_author"),
        nullable=False,
    ),
    sa.Column("request_id", sa.Uuid, nullable=False),
    sa.Column("staff", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("key_version", sa.Integer, nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.UniqueConstraint("author_id", "request_id", name="uq_support_message_request"),
    sa.UniqueConstraint("key_version", "nonce", name="uq_support_nonce"),
    sa.CheckConstraint(
        "key_version > 0 AND octet_length(nonce) = 12 "
        "AND octet_length(ciphertext) BETWEEN 17 AND 4256",
        name="ck_support_envelope",
    ),
)
sa.Index(
    "ix_support_message_page",
    support_messages.c.ticket_id,
    support_messages.c.created_at,
    support_messages.c.id,
)

support_actions = sa.Table(
    "support_actions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "actor_id",
        sa.Uuid,
        sa.ForeignKey("users.id", name="fk_support_action_actor"),
        nullable=False,
    ),
    sa.Column(
        "ticket_id",
        sa.Uuid,
        sa.ForeignKey("support_tickets.id", name="fk_support_action_ticket"),
        nullable=False,
    ),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("staff", sa.Boolean, nullable=False),
    sa.Column("target_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_support_action_target")),
    sa.Column("expected_version", sa.Integer, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint(
        "(kind = 'ASSIGN' AND staff) OR (kind = 'CLOSE' AND target_id IS NULL)",
        name="ck_support_action_kind",
    ),
    sa.CheckConstraint(
        "status IN ('pending','applied','cancelled') "
        "AND (status = 'pending') = (completed_at IS NULL)",
        name="ck_support_action_state",
    ),
    sa.CheckConstraint("expected_version >= 0", name="ck_support_action_version"),
)
