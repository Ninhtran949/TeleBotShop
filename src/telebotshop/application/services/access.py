from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.domain.errors import AccessDenied, InvalidAction, InvalidInput
from telebotshop.domain.models.access import (
    Access,
    Permission,
    ResourceScope,
    can,
    validate_delegation,
)
from telebotshop.domain.models.admin import (
    ActionKind,
    ActionStatus,
    AdminAction,
    ShopSettings,
    validate_support_username,
)
from telebotshop.domain.models.identity import Role, validate_telegram_id


async def require_permission(
    work: UnitOfWork,
    actor_id: int,
    permission: Permission,
    scope: ResourceScope | None = None,
) -> Access:
    """Hold the access lock through the caller's transaction, including its mutation/commit."""
    validate_telegram_id(actor_id)
    await work.access.lock()
    actor = await work.access.get(actor_id)
    if actor is None or not can(actor, permission, scope):
        raise AccessDenied("Access denied")
    return actor


class AccessService:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    async def authorize(
        self,
        actor_id: int,
        permission: Permission,
        scope: ResourceScope | None = None,
    ) -> Access:
        async with self.uow() as work:
            return await require_permission(work, actor_id, permission, scope)

    async def admin_access(self, actor_id: int) -> Access:
        validate_telegram_id(actor_id)
        async with self.uow() as work:
            await work.access.lock()
            actor = await work.access.get(actor_id)
            if actor is None or actor.role is None or not actor.permissions:
                raise AccessDenied("Access denied")
            return actor

    async def settings(self, actor_id: int) -> ShopSettings:
        async with self.uow() as work:
            await require_permission(work, actor_id, Permission.MANAGE_SETTINGS)
            return await work.access.lock()

    async def prepare_permission(
        self,
        actor_id: int,
        target_id: int,
        role: Role,
        permission: Permission,
        grant: bool,
    ) -> AdminAction:
        validate_telegram_id(target_id)
        validate_delegation(role, permission)
        if type(grant) is not bool:
            raise InvalidInput("Invalid delegation")
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_ACCESS)
            target = await work.access.get(target_id)
            if (
                target is None
                or target.role == Role.OWNER
                or target.user_id == actor.user_id
                or target.role != role
                and target.permissions
                or (permission in target.permissions) == grant
            ):
                raise InvalidAction("Invalid access change")
            action = AdminAction(
                uuid4(),
                actor.user_id,
                ActionKind.GRANT if grant else ActionKind.REVOKE,
                target.user_id,
                role,
                permission,
                None,
                target.version,
                datetime.now(UTC) + timedelta(minutes=10),
            )
            await work.access.save_action(action)
            await work.commit()
            return action

    async def prepare_support(self, actor_id: int, username: str | None) -> AdminAction:
        validate_support_username(username)
        async with self.uow() as work:
            actor = await require_permission(work, actor_id, Permission.MANAGE_SETTINGS)
            settings = await work.access.lock()
            if settings.support_username == username:
                raise InvalidAction("Setting unchanged")
            action = AdminAction(
                uuid4(),
                actor.user_id,
                ActionKind.SUPPORT,
                None,
                None,
                None,
                username,
                settings.version,
                datetime.now(UTC) + timedelta(minutes=10),
            )
            await work.access.save_action(action)
            await work.commit()
            return action

    async def confirm(self, actor_id: int, action_id: UUID) -> bool:
        if not isinstance(action_id, UUID):
            raise InvalidInput("Invalid action")
        async with self.uow() as work:
            settings = await work.access.lock(exclusive=True)
            actor = await require_permission(work, actor_id, Permission.MANAGE_ACCESS)
            action = await work.access.get_action(action_id)
            if action is None or action.actor_id != actor.user_id:
                raise InvalidAction("Invalid action")
            if action.status == ActionStatus.APPLIED:
                return False
            if action.status != ActionStatus.PENDING or action.expires_at <= datetime.now(UTC):
                raise InvalidAction("Invalid action")
            if action.kind == ActionKind.SUPPORT:
                await require_permission(work, actor_id, Permission.MANAGE_SETTINGS)
                if action.expected_version != settings.version:
                    raise InvalidAction("Stale action")
                await work.access.set_support_username(action.value)
            else:
                if action.target_id is None or action.role is None or action.permission is None:
                    raise InvalidAction("Invalid action")
                target = await work.access.get_by_id(action.target_id)
                if (
                    target is None
                    or target.role == Role.OWNER
                    or target.version != action.expected_version
                ):
                    raise InvalidAction("Stale action")
                validate_delegation(action.role, action.permission)
                await work.access.set_permission(
                    target.user_id,
                    action.role,
                    action.permission,
                    grant=action.kind == ActionKind.GRANT,
                )
            await work.access.audit(action)
            await work.access.complete(action)
            await work.commit()
            return True

    async def cancel(self, actor_id: int, action_id: UUID) -> None:
        if not isinstance(action_id, UUID):
            raise InvalidInput("Invalid action")
        async with self.uow() as work:
            await work.access.lock(exclusive=True)
            actor = await require_permission(work, actor_id, Permission.MANAGE_ACCESS)
            action = await work.access.get_action(action_id)
            if action is None or action.actor_id != actor.user_id:
                raise InvalidAction("Invalid action")
            if action.status == ActionStatus.PENDING:
                await work.access.complete(action, cancelled=True)
                await work.commit()
