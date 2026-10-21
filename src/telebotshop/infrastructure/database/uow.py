from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telebotshop.application.ports.uow import UnitOfWork


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()


@asynccontextmanager
async def unit_of_work(engine: AsyncEngine) -> AsyncIterator[SqlAlchemyUnitOfWork]:
    sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
    async with sessions() as session:
        await session.begin()
        # Session close rolls back uncommitted work on both normal and exceptional exit.
        yield SqlAlchemyUnitOfWork(session)
