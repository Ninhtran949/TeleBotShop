import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Uuid, server_default=sa.text("gen_random_uuid()"), primary_key=True),
    sa.Column("telegram_id", sa.BigInteger, nullable=False),
    sa.Column("username", sa.String(32)),
    sa.Column("display_name", sa.String(129)),
    sa.Column("language", sa.String(35), nullable=False, server_default="vi"),
    sa.Column("access_version", sa.Integer, nullable=False, server_default="0"),
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
    sa.CheckConstraint("access_version >= 0", name="ck_users_access_version"),
)
roles = sa.Table(
    "roles",
    metadata,
    sa.Column("name", sa.String(16), primary_key=True),
    sa.CheckConstraint("name IN ('OWNER', 'ADMIN', 'SUPPORT')", name="ck_roles_name"),
)
user_roles = sa.Table(
    "user_roles",
    metadata,
    sa.Column(
        "user_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_user_roles_user"), primary_key=True
    ),
    sa.Column(
        "role",
        sa.String(16),
        sa.ForeignKey("roles.name", name="fk_user_roles_role"),
        nullable=False,
    ),
)
sa.Index(
    "uq_user_roles_one_owner",
    user_roles.c.role,
    unique=True,
    postgresql_where=sa.text("role = 'OWNER'"),
)
shop_settings = sa.Table(
    "shop_settings",
    metadata,
    sa.Column("id", sa.SmallInteger, primary_key=True),
    sa.Column("owner_user_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_shop_settings_owner")),
    sa.Column("support_username", sa.String(32)),
    sa.Column("version", sa.Integer, nullable=False, server_default="0"),
    sa.CheckConstraint("id = 1", name="ck_shop_settings_singleton"),
    sa.CheckConstraint("version >= 0", name="ck_shop_settings_version"),
    sa.CheckConstraint(
        "support_username ~ '^[A-Za-z][A-Za-z0-9_]{4,31}$'",
        name="ck_shop_settings_support_username",
    ),
)
audit_logs = sa.Table(
    "audit_logs",
    metadata,
    sa.Column("id", sa.Uuid, server_default=sa.text("gen_random_uuid()"), primary_key=True),
    sa.Column(
        "actor_id", sa.Uuid, sa.ForeignKey("users.id", name="fk_audit_logs_actor"), nullable=False
    ),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("target_id", sa.Uuid, nullable=False),
    sa.Column("correlation_id", sa.Uuid, nullable=False),
    sa.Column("outcome", sa.String(16), nullable=False, server_default="applied"),
    sa.Column("changes", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("correlation_id", name="uq_audit_logs_correlation"),
    sa.CheckConstraint("outcome = 'applied'", name="ck_audit_logs_outcome"),
)

permissions = sa.Table(
    "permissions",
    metadata,
    sa.Column("name", sa.String(32), primary_key=True),
    sa.CheckConstraint(
        "name IN ('access.manage', 'settings.manage', 'catalog.manage', "
        "'inventory.manage', 'orders.read', 'payments.refund', 'tickets.manage')",
        name="ck_permissions_name",
    ),
)
role_permissions = sa.Table(
    "role_permissions",
    metadata,
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
user_permissions = sa.Table(
    "user_permissions",
    metadata,
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
admin_actions = sa.Table(
    "admin_actions",
    metadata,
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
