from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telebotshop.application.ports.uow import UnitOfWork
from telebotshop.infrastructure.database.repositories.access import SqlAccessRepository
from telebotshop.infrastructure.database.repositories.catalog import SqlCatalogRepository
from telebotshop.infrastructure.database.repositories.fulfillment import SqlFulfillmentRepository
from telebotshop.infrastructure.database.repositories.history import SqlHistoryRepository
from telebotshop.infrastructure.database.repositories.identity import SqlIdentityRepository
from telebotshop.infrastructure.database.repositories.inventory import SqlInventoryRepository
from telebotshop.infrastructure.database.repositories.manual import SqlManualRepository
from telebotshop.infrastructure.database.repositories.orders import SqlOrderRepository
from telebotshop.infrastructure.database.repositories.payments import SqlPaymentRepository
from telebotshop.infrastructure.database.repositories.resources import SqlResourceRepository
from telebotshop.infrastructure.database.repositories.support import SqlSupportRepository


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.identity = SqlIdentityRepository(session)
        self.access = SqlAccessRepository(session)
        self.catalog = SqlCatalogRepository(session)
        self.inventory = SqlInventoryRepository(session)
        self.orders = SqlOrderRepository(session)
        self.payments = SqlPaymentRepository(session)
        self.fulfillment = SqlFulfillmentRepository(session)
        self.resources = SqlResourceRepository(session)
        self.manual = SqlManualRepository(session)
        self.history = SqlHistoryRepository(session)
        self.support = SqlSupportRepository(session)

    async def commit(self) -> None:
        await self.session.commit()


@asynccontextmanager
async def unit_of_work(engine: AsyncEngine) -> AsyncIterator[SqlAlchemyUnitOfWork]:
    sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
    async with sessions() as session:
        await session.begin()
        # Session close rolls back uncommitted work on both normal and exceptional exit.
        yield SqlAlchemyUnitOfWork(session)
