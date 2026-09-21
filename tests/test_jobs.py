from datetime import timedelta
from types import SimpleNamespace as Obj

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import Principal
from app.jobs import ExtractionJob, claim, enqueue, fenced_job
from app.models import Base, Case, Document, utcnow
from app.worker import process_one


@pytest.fixture
def queued(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "jobs.db"))
    Base.metadata.create_all(engine)
    principal = Principal("worker", "tenant", frozenset({"HR"}))
    with Session(engine) as db:
        case = Case(
            tenant_id="tenant",
            owner_id="employee",
            department="engineering",
            consent_at=utcnow(),
            status="extracting",
        )
        db.add(case)
        db.flush()
        document = Document(
            case_id=case.id,
            tenant_id="tenant",
            filename="synthetic.pdf",
            content_type="application/pdf",
            storage_key="safe/key",
            sha256="0" * 64,
            scan_status="clean",
        )
        db.add(document)
        db.flush()
        job = enqueue(db, principal, case, document)
        job_id = job.id
        db.commit()
    return engine, principal, job_id


def test_restart_lease_and_fencing(queued):
    engine, principal, job_id = queued
    with Session(engine) as db:
        first = claim(db, principal, lease_seconds=1)
    with Session(engine) as db:
        assert claim(db, principal) is None
        second = claim(db, principal, now=utcnow() + timedelta(seconds=2))
        assert second[0] == job_id and second[1] != first[1]
        assert fenced_job(db, principal, *first) is None
        assert fenced_job(db, principal, *second).attempts == 2


def test_worker_success_and_no_cross_tenant_discovery(queued):
    engine, principal, job_id = queued
    with Session(engine) as db:
        assert claim(db, Principal("other", "other", frozenset({"HR"}))) is None
    result = {"accepted": {"email": "synthetic@example.invalid"}, "review_fields": [], "candidates": []}
    assert process_one(
        engine, principal, reader=lambda doc: b"%PDF-synthetic", extractor=lambda content, doc_id: result
    )
    with Session(engine) as db:
        job = db.get(ExtractionJob, job_id)
        assert job.status == "complete"
        assert db.get(Document, job.document_id).extraction["reviewed"] is False
    assert not process_one(engine, principal)


def test_failure_persists_retry_and_withdrawal_cancels(queued):
    engine, principal, job_id = queued

    def failing(content, document_id):
        raise RuntimeError("private provider message")

    assert process_one(engine, principal, reader=lambda doc: b"bytes", extractor=failing)
    with Session(engine) as db:
        job = db.get(ExtractionJob, job_id)
        assert job.status == "queued" and job.error_code == "extraction_failed"
        assert "private" not in str(db.get(Document, job.document_id).extraction)
        case = db.get(Case, job.case_id)
        case.consent_withdrawn_at = utcnow()
        db.commit()
    assert process_one(
        engine,
        principal,
        now=utcnow() + timedelta(seconds=30),
        reader=lambda doc: pytest.fail("No read after withdrawal"),
    )
    with Session(engine) as db:
        assert db.get(ExtractionJob, job_id).status == "cancelled"


def test_all_fields_normalized_with_provenance():
    from app.extraction import candidates_from_result

    lines = [
        "Name: Synthetic Person",
        "Phone: +91 98765 43210",
        "Email: TEST@example.invalid",
        "PAN ABCDE1234F",
        "Aadhaar 2345 6789 0123",
    ]
    offset = 0
    words = []
    ocr_lines = []
    for text in lines:
        span = Obj(offset=offset, length=len(text))
        words.append(Obj(span=span, confidence=0.98))
        ocr_lines.append(Obj(content=text, spans=[span]))
        offset += len(text) + 1
    result = candidates_from_result(
        Obj(pages=[Obj(page_number=1, words=words, lines=ocr_lines)]), "fixture-doc"
    )
    assert result["accepted"] == {
        "full_name": "Synthetic Person",
        "phone": "+919876543210",
        "email": "test@example.invalid",
        "pan": "ABCDE1234F",
        "aadhaar": "234567890123",
    }
    assert all(item["document_id"] == "fixture-doc" and item["page"] == 1 for item in result["candidates"])


def test_two_workers_only_one_claim(queued):
    from concurrent.futures import ThreadPoolExecutor

    engine, principal, job_id = queued

    def attempt():
        with Session(engine) as db:
            return claim(db, principal)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1


def test_synthetic_fixture_file_signatures():
    from pathlib import Path

    root = Path(__file__).with_name("fixtures")
    assert (root / "synthetic-onboarding.pdf").read_bytes().startswith(b"%PDF-1.4")
    assert (root / "synthetic-onboarding.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_document_key_reuse_rejects_other_hosts(monkeypatch):
    from app.extraction import document_credential

    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE", "api_key")
    monkeypatch.delenv("AZURE_DOCUMENT_INTELLIGENCE_API_KEY", raising=False)
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://same.services.ai.azure.com/api/projects/test")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-key")
    with pytest.raises(ValueError):
        document_credential("https://different.example.invalid")
    with pytest.raises(ValueError):
        document_credential("http://same.services.ai.azure.com")
    assert document_credential("https://same.services.ai.azure.com").key == "synthetic-key"
