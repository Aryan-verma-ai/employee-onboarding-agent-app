"""Test verification that PAN and Aadhaar photos/scans are accurately classified and extracted."""

import pytest
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


def test_is_valid_candidate_email():
    from app.extraction import is_valid_candidate_email

    # Allowed candidate personal emails
    assert is_valid_candidate_email("candidate@gmail.com") is True
    assert is_valid_candidate_email("user.name@outlook.com") is True
    assert is_valid_candidate_email("john_doe@yahoo.com") is True
    assert is_valid_candidate_email("neelabh@yahoo.in") is True
    assert is_valid_candidate_email("test@hotmail.com") is True
    assert is_valid_candidate_email("test@icloud.com") is True

    # Disallowed helpline / support / government emails
    assert is_valid_candidate_email("help@uidai.gov.in") is False
    assert is_valid_candidate_email("support@uidai.gov.in") is False
    assert is_valid_candidate_email("helpdesk@incometax.gov.in") is False
    assert is_valid_candidate_email("info@randomcompany.org") is False
    assert is_valid_candidate_email("noreply@service.com") is False
    assert is_valid_candidate_email("") is False
    assert is_valid_candidate_email(None) is False


def test_is_valid_indian_phone():
    from app.extraction import is_valid_indian_phone

    # Valid 10-digit Indian mobiles
    assert is_valid_indian_phone("9876543210") is True
    assert is_valid_indian_phone("+91 9876543210") is True
    assert is_valid_indian_phone("+91-8123456789") is True
    assert is_valid_indian_phone("7123456789") is True
    assert is_valid_indian_phone("6123456789") is True

    # Invalid phones: 12-digit Aadhaar number
    assert is_valid_indian_phone("429689817829") is False
    assert is_valid_indian_phone("4296 8981 7829", aadhaar_val="429689817829") is False
    assert is_valid_indian_phone("9876543210", aadhaar_val="987654321099") is False  # Substring of Aadhaar
    # Helpline
    assert is_valid_indian_phone("1947") is False
    # Starting with 1-5
    assert is_valid_indian_phone("1234567890") is False
    assert is_valid_indian_phone("5123456789") is False


def test_clean_full_name():
    from app.extraction import clean_full_name

    # Bilingual Gurmukhi + English name on Aadhaar
    assert clean_full_name("ਠੀਲਾਵ Neelabh नरम भिडी") == "Neelabh"
    assert clean_full_name("Neelabh DOB: 01/06/2006 MALE") == "Neelabh"
    assert clean_full_name("C/O: Nitin Karnwal") == "Nitin Karnwal"
    assert clean_full_name("Amit Kumar S/O Ramesh Kumar") == "Amit Kumar Ramesh Kumar"
    assert clean_full_name("Rahul Sharma") == "Rahul Sharma"


def test_aadhaar_ocr_extraction_filters_uidai_email_and_aadhaar_phone():
    from app.extraction import candidates_from_result
    from types import SimpleNamespace as Obj

    # Simulate OCR lines from real Aadhaar card back
    back_lines = [
        "Address: C/O: Nitin Karnwal, 237 B, Ansal Enclave, Ludhiana, Punjab - 141010",
        "4296 8981 7829",
        "1947",
        "help@uidai.gov.in",
        "Download Date: 11/03/2022",
    ]
    words = []
    ocr_lines = []
    offset = 0
    for text in back_lines:
        span = Obj(offset=offset, length=len(text))
        words.append(Obj(span=span, confidence=0.98))
        ocr_lines.append(Obj(content=text, spans=[span]))
        offset += len(text) + 1

    result = candidates_from_result(
        Obj(pages=[Obj(page_number=1, words=words, lines=ocr_lines)]), "test-aadhaar-back"
    )

    accepted = result.get("accepted", {})
    # Aadhaar should be extracted
    assert accepted.get("aadhaar") == "429689817829"
    # help@uidai.gov.in MUST NOT be accepted as candidate email!
    assert "email" not in accepted
    assert not any(c["field"] == "email" for c in result["candidates"])
    # 4296 8981 7829 or 1947 MUST NOT be accepted as phone number!
    assert "phone" not in accepted
    assert not any(c["field"] == "phone" for c in result["candidates"])
    # Download date MUST NOT be accepted as DOB!
    assert "dob" not in accepted


