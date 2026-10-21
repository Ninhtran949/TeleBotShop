import hashlib
import hmac
import json
from dataclasses import asdict
from secrets import token_bytes

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from telebotshop.application.ports.inventory_crypto import InventoryCipher
from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.inventory import (
    CryptoProof,
    InventoryContext,
    InventoryCryptoError,
    SealedPayload,
    SecretPayload,
    payload_from_record,
)


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
    def _record(context: InventoryContext, payload: SecretPayload) -> dict[str, str]:
        record = asdict(payload)
        payload_from_record(context.kind, record)
        return record

    @staticmethod
    def _aad(context: InventoryContext, version: int) -> bytes:
        return encode(
            [
                "inventory:v1",
                str(context.product_id),
                context.sku,
                str(context.unit_id),
                context.kind,
                version,
            ]
        )

    def seal(self, context: InventoryContext, payload: SecretPayload) -> SealedPayload:
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

    def open(self, context: InventoryContext, sealed: SealedPayload) -> SecretPayload:
        # No decrypt port/use case is exposed before authorized entitlement delivery exists.
        try:
            plaintext = self.keys[sealed.key_version].decrypt(
                sealed.nonce, sealed.ciphertext, self._aad(context, sealed.key_version)
            )
            return payload_from_record(context.kind, json.loads(plaintext))
        except (InvalidTag, KeyError, ValueError, TypeError, InvalidInput):
            raise InventoryCryptoError("Inventory decryption failed") from None
