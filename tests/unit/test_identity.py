from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from telebotshop.application.ports.identity import IdentityRepository
from telebotshop.application.ports.uow import UnitOfWork
from telebotshop.application.services.identity import IdentityService
from telebotshop.domain.errors import AccessDenied, InvalidInput
from telebotshop.domain.models.identity import CustomerIdentity, Profile


@pytest.mark.parametrize("telegram_id", [True, 0, -1, 2**52, "123", None])
def test_customer_identity_rejects_untrusted_ids(telegram_id: object) -> None:
    with pytest.raises(InvalidInput):
        CustomerIdentity(telegram_id, None, "Test")


@pytest.mark.parametrize(
    ("username", "display_name", "language"),
    [
        ("bad username", "Test", "vi"),
        (None, "", "vi"),
        (None, "x" * 130, "vi"),
        (None, "Test", "x" * 36),
        (None, "Test", "<script>"),
    ],
)
def test_profile_validation_errors_do_not_echo_input(
    username: str | None, display_name: str, language: str
) -> None:
    with pytest.raises(InvalidInput, match="Invalid identity") as failure:
        CustomerIdentity(123, username, display_name, language)
    assert "<script>" not in str(failure.value)
    assert "bad username" not in str(failure.value)


def test_default_locale_and_profile_repr_do_not_expose_identity() -> None:
    identity = CustomerIdentity(123, "fake_customer", "Private display name")
    assert identity.language == "vi"
    assert "fake_customer" not in repr(identity)
    assert "Private display name" not in repr(identity)


@pytest.mark.parametrize(
    ("username", "display_name", "language"),
    [
        (123, "Fake", "vi"),
        (None, True, "vi"),
        (None, "Fake", None),
    ],
)
def test_malformed_profile_field_types_fail_with_sanitized_domain_error(
    username: object,
    display_name: object,
    language: object,
) -> None:
    with pytest.raises(InvalidInput, match="Invalid identity"):
        CustomerIdentity(101, username, display_name, language)


@pytest.mark.asyncio
async def test_direct_profile_use_case_rejects_a_repository_result_owned_by_another_user() -> None:
    work = AsyncMock(spec=UnitOfWork)
    work.identity = AsyncMock(spec=IdentityRepository)
    work.identity.get_profile.return_value = Profile(
        uuid4(),
        102,
        None,
        "Fake other user",
        "vi",
        datetime.now(UTC),
        datetime.now(UTC),
    )

    @asynccontextmanager
    async def factory() -> AsyncIterator[UnitOfWork]:
        yield work

    with pytest.raises(AccessDenied):
        await IdentityService(factory).get_my_profile(101)
