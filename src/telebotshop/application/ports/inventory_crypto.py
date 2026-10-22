from typing import Protocol

from telebotshop.domain.models.inventory import (
    CryptoProof,
    InventoryContext,
    SealedPayload,
    SecretPayload,
)
from telebotshop.domain.models.manual import ManualEvidence, ManualEvidenceContext
from telebotshop.domain.models.resources import ResourceContext, ResourcePayload
from telebotshop.domain.models.support import SupportMessage, SupportMessageContext

type CipherContext = (
    InventoryContext | ResourceContext | ManualEvidenceContext | SupportMessageContext
)
type CipherPayload = SecretPayload | ResourcePayload | ManualEvidence | SupportMessage


class InventoryCipher(Protocol):
    def proof(self) -> CryptoProof: ...

    def seal(self, context: CipherContext, payload: CipherPayload) -> SealedPayload: ...

    def fingerprint(self, context: InventoryContext, payload: SecretPayload) -> bytes: ...

    def open(self, context: CipherContext, sealed: SealedPayload) -> CipherPayload: ...
