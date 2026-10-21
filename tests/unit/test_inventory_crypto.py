from dataclasses import replace
from uuid import uuid4

import pytest

from telebotshop.domain.models.catalog import ProductType
from telebotshop.domain.models.inventory import (
    InventoryContext,
    InventoryCryptoError,
    LicensePayload,
    UniqueKeyPayload,
)
from telebotshop.infrastructure.crypto import AesGcmInventoryCipher


def test_aead_roundtrip_uses_fresh_nonces_and_preserves_each_typed_payload() -> None:
    cipher = AesGcmInventoryCipher({1: b"a" * 32}, 1, b"d" * 32)
    for kind, payload in (
        (ProductType.UNIQUE_KEY, UniqueKeyPayload(" FAKE-Key ")),
        (ProductType.LICENSE, LicensePayload("FAKE-License")),
    ):
        context = InventoryContext(uuid4(), "FAKE-SKU", uuid4(), kind)
        first, second = cipher.seal(context, payload), cipher.seal(context, payload)
        assert len(first.nonce) == 12 and first.nonce != second.nonce
        assert first.ciphertext != second.ciphertext
        assert cipher.open(context, first) == payload
        assert b"FAKE" not in first.ciphertext and "FAKE" not in repr(first)


def test_ciphertext_nonce_aad_key_and_type_tampering_fail_closed() -> None:
    cipher = AesGcmInventoryCipher({1: b"a" * 32}, 1, b"d" * 32)
    context = InventoryContext(uuid4(), "FAKE-SKU", uuid4(), ProductType.UNIQUE_KEY)
    encrypted = cipher.seal(context, UniqueKeyPayload("FAKE-Secret"))
    for changed in (
        replace(context, product_id=uuid4()),
        replace(context, sku="OTHER"),
        replace(context, unit_id=uuid4()),
        replace(context, kind=ProductType.LICENSE),
    ):
        with pytest.raises(InventoryCryptoError):
            cipher.open(changed, encrypted)
    for changed in (
        replace(encrypted, nonce=b"x" * 12),
        replace(
            encrypted, ciphertext=encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1])
        ),
        replace(encrypted, key_version=2),
    ):
        with pytest.raises(InventoryCryptoError):
            cipher.open(context, changed)
    with pytest.raises(InventoryCryptoError):
        AesGcmInventoryCipher({1: b"b" * 32}, 1, b"d" * 32).open(context, encrypted)
    with pytest.raises(InventoryCryptoError):
        cipher.seal(context, LicensePayload("FAKE-License"))


def test_key_rotation_reads_old_version_and_keeps_sku_dedup_stable() -> None:
    old = AesGcmInventoryCipher({1: b"a" * 32}, 1, b"d" * 32)
    rotated = AesGcmInventoryCipher({1: b"a" * 32, 2: b"b" * 32}, 2, b"d" * 32)
    context = InventoryContext(uuid4(), "FAKE-SKU", uuid4(), ProductType.UNIQUE_KEY)
    payload = UniqueKeyPayload("FAKE-AbC")
    assert rotated.open(context, old.seal(context, payload)) == payload
    assert rotated.seal(context, payload).key_version == 2
    fingerprint = old.fingerprint(context, payload)
    assert len(fingerprint) == 32 and fingerprint == rotated.fingerprint(context, payload)
    assert fingerprint == old.fingerprint(replace(context, unit_id=uuid4()), payload)
    assert fingerprint != old.fingerprint(replace(context, sku="OTHER"), payload)
    assert fingerprint != old.fingerprint(context, UniqueKeyPayload("fake-abc"))
    assert fingerprint != old.fingerprint(context, UniqueKeyPayload(" FAKE-AbC"))
    assert old.fingerprint(context, UniqueKeyPayload("é")) != old.fingerprint(
        context, UniqueKeyPayload("e\u0301")
    )
