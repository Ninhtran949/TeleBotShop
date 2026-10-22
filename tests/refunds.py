from functools import partial
from unittest.mock import AsyncMock

from telebotshop.application.services.refunds import RefundService
from telebotshop.domain.models.refunds import RefundResult, RefundState
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import fake_cipher


async def refund_staff(engine, actor_id=202):
    from telebotshop.application.services.access import AccessService
    from telebotshop.application.services.identity import IdentityService
    from telebotshop.domain.models.access import Permission
    from telebotshop.domain.models.identity import CustomerIdentity, Role

    factory = partial(unit_of_work, engine)
    await IdentityService(factory).get_or_create_customer(
        CustomerIdentity(actor_id, None, "Fake staff")
    )
    service = AccessService(factory)
    grant = await service.prepare_permission(9001, actor_id, Role.ADMIN, Permission.REFUND, True)
    await service.confirm(9001, grant.id)
    return service


def refund_service(engine, gateway=None):
    if gateway is None:
        gateway = AsyncMock()
        gateway.refund.return_value = RefundResult(RefundState.SUCCEEDED)
        gateway.transactions.return_value = ()
    return RefundService(partial(unit_of_work, engine), fake_cipher(), gateway)
