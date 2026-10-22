from functools import partial

from telebotshop.application.services.support import SupportService
from telebotshop.infrastructure.database.uow import unit_of_work
from tests.inventory import fake_cipher


def support_service(engine, *, cipher=None, factory=None):
    return SupportService(factory or partial(unit_of_work, engine), cipher or fake_cipher())
