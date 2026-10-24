from datetime import timedelta
from uuid import UUID

from telebotshop.application.ports.uow import UnitOfWork, UnitOfWorkFactory
from telebotshop.application.services.access import require_permission
from telebotshop.application.services.orders import validate_uuid
from telebotshop.domain.errors import AccessDenied, InvalidInput
from telebotshop.domain.models.access import Permission
from telebotshop.domain.models.catalog import Page, validate_page
from telebotshop.domain.models.history import OrderDashboard, OrderView
from telebotshop.domain.models.identity import validate_telegram_id


async def history_scope(work: UnitOfWork, actor_id: int, staff: bool) -> UUID | None:
    validate_telegram_id(actor_id)
    if type(staff) is not bool:
        raise InvalidInput("Invalid history scope")
    if staff:
        await require_permission(work, actor_id, Permission.VIEW_ORDERS)
        return None
    profile = await work.identity.get_profile(actor_id)
    if profile is None:
        raise AccessDenied("Order unavailable")
    return profile.id


class HistoryService:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    async def list(
        self,
        actor_id: int,
        page: int = 0,
        *,
        staff: bool = False,
        buyer_telegram_id: int | None = None,
    ) -> Page[OrderView]:
        validate_page(page)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            buyer_id = await history_scope(work, actor_id, staff)
            if buyer_telegram_id is not None:
                if not staff:
                    raise AccessDenied("Customer lookup unavailable")
                validate_telegram_id(buyer_telegram_id)
                buyer = await work.identity.get_profile(buyer_telegram_id)
                if buyer is None:
                    return Page((), page, False)
                buyer_id = buyer.id
            since = await work.orders.now() - timedelta(days=30) if staff else None
            return await work.history.page(buyer_id, page, since)

    async def detail(self, actor_id: int, order_id: UUID, *, staff: bool = False) -> OrderView:
        validate_uuid(order_id)
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            buyer_id = await history_scope(work, actor_id, staff)
            view = await work.history.detail(order_id, buyer_id)
            if view is None:
                raise AccessDenied("Order unavailable")
            return view

    async def dashboard(self, actor_id: int) -> OrderDashboard:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            await history_scope(work, actor_id, True)
            return await work.history.dashboard(await work.orders.now() - timedelta(days=30))
