from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.access import AccessRepository
from telebotshop.domain.models.access import ROLE_PERMISSIONS, Access, Permission
from telebotshop.domain.models.admin import ActionKind, ActionStatus, AdminAction, ShopSettings
from telebotshop.domain.models.identity import Role
from telebotshop.infrastructure.database.models.identity import (
    admin_actions,
    audit_logs,
    role_permissions,
    shop_settings,
    user_permissions,
    user_roles,
    users,
)


class SqlAccessRepository(AccessRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock(self, *, exclusive: bool = False) -> ShopSettings:
        # shortcut: serialize access/settings writes; narrow locks when contention warrants it.
        row = (
            (
                await self.session.execute(
                    select(
                        shop_settings.c.owner_user_id,
                        shop_settings.c.support_username,
                        shop_settings.c.version,
                    )
                    .where(shop_settings.c.id == 1)
                    .with_for_update(read=not exclusive)
                )
            )
            .mappings()
            .one()
        )
        return ShopSettings(**row)

    async def get(self, telegram_id: int) -> Access | None:
        return await self._get(users.c.telegram_id == telegram_id)

    async def get_by_id(self, user_id: UUID) -> Access | None:
        return await self._get(users.c.id == user_id)

    async def _get(self, criterion: ColumnElement[bool]) -> Access | None:
        row = (
            (
                await self.session.execute(
                    select(
                        users.c.id, users.c.telegram_id, users.c.access_version, user_roles.c.role
                    )
                    .select_from(users.outerjoin(user_roles, users.c.id == user_roles.c.user_id))
                    .where(criterion)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        role = None if row["role"] is None else Role(row["role"])
        permitted: frozenset[Permission] = frozenset()
        if role is not None:
            statement = select(role_permissions.c.permission).where(role_permissions.c.role == role)
            if role == Role.OWNER:
                persisted_owner = await self.session.scalar(select(shop_settings.c.owner_user_id))
                if persisted_owner != row["id"]:
                    return Access(
                        row["id"], row["telegram_id"], None, permitted, row["access_version"]
                    )
            else:
                statement = statement.where(
                    role_permissions.c.permission.in_(
                        select(user_permissions.c.permission).where(
                            user_permissions.c.user_id == row["id"]
                        )
                    )
                )
            permitted = frozenset(
                Permission(name) for name in await self.session.scalars(statement)
            )
            permitted &= ROLE_PERMISSIONS[role]
        return Access(row["id"], row["telegram_id"], role, permitted, row["access_version"])

    async def save_action(self, action: AdminAction) -> None:
        # shortcut: retain expired actions; add cleanup after Owner approves a retention policy.
        await self.session.execute(
            insert(admin_actions).values(
                id=action.id,
                actor_id=action.actor_id,
                kind=action.kind,
                target_id=action.target_id,
                role=action.role,
                permission=action.permission,
                value=action.value,
                expected_version=action.expected_version,
                expires_at=action.expires_at,
            )
        )

    async def get_action(self, action_id: UUID) -> AdminAction | None:
        row = (
            (
                await self.session.execute(
                    select(admin_actions).where(admin_actions.c.id == action_id).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return AdminAction(
            row["id"],
            row["actor_id"],
            ActionKind(row["kind"]),
            row["target_id"],
            None if row["role"] is None else Role(row["role"]),
            None if row["permission"] is None else Permission(row["permission"]),
            row["value"],
            row["expected_version"],
            row["expires_at"],
            ActionStatus(row["status"]),
        )

    async def set_permission(
        self, target_id: UUID, role: Role, permission: Permission, *, grant: bool
    ) -> None:
        if grant:
            await self.session.execute(
                pg_insert(user_roles)
                .values(user_id=target_id, role=role)
                .on_conflict_do_update(index_elements=["user_id"], set_={"role": role})
            )
            await self.session.execute(
                insert(user_permissions).values(user_id=target_id, permission=permission)
            )
        else:
            await self.session.execute(
                delete(user_permissions).where(
                    user_permissions.c.user_id == target_id,
                    user_permissions.c.permission == permission,
                )
            )
        await self.session.execute(
            update(users)
            .where(users.c.id == target_id)
            .values(access_version=users.c.access_version + 1)
        )

    async def set_support_username(self, value: str | None) -> None:
        await self.session.execute(
            update(shop_settings)
            .where(shop_settings.c.id == 1)
            .values(support_username=value, version=shop_settings.c.version + 1)
        )

    async def complete(self, action: AdminAction, *, cancelled: bool = False) -> None:
        await self.session.execute(
            update(admin_actions)
            .where(admin_actions.c.id == action.id)
            .values(
                status=ActionStatus.CANCELLED if cancelled else ActionStatus.APPLIED,
                completed_at=func.clock_timestamp(),
            )
        )

    async def audit(self, action: AdminAction) -> None:
        changes = (
            {"support_username": "cleared" if action.value is None else "changed"}
            if action.kind == ActionKind.SUPPORT
            else {"role": action.role, "permission": action.permission}
        )
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=action.actor_id,
                action=action.kind,
                target_id=action.target_id or action.actor_id,
                correlation_id=action.id,
                changes=changes,
            )
        )
