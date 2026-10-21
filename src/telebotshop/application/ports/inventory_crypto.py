from typing import Protocol

from telebotshop.domain.models.inventory import (
    CryptoProof,
    InventoryContext,
    SealedPayload,
    SecretPayload,
)


class InventoryCipher(Protocol):
    def proof(self) -> CryptoProof: ...

    def seal(self, context: InventoryContext, payload: SecretPayload) -> SealedPayload: ...

    def fingerprint(self, context: InventoryContext, payload: SecretPayload) -> bytes: ...
