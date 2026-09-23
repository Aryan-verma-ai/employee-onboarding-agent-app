"""Test verification that PAN and Aadhaar photos/scans are accurately classified and extracted."""

from types import SimpleNamespace
from app.extraction import classify_document


def test_classify_pan_from_candidates():
    # 1. Image with WhatsApp filename containing PAN candidate
    candidates = [
        {"field": "pan", "value": "ABCDE1234F", "confidence": 0.95},
        {"field": "full_name", "value": "Rahul Sharma", "confidence": 0.90},
    ]
    doc_type = classify_document(candidates, filename="WhatsApp Image 2026-08-31 at 6.45.52 PM.jpeg", content_type="image/jpeg")
    assert doc_type == "pan", f"Expected 'pan', got '{doc_type}'"


def test_classify_aadhaar_from_candidates():
    # 2. Image with phone camera filename containing Aadhaar candidate
    candidates = [
        {"field": "aadhaar", "value": "123456789012", "confidence": 0.95},
        {"field": "full_name", "value": "Rahul Sharma", "confidence": 0.90},
        {"field": "address", "value": "123 MG Road, Bengaluru", "confidence": 0.85},
    ]
    doc_type = classify_document(candidates, filename="IMG_20260923_112233.jpg", content_type="image/jpeg")
    assert doc_type == "aadhaar", f"Expected 'aadhaar', got '{doc_type}'"


def test_classify_from_llm_hint():
    # 3. LLM doc_type_hint detection even if fields are nested
    candidates = [
        {"field": "full_name", "value": "Priya Patel", "doc_type_hint": "pan_card"},
    ]
    doc_type = classify_document(candidates, filename="camera_photo.png", content_type="image/png")
    assert doc_type == "pan", f"Expected 'pan', got '{doc_type}'"

    candidates_aadhaar = [
        {"field": "full_name", "value": "Priya Patel", "doc_type_hint": "aadhaar_card"},
    ]
    doc_type = classify_document(candidates_aadhaar, filename="scan_front.jpeg", content_type="image/jpeg")
    assert doc_type == "aadhaar", f"Expected 'aadhaar', got '{doc_type}'"


def test_classify_true_photograph_only_when_no_text():
    # 4. Pure profile headshot without legal card numbers
    candidates = []
    # Explicit photo filename
    assert classify_document(candidates, filename="headshot.jpg", content_type="image/jpeg") == "photograph"
    assert classify_document(candidates, filename="candidate_photo.png", content_type="image/png") == "photograph"
    assert classify_document(candidates, filename="profile_pic.jpeg", content_type="image/jpeg") == "photograph"

    # WhatsApp image with zero text candidates
    assert classify_document(candidates, filename="WhatsApp Image 2026-08-31 at 6.45.52 PM.jpeg", content_type="image/jpeg") == "photograph"


def test_classify_pan_filename_priority():
    # 5. Filename with 'pan' or 'aadhaar' is never treated as photograph
    candidates = []
    assert classify_document(candidates, filename="my_pancard_photo.jpg", content_type="image/jpeg") == "pan"
    assert classify_document(candidates, filename="aadhaar_card_pic.png", content_type="image/png") == "aadhaar"


def test_worker_pan_image_processes_and_merges(tmp_path):
    # 6. Verify that an image upload with PAN data gets classified as pan,
    # auto-merges fields, and does not get confused with profile photograph
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.auth import Principal
    from app.jobs import ExtractionJob, claim, enqueue
    from app.models import Base, Case, Document, utcnow
    from app.worker import process_one

    engine = create_engine("sqlite:///" + str(tmp_path / "pan_worker.db"))
    Base.metadata.create_all(engine)
    principal = Principal("worker", "tenant-1", frozenset({"HR"}))

    with Session(engine) as db:
        case = Case(
            tenant_id="tenant-1",
            owner_id="employee",
            department="engineering",
            consent_at=utcnow(),
            status="extracting",
            data={},
        )
        db.add(case)
        db.flush()

        doc = Document(
            case_id=case.id,
            tenant_id="tenant-1",
            filename="WhatsApp Image 2026-08-31 at 6.45.52 PM.jpeg",
            content_type="image/jpeg",
            storage_key="test/key",
            sha256="1" * 64,
            scan_status="clean",
            doc_type="other", # Initially uploaded as other
        )
        db.add(doc)
        db.flush()
        enqueue(db, principal, case, doc)
        db.commit()
        case_id = case.id
        doc_id = doc.id

    # Simulated OCR extraction returning PAN data
    ocr_result = {
        "doc_type": "pan",
        "candidates": [
            {"field": "pan", "value": "ABCDE1234F", "confidence": 0.98},
            {"field": "full_name", "value": "Amit Kumar", "confidence": 0.95},
            {"field": "dob", "value": "1995-05-15", "confidence": 0.90},
        ],
        "accepted": {
            "pan": "ABCDE1234F",
            "full_name": "Amit Kumar",
            "dob": "1995-05-15",
        },
        "review_fields": [],
    }

    # Run worker with mock extractor
    processed = process_one(
        engine,
        principal,
        reader=lambda d: b"fake-pan-image-bytes",
        extractor=lambda content, doc_id: ocr_result,
    )
    assert processed is True

    # Verify database state
    with Session(engine) as db:
        updated_doc = db.get(Document, doc_id)
        assert updated_doc.doc_type == "pan", f"Expected doc_type 'pan', got '{updated_doc.doc_type}'"

        updated_case = db.get(Case, case_id)
        assert updated_case.data.get("pan") == "ABCDE1234F"
        assert updated_case.data.get("full_name") == "Amit Kumar"
        assert updated_case.data.get("dob") == "1995-05-15"
