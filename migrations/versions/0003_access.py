"""Explicit staff grants, non-secret settings and durable admin confirmations."""

import sqlalchemy as sa
from alembic import op

revision = "0003_access"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("access_version", sa.Integer, nullable=False, server_default="0")
    )
    op.create_check_constraint("ck_users_access_version", "users", "access_version >= 0")
    op.add_column("shop_settings", sa.Column("support_username", sa.String(32)))
    op.add_column(
        "shop_settings", sa.Column("version", sa.Integer, nullable=False, server_default="0")
    )
    op.create_check_constraint("ck_shop_settings_version", "shop_settings", "version >= 0")
    op.create_check_constraint(
        "ck_shop_settings_support_username",
        "shop_settings",
        "support_username ~ '^[A-Za-z][A-Za-z0-9_]{4,31}$'",
    )
    names = (
        "access.manage",
        "settings.manage",
        "catalog.manage",
        "inventory.manage",
        "orders.read",
        "payments.refund",
        "tickets.manage",
    )
    permissions = op.create_table(
        "permissions",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.CheckConstraint(
            "name IN ('access.manage', 'settings.manage', 'catalog.manage', "
            "'inventory.manage', 'orders.read', 'payments.refund', 'tickets.manage')",
            name="ck_permissions_name",
        ),
    )
    op.bulk_insert(permissions, [{"name": name} for name in names])
    role_permissions = op.create_table(
        "role_permissions",
        sa.Column(
            "role",
            sa.String(16),
            sa.ForeignKey("roles.name", name="fk_role_permissions_role"),
            primary_key=True,
        ),
        sa.Column(
            "permission",
            sa.String(32),
            sa.ForeignKey("permissions.name", name="fk_role_permissions_permission"),
            primary_key=True,
        ),
    )
    grants = {"OWNER": names, "ADMIN": names[2:], "SUPPORT": ("tickets.manage",)}
    op.bulk_insert(
        role_permissions,
        [
            {"role": role, "permission": permission}
            for role, permitted in grants.items()
            for permission in permitted
        ],
    )
    op.create_table(
        "user_permissions",
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("users.id", name="fk_user_permissions_user"),
            primary_key=True,
        ),
        sa.Column(
            "permission",
            sa.String(32),
            sa.ForeignKey("permissions.name", name="fk_user_permissions_permission"),
            primary_key=True,
        ),
    )
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "actor_id",
            sa.Uuid,
            sa.ForeignKey("users.id", name="fk_admin_actions_actor"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_admin_actions_target")),
        sa.Column("role", sa.String(16), sa.ForeignKey("roles.name", name="fk_admin_actions_role")),
        sa.Column(
            "permission",
            sa.String(32),
            sa.ForeignKey("permissions.name", name="fk_admin_actions_permission"),
        ),
        sa.Column("value", sa.String(32)),
        sa.Column("expected_version", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("expected_version >= 0", name="ck_admin_actions_version"),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'cancelled')", name="ck_admin_actions_status"
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (completed_at IS NULL)", name="ck_admin_actions_completion"
        ),
        sa.CheckConstraint(
            "(kind IN ('permission.grant', 'permission.revoke') AND target_id IS NOT NULL "
            "AND role IS NOT NULL AND role IN ('ADMIN', 'SUPPORT') AND permission IS NOT NULL "
            "AND value IS NULL) OR (kind = 'settings.support' AND target_id IS NULL "
            "AND role IS NULL AND permission IS NULL "
            "AND (value IS NULL OR value ~ '^[A-Za-z][A-Za-z0-9_]{4,31}$'))",
            name="ck_admin_actions_shape",
        ),
    )


def downgrade() -> None:
    op.drop_table("admin_actions")
    op.drop_table("user_permissions")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_constraint("ck_shop_settings_support_username", "shop_settings")
    op.drop_constraint("ck_shop_settings_version", "shop_settings")
    op.drop_column("shop_settings", "version")
    op.drop_column("shop_settings", "support_username")
    op.drop_constraint("ck_users_access_version", "users")
    op.drop_column("users", "access_version")