@pytest.mark.anyio
async def test_photo_upload_allowed_on_completed_case(tmp_path, monkeypatch):
    from app.models import Base, Case, utcnow
    from app.auth import Principal
    from app.documents import auto_upload_and_extract
    from fastapi import UploadFile, HTTPException, BackgroundTasks
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    import io

    # Mock storage
    monkeypatch.setattr("app.documents.store_content", lambda key, content, content_type: "clean")

    engine = create_engine("sqlite:///" + str(tmp_path / "completed_upload.db"))
    Base.metadata.create_all(engine)
    principal = Principal("hr-user", "tenant-1", frozenset({"HR"}))

    with Session(engine) as db:
        case = Case(
            tenant_id="tenant-1",
            owner_id="hr-user",
            department="engineering",
            consent_at=utcnow(),
            status="created", # Finalized case
            employee_id="EMP-123456",
            data={"full_name": "Test Employee", "email": "test@gmail.com"},
        )
        db.add(case)
        db.commit()
        case_id = case.id

    # 1. Uploading a photo on completed case should SUCCEED
    photo_file = UploadFile(
        filename="WhatsApp Image 2026-08-31 at 6.45.52 PM.jpeg",
        file=io.BytesIO(b"\xff\xd8\xff\xe0" + b"x" * 100),
        headers={"content-type": "image/jpeg"},
    )
    with Session(engine) as db:
        bg = BackgroundTasks()
        res = await auto_upload_and_extract(case_id, bg, photo_file, db=db, principal=principal)
        assert res["doc_type"] == "photograph"
        assert res["extraction_status"] == "complete"

        # Verify case.data updated with photo
        updated = db.get(Case, case_id)
        assert updated.data.get("has_photograph") is True
        assert updated.data.get("photograph_document_id") == res["id"]

    # 2. Uploading a document (e.g. updated resume) on completed case is also permitted
    pdf_file = UploadFile(
        filename="resume.pdf",
        file=io.BytesIO(b"%PDF-1.4" + b"x" * 100),
        headers={"content-type": "application/pdf"},
    )
    with Session(engine) as db:
        bg = BackgroundTasks()
        res_pdf = await auto_upload_and_extract(case_id, bg, pdf_file, db=db, principal=principal)
        assert res_pdf["doc_type"] == "resume"

    # 3. PATCHing every field on completed case should succeed and update Employee record
    from app.main import update_case, UpdateCase
    from app.service import OnboardingService
    from app.models import Employee
    from sqlalchemy import select
    import hashlib

    # First ensure Employee record exists
    with Session(engine) as db:
        emp = Employee(
            id="EMP-123456",
            case_id=case_id,
            tenant_id="tenant-1",
            data={"full_name": "Test Employee", "email": "test@gmail.com"},
        )
        db.add(emp)
        db.commit()

    all_fields_patch = {
        "full_name": "Vikramaditya Sharma",
        "email": "vikram.sharma@gmail.com",
        "phone": "+919876543210",
        "pan": "ABCDE1234F",
        "aadhaar": "987654321098",
        "dob": "1992-08-25",
        "address": "456 Silicon Valley Boulevard, Bengaluru",
        "start_date": "2026-11-01",
        "department": "finance",
    }
    with Session(engine) as db:
        svc = OnboardingService(db, principal)
        body = UpdateCase(data=all_fields_patch)
        updated_res = update_case(case_id, body, svc=svc)
        assert updated_res["status"] == "created"
        assert updated_res["department"] == "finance"
        for k, v in all_fields_patch.items():
            if k == "department":
                continue
            assert updated_res["data"][k] == v

        # Check DB Case & Employee records
        db_case = db.get(Case, case_id)
        assert db_case.department == "finance"
        assert db_case.data["full_name"] == "Vikramaditya Sharma"
        assert db_case.data["address"] == "456 Silicon Valley Boulevard, Bengaluru"

        db_emp = db.scalar(select(Employee).where(Employee.case_id == case_id))
        assert db_emp is not None
        assert db_emp.data["full_name"] == "Vikramaditya Sharma"
        assert db_emp.data["email"] == "vikram.sharma@gmail.com"
        assert db_emp.data["pan"] == "ABCDE1234F"
        assert db_emp.data["address"] == "456 Silicon Valley Boulevard, Bengaluru"
        assert db_emp.pan_fingerprint == hashlib.sha256(b"ABCDE1234F").hexdigest()



