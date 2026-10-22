from uuid import uuid4

from telebotshop.application.ports.uow import UnitOfWorkFactory
from telebotshop.application.services.refund_receipts import apply_received_refund
from telebotshop.domain.models.inventory import FINITE_TYPES
from telebotshop.domain.models.orders import OrderAttention, OrderStatus
from telebotshop.domain.models.payments import (
    CommittedWork,
    PaymentOutcome,
    PaymentReceipt,
    PaymentResult,
    PaymentStatus,
    ReviewReason,
    StoredPayment,
)


class PaymentService:
    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self.uow = uow

    async def record(self, receipt: PaymentReceipt) -> PaymentResult:
        async with self.uow() as work:
            await work.orders.configure_timeouts()
            # Charge first, then order and unit: conflicting payloads use the same lock order.
            await work.payments.lock_charge(receipt.charge_id)
            existing = await work.payments.by_charge(receipt.charge_id)
            if existing is not None:
                if existing.receipt == receipt:
                    return PaymentResult(existing.id, PaymentOutcome.DUPLICATE, existing.order_id)
                await work.payments.observe_conflict(existing.id, receipt)
                await work.commit()
                return PaymentResult(existing.id, PaymentOutcome.CONFLICT, existing.order_id)

            order = await work.orders.by_payload(receipt.payload)
            profile = (
                await work.identity.get_profile(receipt.payer_id)
                if receipt.payer_id is not None
                else None
            )
            reason = None
            if order is None:
                reason = ReviewReason.UNKNOWN_ORDER
            elif receipt.payer_id is None:
                reason = ReviewReason.MISSING_PAYER
            elif profile is None or profile.id != order.buyer_id:
                reason = ReviewReason.WRONG_BUYER
            elif receipt.currency != "XTR":
                reason = ReviewReason.WRONG_CURRENCY
            elif receipt.amount != order.price_stars * order.quantity:
                reason = ReviewReason.WRONG_AMOUNT
            elif (
                receipt.is_recurring
                or receipt.is_first_recurring
                or receipt.subscription_expiration_date is not None
                or receipt.has_order_info
                or receipt.shipping_option_id is not None
            ):
                reason = ReviewReason.UNSUPPORTED
            elif await work.payments.primary(order.id) is not None:
                reason = ReviewReason.EXTRA_CHARGE
            elif order.status not in {OrderStatus.AWAITING_PAYMENT, OrderStatus.EXPIRED}:
                reason = ReviewReason.INVALID_ORDER_STATE

            payment = StoredPayment(
                uuid4(),
                receipt,
                PaymentStatus.MATCHED if reason is None else PaymentStatus.REQUIRES_REVIEW,
                order.id if order is not None else None,
                reason,
            )
            await work.payments.save(payment)
            if reason is not None:
                await apply_received_refund(work, payment)
                await work.commit()
                return PaymentResult(payment.id, PaymentOutcome.REQUIRES_REVIEW, payment.order_id)

            assert order is not None and profile is not None
            resource = None
            if order.product_type in FINITE_TYPES:
                available = await work.orders.has_reservation(order, for_update=True)
                missing = OrderAttention.RESERVATION_LOST
            else:
                resource = await work.resources.for_order(order)
                available = resource is not None
                missing = OrderAttention.ENTITLEMENT_UNAVAILABLE
            attention = None if available else missing
            await work.payments.mark_paid(order, attention)
            if available:
                if order.product_type in FINITE_TYPES:
                    await work.payments.allocate(order)
                await work.payments.save_work(
                    CommittedWork(
                        uuid4(),
                        order.id,
                        order.item_id,
                        payment.id,
                        order.unit_id,
                        order.buyer_id,
                        profile.telegram_id,
                        order.product_type,
                        None if resource is None else resource.id,
                    )
                )
            await work.orders.audit(order, "payment.matched" if available else "payment.attention")
            await apply_received_refund(work, payment)
            await work.commit()
            return PaymentResult(payment.id, PaymentOutcome.MATCHED, order.id, attention)
