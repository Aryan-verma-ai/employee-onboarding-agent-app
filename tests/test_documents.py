from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import documents


def test_magic_bytes_and_limit(monkeypatch):
    monkeypatch.setattr(documents, "settings", SimpleNamespace(max_upload_bytes=10))
    documents.validate_upload(b"%PDF-123", "application/pdf")
    for content, mime in [
        (b"%PDF-123", "image/png"),
        (b"bad", "application/pdf"),
        (b"%PDF-123456789", "application/pdf"),
    ]:
        with pytest.raises(HTTPException):
            documents.validate_upload(content, mime)


def test_storage_scan_fails_closed(monkeypatch):
    monkeypatch.setattr(documents, "settings", SimpleNamespace(environment="production"))
    blob = SimpleNamespace(
        get_blob_tags=lambda: {}, download_blob=lambda **kw: pytest.fail("Must not read unscanned content")
    )
    monkeypatch.setattr(documents, "blob_client", lambda key: blob)
    with pytest.raises(HTTPException) as error:
        documents.read_clean_content(SimpleNamespace(storage_key="private/doc"))
    assert error.value.status_code == 409


def test_local_key_cannot_escape_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(documents, "settings", SimpleNamespace(storage_path=str(tmp_path)))
    with pytest.raises(HTTPException):
        documents.local_path("../../outside")
