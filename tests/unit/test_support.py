from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.inventory import InventoryCryptoError
from telebotshop.domain.models.resources import ReusableTextPayload
from telebotshop.domain.models.support import PublicTerms, SupportMessage, SupportMessageContext
from telebotshop.infrastructure.support import TermsConfigurationError, load_terms
from tests.inventory import fake_cipher


def test_ticket_body_is_bounded_typed_encrypted_and_bound_to_all_identities():
    cipher = fake_cipher()
    context = SupportMessageContext(uuid4(), uuid4(), uuid4(), uuid4())
    payload = SupportMessage("FAKE private support <&>\nNội dung")
    sealed = cipher.seal(context, payload)
    assert b"FAKE" not in sealed.ciphertext and "FAKE" not in repr(payload)
    assert cipher.open(context, sealed) == payload
    for field in ("ticket_id", "customer_id", "message_id", "author_id"):
        with pytest.raises(InventoryCryptoError):
            cipher.open(replace(context, **{field: uuid4()}), sealed)
    with pytest.raises(InventoryCryptoError):
        cipher.open(replace(context, staff=True), sealed)
    with pytest.raises(InventoryCryptoError):
        cipher.seal(context, ReusableTextPayload("same shape, wrong type"))
    for value in ("", " ", "x" * 2001, "ạ" * 667, "bad\x00body"):
        with pytest.raises(InvalidInput):
            SupportMessage(value)


def test_public_terms_file_is_explicit_bounded_and_does_not_publish_draft(tmp_path):
    assert load_terms(None) is None
    path = tmp_path / "terms.md"
    path.write_text("FAKE TEST TERMS\n" + "điều khoản 😀\n" * 400, encoding="utf-8")
    terms = load_terms(path)
    assert "".join(terms.pages) == terms.text
    assert all(len(page.encode("utf-8")) <= 3000 for page in terms.pages)
    for body in (
        "Ngày: [DD/MM/YYYY]",
        "Đơn vị: [Tên cá nhân kinh doanh/doanh nghiệp]",
        "[X giờ]",
        "[Y ngày làm việc]",
        "[Liên kết chính sách quyền riêng tư]",
        "x" * 24001,
    ):
        path.write_text(body, encoding="utf-8")
        with pytest.raises(TermsConfigurationError):
            load_terms(path)
    with pytest.raises(TermsConfigurationError):
        load_terms(tmp_path / ".env")
    with pytest.raises(InvalidInput):
        PublicTerms("bad\x00body")


def test_terms_network_paths_are_rejected_before_resolution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network path resolution")

    monkeypatch.setattr(Path, "resolve", forbidden)
    with pytest.raises(TermsConfigurationError):
        load_terms(Path(r"\\untrusted\share\terms.md"))
