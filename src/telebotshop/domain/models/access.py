from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import AccessDenied, InvalidInput
from telebotshop.domain.models.identity import Role, validate_telegram_id


class Permission(StrEnum):
    MANAGE_ACCESS = "access.manage"
    MANAGE_SETTINGS = "settings.manage"
    MANAGE_CATALOG = "catalog.manage"
    MANAGE_INVENTORY = "inventory.manage"
    MANAGE_FULFILLMENT = "fulfillment.manage"
    VIEW_ORDERS = "orders.read"
    REFUND = "payments.refund"
    MANAGE_TICKETS = "tickets.manage"


ROLE_PERMISSIONS = {
    Role.OWNER: frozenset(Permission),
    Role.ADMIN: frozenset(
        {
            Permission.MANAGE_CATALOG,
            Permission.MANAGE_INVENTORY,
            Permission.MANAGE_FULFILLMENT,
            Permission.VIEW_ORDERS,
            Permission.REFUND,
            Permission.MANAGE_TICKETS,
        }
    ),
    Role.SUPPORT: frozenset({Permission.MANAGE_TICKETS}),
}


@dataclass(frozen=True, repr=False)
class Access:
    user_id: UUID
    telegram_id: int
    role: Role | None
    permissions: frozenset[Permission]
    version: int


@dataclass(frozen=True)
class ResourceScope:
    """Ownership/assignment loaded by a use case, never copied from transport input."""

    assigned_staff_id: int | None = None


def can(access: Access, permission: Permission, scope: ResourceScope | None = None) -> bool:
    if (
        not isinstance(permission, Permission)
        or permission not in access.permissions
        or permission not in ROLE_PERMISSIONS.get(access.role, frozenset())
    ):
        return False
    if access.role == Role.SUPPORT:
        return scope is not None and scope.assigned_staff_id == access.telegram_id
    return True


def validate_delegation(role: Role, permission: Permission) -> None:
    if (
        not isinstance(role, Role)
        or not isinstance(permission, Permission)
        or role == Role.OWNER
        or permission not in ROLE_PERMISSIONS[role]
    ):
        raise InvalidInput("Invalid delegation")


def require_customer_ownership(actor_id: int, owner_id: int) -> None:
    validate_telegram_id(actor_id)
    validate_telegram_id(owner_id)
    if actor_id != owner_id:
        raise AccessDenied("Access denied")
