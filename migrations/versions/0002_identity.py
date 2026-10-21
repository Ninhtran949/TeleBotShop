"""Identity, persisted Owner bootstrap and append-only audit.

Revision ID: 0002_identity
Revises: 0001_runtime
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_identity"
down_revision = "0001_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid, server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger, nullable=False),
        sa.Column("username", sa.String(32)),
        sa.Column("display_name", sa.String(129)),
        sa.Column("language", sa.String(35), nullable=False, server_default="vi"),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
        sa.CheckConstraint(
            "telegram_id > 0 AND telegram_id < 4503599627370496", name="ck_users_telegram_id"
        ),
    )
    roles = op.create_table(
        "roles",
        sa.Column("name", sa.String(16), primary_key=True),
        sa.CheckConstraint("name IN ('OWNER', 'ADMIN', 'SUPPORT')", name="ck_roles_name"),
    )
    op.bulk_insert(roles, [{"name": name} for name in ("OWNER", "ADMIN", "SUPPORT")])
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", name="fk_user_roles_user"),
            primary_key=True,
        ),
        sa.Column(
            "role",
            sa.String(16),
            sa.ForeignKey("roles.name", name="fk_user_roles_role"),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_user_roles_one_owner",
        "user_roles",
        ["role"],
        unique=True,
        postgresql_where=sa.text("role = 'OWNER'"),
    )
    op.create_table(
        "shop_settings",
        sa.Column("id", sa.SmallInteger, primary_key=True),
        sa.Column(
            "owner_user_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_shop_settings_owner")
        ),
        sa.CheckConstraint("id = 1", name="ck_shop_settings_singleton"),
    )
    op.execute("INSERT INTO shop_settings (id) VALUES (1)")
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid, server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "actor_id",
            sa.Uuid,
            sa.ForeignKey("users.id", name="fk_audit_logs_actor"),
            nullable=False,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_id", sa.Uuid, nullable=False),
        sa.Column("correlation_id", sa.Uuid, nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="applied"),
        sa.Column(
            "changes", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.UniqueConstraint("correlation_id", name="uq_audit_logs_correlation"),
        sa.CheckConstraint("outcome = 'applied'", name="ck_audit_logs_outcome"),
    )
    op.execute("""
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Audit is append-only' USING ERRCODE = '55000'; END;
        $$
    """)
    op.execute("""CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_logs
                  FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()""")


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION reject_audit_mutation()")
    op.drop_table("shop_settings")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")
