from telebotshop.application.ports.notifications import NotificationSender
from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.identity import Role
from telebotshop.domain.models.notifications import (
    NotificationKind,
    NotificationRequest,
    NotificationResult,
    NotificationState,
)


class NotificationService:
    def __init__(
        self, uow: UnitOfWorkFactory, sender: NotificationSender, *, threshold: int
    ) -> None:
        if type(threshold) is not int or not 0 <= threshold <= 1_000_000:
            raise InvalidInput("Invalid notification threshold")
        self.uow, self.sender, self.threshold = uow, sender, threshold

    async def discover(self, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Invalid notification batch")
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            count = await work.notifications.discover(
                self.threshold, limit, await work.orders.now()
            )
            await work.commit()
            return count

    async def run_once(self) -> bool:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            await work.access.lock()
            now = await work.orders.now()
            await work.notifications.expire(now)
            note = await work.notifications.next()
            if note is None:
                await work.commit()
                return False
            recipient = await work.notifications.recipient(note)
            actor = await work.access.get(recipient)
            allowed = actor is not None and (
                note.kind in {NotificationKind.TICKET_REPLY, NotificationKind.WALLET_TOPUP}
                or actor.role == Role.OWNER
            )
            if not allowed or not await work.notifications.valid(note, self.threshold, now):
                await work.notifications.suppress(note, now)
                await work.commit()
                return True
            deadline = await work.notifications.start(note, now)
            request = NotificationRequest(note.id, note.kind, note.target_id, recipient, deadline)
            await work.commit()
        result = await self.sender.send(request)
        await self.complete(request, result)
        return True

    async def complete(self, request: NotificationRequest, result: NotificationResult) -> None:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            note = await work.notifications.get(request.id)
            if (
                note is None
                or note.state not in {NotificationState.SENDING, NotificationState.UNCERTAIN}
                or note.observed_state is not None
            ):
                return
            now = await work.orders.now()
            state = (
                result.state
                if note.state == NotificationState.SENDING and note.deadline > now
                else NotificationState.UNCERTAIN
            )
            if (
                note.kind != request.kind
                or note.target_id != request.target_id
                or note.deadline != request.deadline
                or await work.notifications.recipient(note) != request.telegram_id
            ):
                return
            await work.notifications.finish(note, result, state, now)
            await work.commit()
