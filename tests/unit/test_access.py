from uuid import uuid4

import pytest

from telebotshop.domain.errors import AccessDenied, InvalidInput
from telebotshop.domain.models.access import (
    Access,
    Permission,
    ResourceScope,
    can,
    require_customer_ownership,
    validate_delegation,
)
from telebotshop.domain.models.identity import Role


@pytest.mark.parametrize("role", [None, Role.ADMIN, Role.SUPPORT])
@pytest.mark.parametrize("permission", list(Permission))
def test_roles_without_explicit_grants_default_deny(
    role: Role | None, permission: Permission
) -> None:
    assert not can(Access(uuid4(), 101, role, frozenset(), 0), permission)


def test_owner_and_admin_have_specific_permissions_without_implicit_escalation() -> None:
    admin = Access(uuid4(), 101, Role.ADMIN, frozenset({Permission.REFUND}), 0)
    assert can(admin, Permission.REFUND)
    assert not can(admin, Permission.MANAGE_ACCESS)
    assert not can(admin, Permission.MANAGE_SETTINGS)
    assert not can(admin, "unknown_permission")
    owner = Access(uuid4(), 9001, Role.OWNER, frozenset(Permission), 0)
    assert all(can(owner, permission) for permission in Permission)


def test_support_permission_needs_server_loaded_assignment_and_cannot_read_orders() -> None:
    support = Access(uuid4(), 101, Role.SUPPORT, frozenset(Permission), 0)
    assert not can(support, Permission.MANAGE_TICKETS)
    assert not can(support, Permission.MANAGE_TICKETS, ResourceScope(assigned_staff_id=102))
    assert can(support, Permission.MANAGE_TICKETS, ResourceScope(assigned_staff_id=101))
    assert not can(support, Permission.VIEW_ORDERS, ResourceScope(assigned_staff_id=101))
    assert not can(support, Permission.REFUND)
    assert not can(support, Permission.MANAGE_ACCESS)


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (Role.OWNER, Permission.REFUND),
        (Role.ADMIN, Permission.MANAGE_ACCESS),
        (Role.ADMIN, Permission.MANAGE_SETTINGS),
        (Role.SUPPORT, Permission.REFUND),
        (Role.SUPPORT, Permission.VIEW_ORDERS),
    ],
)
def test_delegation_cannot_create_owner_or_exceed_staff_role(
    role: Role,
    permission: Permission,
) -> None:
    with pytest.raises(InvalidInput):
        validate_delegation(role, permission)


def test_customer_scope_requires_own_server_loaded_resource() -> None:
    require_customer_ownership(101, 101)
    with pytest.raises(AccessDenied):
        require_customer_ownership(101, 102)
    with pytest.raises(InvalidInput):
        require_customer_ownership(True, 101)
