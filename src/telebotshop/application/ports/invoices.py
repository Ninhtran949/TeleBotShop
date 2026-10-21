from typing import Protocol

from telebotshop.domain.models.orders import InvoiceDelivery, InvoiceRequest


class InvoiceSender(Protocol):
    async def send(self, request: InvoiceRequest) -> InvoiceDelivery: ...
