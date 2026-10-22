from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.application.ports.uow import UnitOfWork
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.fulfillment import DeliveryPayload, Fulfillment, ManualNotice
from telebotshop.domain.models.inventory import InventoryContext
from telebotshop.domain.models.orders import Order, OrderStatus
from telebotshop.domain.models.resources import (
    ManualPayload,
    ResourceContext,
    validate_resource_payload,
)


async def read_entitlement(
    work: UnitOfWork, order: Order, fulfillment: Fulfillment, cipher: InventoryCipher
) -> tuple[InventoryContext | ResourceContext, DeliveryPayload] | None:
    """Caller authorizes its actor; recheck the paid binding before loading any ciphertext."""
    committed = fulfillment.work
    if (
        order.status not in {OrderStatus.PAID, OrderStatus.FULFILLING, OrderStatus.DELIVERED}
        or order.attention_reason is not None
        or fulfillment.hold
        or order.id != committed.order_id
        or order.buyer_id != committed.buyer_id
        or order.item_id != committed.order_item_id
        or order.unit_id != committed.inventory_unit_id
        or order.product_type != committed.product_type
        or await work.payments.primary(order.id) != committed.payment_id
    ):
        return None
    encrypted = await work.fulfillment.entitlement(fulfillment)
    if encrypted is None:
        return None
    payload = cipher.open(encrypted.context, encrypted.sealed)
    if isinstance(encrypted.context, ResourceContext):
        if encrypted.metadata is None:
            raise InvalidInput("Missing resource snapshot")
        validate_resource_payload(committed.product_type, encrypted.metadata, payload)
        if isinstance(payload, ManualPayload):
            payload = ManualNotice(payload.customer_notice)
    return encrypted.context, payload
