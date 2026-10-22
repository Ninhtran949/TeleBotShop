from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.admin import ActionStatus
from telebotshop.domain.models.catalog import validate_code
from telebotshop.domain.models.fulfillment import DeliveryState
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.resources import validate_resource_text


@dataclass(frozen=True, repr=False)
class ManualEvidence:
    reference: str

    def __post_init__(self) -> None:
        validate_resource_text(self.reference, 500)


def evidence_from_record(record: object) -> ManualEvidence:
    if isinstance(record, dict) and set(record) == {"reference"}:
        return ManualEvidence(record["reference"])
    raise InvalidInput("Invalid manual evidence")


@dataclass(frozen=True, repr=False)
class ManualEvidenceContext:
    product_id: UUID
    sku: str
    work_id: UUID
    action_id: UUID

    def __post_init__(self) -> None:
        validate_code(self.sku)
        if not all(isinstance(v, UUID) for v in (self.product_id, self.work_id, self.action_id)):
            raise InventoryCryptoError("Invalid evidence context")


@dataclass(frozen=True, repr=False)
class ManualWorkView:
    work_id: UUID
    order_id: UUID
    destination_telegram_id: int
    sku: str
    state: DeliveryState


@dataclass(frozen=True, repr=False)
class ManualDetail(ManualWorkView):
    customer_notice: str
    staff_instructions: str


@dataclass(frozen=True, repr=False)
class ManualAction:
    id: UUID
    actor_id: UUID
    work_id: UUID
    attempt_id: UUID
    expires_at: datetime
    status: ActionStatus = ActionStatus.PENDING
