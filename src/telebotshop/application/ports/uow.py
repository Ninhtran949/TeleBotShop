from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from telebotshop.application.ports.access import AccessRepository
from telebotshop.application.ports.identity import IdentityRepository


class UnitOfWork(Protocol):
    identity: IdentityRepository
    access: AccessRepository

    async def commit(self) -> None: ...


type UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]
