from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from telebotshop.application.ports.identity import IdentityRepository
from telebotshop.domain.models.identity import CustomerIdentity, Profile
from telebotshop.infrastructure.database.models.identity import (
    audit_logs,
    shop_settings,
    user_roles,
    users,
)

PROFILE_COLUMNS = tuple(
    users.c[name]
    for name in (
        "id",
        "telegram_id",
        "username",
        "display_name",
        "language",
        "registered_at",
        "last_seen_at",
    )
)


class SqlIdentityRepository(IdentityRepository):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_customer(self, identity: CustomerIdentity) -> Profile:
        statement = pg_insert(users).values(
            telegram_id=identity.telegram_id,
            username=identity.username,
            display_name=identity.display_name,
            language=identity.language,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_users_telegram_id",
            set_={
                "username": statement.excluded.username,
                "display_name": statement.excluded.display_name,
                "language": statement.excluded.language,
                "last_seen_at": func.clock_timestamp(),
            },
        ).returning(*PROFILE_COLUMNS)
        row = (await self.session.execute(statement)).mappings().one()
        return Profile(**row)

    async def get_profile(self, telegram_id: int) -> Profile | None:
        result = await self.session.execute(
            select(*PROFILE_COLUMNS).where(users.c.telegram_id == telegram_id)
        )
        row = result.mappings().one_or_none()
        return None if row is None else Profile(**row)

    async def lock_owner(self) -> UUID | None:
        result = await self.session.execute(
            select(shop_settings.c.owner_user_id).where(shop_settings.c.id == 1).with_for_update()
        )
        return result.scalar_one()

    async def is_owner(self, user_id: UUID) -> bool:
        return (
            await self.session.scalar(
                select(user_roles.c.user_id).where(
                    user_roles.c.user_id == user_id, user_roles.c.role == "OWNER"
                )
            )
            is not None
        )

    async def assign_first_owner(self, user_id: UUID) -> None:
        await self.session.execute(insert(user_roles).values(user_id=user_id, role="OWNER"))
        await self.session.execute(
            update(shop_settings).where(shop_settings.c.id == 1).values(owner_user_id=user_id)
        )
        await self.session.execute(
            insert(audit_logs).values(
                actor_id=user_id,
                action="owner.bootstrap",
                target_id=user_id,
                correlation_id=uuid4(),
                changes={"role": "OWNER"},
            )
        )
