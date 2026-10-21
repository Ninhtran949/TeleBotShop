from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.domain.errors import AccessDenied, OwnerConfigurationError
from telebotshop.domain.models.access import require_customer_ownership
from telebotshop.domain.models.identity import CustomerIdentity, Profile, validate_telegram_id


class IdentityService:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    async def get_or_create_customer(self, identity: CustomerIdentity) -> Profile:
        async with self.uow() as work:
            profile = await work.identity.upsert_customer(identity)
            await work.commit()
            return profile

    async def get_my_profile(self, actor_id: int) -> Profile:
        validate_telegram_id(actor_id)
        async with self.uow() as work:
            profile = await work.identity.get_profile(actor_id)
            if profile is None:
                raise AccessDenied("Access denied")
            require_customer_ownership(actor_id, profile.telegram_id)
            return profile

    async def bootstrap_owner(self, owner_id: int) -> None:
        validate_telegram_id(owner_id)
        async with self.uow() as work:
            owner = await work.identity.lock_owner()
            if owner is not None:
                profile = await work.identity.get_profile(owner_id)
                if (
                    profile is None
                    or profile.id != owner
                    or not await work.identity.is_owner(owner)
                ):
                    raise OwnerConfigurationError("Owner configuration mismatch")
                return
            profile = await work.identity.get_profile(owner_id)
            if profile is None:
                profile = await work.identity.upsert_customer(
                    CustomerIdentity(owner_id, None, None)
                )
            await work.identity.assign_first_owner(profile.id)
            await work.commit()
