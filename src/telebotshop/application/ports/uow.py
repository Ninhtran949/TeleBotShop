from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from telebotshop.application.ports.access import AccessRepository
from telebotshop.application.ports.catalog import CatalogRepository
from telebotshop.application.ports.identity import IdentityRepository
from telebotshop.application.ports.inventory import InventoryRepository


class UnitOfWork(Protocol):
    identity: IdentityRepository
    access: AccessRepository
    catalog: CatalogRepository
    inventory: InventoryRepository

    async def commit(self) -> None: ...


type UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]
