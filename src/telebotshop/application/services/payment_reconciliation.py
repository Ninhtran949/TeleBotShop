from datetime import UTC, datetime, timedelta

from telebotshop.application.ports.topups import SettlementReader
from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.topups import TopUpService
from telebotshop.domain.errors import InvalidAction


class PaymentReconciliationService:
    def __init__(self, uow: UnitOfWorkFactory, topups: TopUpService):
        self.uow, self.topups = uow, topups

    async def run(self, reader: SettlementReader, *, since: datetime | None = None) -> int:
        self.topups.available()
        now = datetime.now(UTC)
        if since is not None and (
            not isinstance(since, datetime)
            or since.tzinfo is None
            or since.utcoffset() is None
            or since > now
        ):
            raise InvalidAction("Reconciliation start requires a past timezone-aware timestamp")
        async with self.uow() as work:
            after = (
                since or await work.topups.checkpoint(reader.provider) or now - timedelta(hours=1)
            )
        before = min(after + timedelta(hours=1), now)
        count = 0
        for page in range(1, 11):
            # No UoW/connection is retained during provider I/O.
            events = await reader.read(after - timedelta(minutes=5), before, page)
            for event in events:
                await self.topups.ingest(event)
                count += 1
            if len(events) < 100:
                break
        else:
            raise InvalidAction(
                "Reconciliation window requires operator narrowing; cursor unchanged"
            )
        async with self.uow() as work:
            await work.topups.advance(reader.provider, before)
            await work.commit()
        return count
