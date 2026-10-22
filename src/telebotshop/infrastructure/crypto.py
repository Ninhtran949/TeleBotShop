import hashlib
import hmac
import json
from dataclasses import asdict
from secrets import token_bytes

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from telebotshop.application.ports.inventory_crypto import (
    CipherContext,
    CipherPayload,
    InventoryCipher,
)
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.inventory import (
    CryptoProof,
    InventoryContext,
    InventoryCryptoError,
    SealedPayload,
    SecretPayload,
    payload_from_record,
)
from telebotshop.domain.models.manual import ManualEvidenceContext, evidence_from_record
from telebotshop.domain.models.resources import ResourceContext, resource_payload_from_record
from telebotshop.domain.models.support import SupportMessageContext, support_message_from_record


def encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class AesGcmInventoryCipher(InventoryCipher):
    def __init__(self, keys: dict[int, bytes], current_version: int, dedup_key: bytes) -> None:
        if (
            not keys
            or type(current_version) is not int
            or current_version not in keys
            or any(type(v) is not int or not 0 < v < 2**31 or len(k) != 32 for v, k in keys.items())
            or len(dedup_key) != 32
            or dedup_key in keys.values()
        ):
            raise InventoryCryptoError("Invalid inventory key configuration")
        self.keys = {version: AESGCM(key) for version, key in keys.items()}
        self.current_version = current_version
        self.dedup_key = dedup_key
        self._proof = CryptoProof(
            hmac.digest(dedup_key, b"inventory-dedup-verifier:v1", hashlib.sha256),
            tuple(
                (
                    version,
                    hmac.digest(
                        dedup_key,
                        encode(["inventory-key-verifier:v1", version]) + key,
                        hashlib.sha256,
                    ),
                )
                for version, key in sorted(keys.items())
            ),
        )

    def proof(self) -> CryptoProof:
        return self._proof

    @staticmethod
    def _record(context: CipherContext, payload: CipherPayload) -> dict[str, str]:
        record = asdict(payload)
        decoded = (
            support_message_from_record(record)
            if isinstance(context, SupportMessageContext)
            else evidence_from_record(record)
            if isinstance(context, ManualEvidenceContext)
            else payload_from_record(context.kind, record)
            if isinstance(context, InventoryContext)
            else resource_payload_from_record(context.kind, record)
        )
        if type(decoded) is not type(payload):
            raise InvalidInput("Invalid encrypted payload type")
        return record

    @staticmethod
    def _aad(context: CipherContext, version: int) -> bytes:
        if isinstance(context, SupportMessageContext):
            return encode(
                [
                    "support-message:v1",
                    str(context.ticket_id),
                    str(context.customer_id),
                    str(context.message_id),
                    str(context.author_id),
                    context.staff,
                    version,
                ]
            )
        if isinstance(context, ManualEvidenceContext):
            return encode(
                [
                    "manual-evidence:v1",
                    str(context.product_id),
                    context.sku,
                    str(context.work_id),
                    str(context.action_id),
                    version,
                ]
            )
        return encode(
            [
                "resource:v1" if isinstance(context, ResourceContext) else "inventory:v1",
                str(context.product_id),
                context.sku,
                str(
                    context.resource_id if isinstance(context, ResourceContext) else context.unit_id
                ),
                context.kind,
                version,
            ]
        )

    def seal(self, context: CipherContext, payload: CipherPayload) -> SealedPayload:
        try:
            record = self._record(context, payload)
            nonce = token_bytes(12)
            ciphertext = self.keys[self.current_version].encrypt(
                nonce, encode(record), self._aad(context, self.current_version)
            )
            return SealedPayload(self.current_version, nonce, ciphertext)
        except (InvalidInput, ValueError, TypeError):
            raise InventoryCryptoError("Inventory encryption failed") from None

    def fingerprint(self, context: InventoryContext, payload: SecretPayload) -> bytes:
        try:
            material = encode(
                [
                    "inventory-dedup:v1",
                    str(context.product_id),
                    context.sku,
                    context.kind,
                    self._record(context, payload),
                ]
            )
            return hmac.digest(self.dedup_key, material, hashlib.sha256)
        except (InvalidInput, ValueError, TypeError):
            raise InventoryCryptoError("Inventory fingerprint failed") from None

    def open(self, context: CipherContext, sealed: SealedPayload) -> CipherPayload:
        # No decrypt port/use case is exposed before authorized entitlement delivery exists.
        try:
            plaintext = self.keys[sealed.key_version].decrypt(
                sealed.nonce, sealed.ciphertext, self._aad(context, sealed.key_version)
            )
            record = json.loads(plaintext)
            return (
                support_message_from_record(record)
                if isinstance(context, SupportMessageContext)
                else evidence_from_record(record)
                if isinstance(context, ManualEvidenceContext)
                else payload_from_record(context.kind, record)
                if isinstance(context, InventoryContext)
                else resource_payload_from_record(context.kind, record)
            )
        except (InvalidTag, KeyError, ValueError, TypeError, InvalidInput):
            raise InventoryCryptoError("Inventory decryption failed") from None
