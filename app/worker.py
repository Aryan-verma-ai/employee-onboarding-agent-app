"""Run one RLS-scoped worker per configured tenant: python -m app.worker."""

import logging
import os
import time
from datetime import timedelta

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .auth import Principal
from .db import engine
from .jobs import ExtractionJob, claim, fenced_job
from .models import utcnow


def process_one(bind, principal, reader=None, extractor=None, now=None):
    from .documents import authorized_document, read_clean_content
    from .extraction import extract_document

    reader, extractor = reader or read_clean_content, extractor or extract_document
    with Session(bind) as db:
        db.info["principal"] = principal
        lease = claim(db, principal, now=now)
        if not lease:
            return False
        job_id, token = lease
        job = db.get(ExtractionJob, job_id)
        case_id, document_id = job.case_id, job.document_id
        result, error, scan_status = None, None, None
        try:
            service, case, document = authorized_document(case_id, document_id, db, principal)
            service.require_consent(case)
            content = reader(document)
            scan_status = document.scan_status
            if document.doc_type == "photograph":
                result = {
                    "candidates": [],
                    "accepted": {},
                    "review_fields": [],
                    "notes": "Photograph verified",
                }
            else:
                db.rollback()  # Do not hold a transaction across the cloud call.
                result = extractor(content, document_id)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code == 409:
                logging.getLogger(__name__).info(
                    "Malware scan pending for document %s (409) — will retry shortly", document_id
                )
                error = "scan_pending"
            else:
                logging.getLogger(__name__).exception(
                    "Extraction failed for document %s: %s", document_id, exc
                )
                error = "consent_or_access_denied" if status_code in {403, 404} else "extraction_failed"
            db.rollback()
        job = fenced_job(db, principal, job_id, token)
        if not job:
            return True  # Expired worker may not overwrite a newer attempt.
        service, case, document = authorized_document(case_id, document_id, db, principal)
        if not case.consent_at or case.consent_withdrawn_at:
            job.status, job.error_code = "cancelled", "consent_or_case_closed"
            db.commit()
            return True
        if error:
            if error == "scan_pending":
                # Malware scan still in progress — retry quickly, don't count as a real failure
                scan_exhausted = job.attempts >= 20
                job.status = "failed" if scan_exhausted else "queued"
                job.error_code = error
                job.available_at = utcnow() + timedelta(seconds=15)
                document.extraction = {
                    "status": job.status,
                    "retryable": True,
                    "error": error,
                    "job_id": job.id,
                }
                if scan_exhausted:
                    service.transition(case, "failed")
                service.audit(
                    case_id,
                    "document.scan_pending_retry" if not scan_exhausted else "document.scan_pending_failed",
                    {"document_id": document_id, "attempt": job.attempts},
                )
            else:
                exhausted = job.attempts >= 5
                job.status = "failed" if exhausted else "queued"
                job.error_code = error
                job.available_at = utcnow() + timedelta(seconds=min(300, 5 * 2**job.attempts))
                document.extraction = {
                    "status": job.status,
                    "retryable": True,
                    "error": error,
                    "job_id": job.id,
                }
                if exhausted:
                    service.transition(case, "failed")
                service.audit(
                    case_id,
                    "document.extraction_retry" if not exhausted else "document.extraction_failed",
                    {"document_id": document_id, "attempt": job.attempts},
                )
        else:
            job.status, job.error_code = "complete", None
            document.scan_status = scan_status
            document.extraction = {"status": "complete", "reviewed": False, "job_id": job.id, **result}

            # ── Auto-classify document type from OCR candidates ──
            from .extraction import classify_document

            candidates = result.get("candidates", [])
            detected_type = classify_document(
                candidates, filename=document.filename, content_type=document.content_type
            )
            if detected_type != "other" or document.doc_type == "other":
                document.doc_type = detected_type
                if detected_type == "photograph":
                    case_data = dict(case.data or {})
                    case_data["has_photograph"] = True
                    case_data["photograph_document_id"] = document.id
                    case_data["photo_is_user_uploaded"] = True
                    case_data["photo_filename"] = document.filename
                    case_data.pop("photo_auto_extracted_from", None)
                    case.data = case_data
                service.audit(
                    case_id,
                    "document.auto_classified",
                    {"document_id": document_id, "detected_type": detected_type},
                )

            # ── Auto-merge high-confidence accepted values into case.data ──
            from .extraction import is_valid_candidate_email, is_valid_indian_phone

            accepted = result.get("accepted", {})
            if accepted:
                merged = {**case.data}
                aadhaar_num = merged.get("aadhaar") or accepted.get("aadhaar", "")
                for field, value in accepted.items():
                    # Reject invalid emails (e.g. uidai helpline or non-allowed domain)
                    if field == "email" and not is_valid_candidate_email(value):
                        continue
                    # Reject numbers that match aadhaar or helpline
                    if field == "phone" and not is_valid_indian_phone(value, aadhaar_val=aadhaar_num):
                        continue
                    # Only fill empty fields — never overwrite user corrections
                    if not merged.get(field):
                        merged[field] = value
                if merged != case.data:
                    case.data = merged
                    if case.status == "created":
                        import hashlib

                        from sqlalchemy import select

                        from .models import Employee

                        emp = db.scalar(select(Employee).where(Employee.case_id == case.id))
                        if emp:
                            emp.data = dict(merged)
                            if merged.get("pan"):
                                emp.pan_fingerprint = hashlib.sha256(
                                    merged["pan"].strip().upper().encode()
                                ).hexdigest()
                    service.audit(
                        case_id,
                        "data.auto_merged",
                        {"fields": sorted(set(merged) - set(case.data))},
                    )

            if case.status != "created":
                if case.status == "failed":
                    service.transition(case, "extracting")
                service.transition(case, "needs-information")
            service.audit(
                case_id, "document.extracted", {"document_id": document_id, "attempt": job.attempts}
            )

            # ── Auto-extract portrait photo from PAN / Aadhaar / Resume ──
            if document.doc_type in ("pan", "aadhaar", "resume"):
                _try_auto_extract_photo(db, principal, service, case, document)
        job.lease_until, job.lease_token = None, None
        db.commit()
        return True


def _try_auto_extract_photo(db, principal, service, case, document):
    """After successful OCR, try to extract a portrait photo from the document.

    Only runs if:
      1. The document is PAN, Aadhaar, or Resume
      2. No dedicated photograph document already exists for this case
    """
    import hashlib
    from uuid import uuid4

    from sqlalchemy import select

    from .models import Document as DocModel
    from .photo_extract import extract_photo_from_document

    case_id = case.id

    # Skip if a user-uploaded photograph already exists for this case
    if case.data and case.data.get("photo_is_user_uploaded"):
        logging.getLogger(__name__).debug(
            "User-uploaded photo already exists for case %s — skipping auto-extract", case_id
        )
        return

    # Skip if a photograph already exists for this case
    existing_photo = db.scalar(
        select(DocModel).where(
            DocModel.case_id == case_id,
            DocModel.doc_type == "photograph",
            DocModel.tenant_id == principal.tenant_id,
        )
    )
    if existing_photo:
        logging.getLogger(__name__).debug("Photo already exists for case %s — skipping auto-extract", case_id)
        return

    # Read document content
    try:
        from .documents import read_clean_content

        content = read_clean_content(document)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Cannot read document %s for photo extraction: %s", document.id, exc
        )
        return

    # Extract photo
    result = extract_photo_from_document(content, document.doc_type, document.content_type)
    if not result:
        logging.getLogger(__name__).info(
            "No portrait photo found in %s document %s", document.doc_type, document.id
        )
        return

    photo_bytes, photo_content_type = result
    digest = hashlib.sha256(photo_bytes).hexdigest()

    # Don't store duplicate by sha256
    dup = db.scalar(select(DocModel).where(DocModel.case_id == case_id, DocModel.sha256 == digest))
    if dup:
        return

    # Store the extracted photo
    photo_id = str(uuid4())
    key = f"{hashlib.sha256(principal.tenant_id.encode()).hexdigest()}/{case_id}/{photo_id}"

    from .documents import store_content

    scan_status = store_content(key, photo_bytes, photo_content_type)

    photo_doc = DocModel(
        id=photo_id,
        case_id=case_id,
        tenant_id=principal.tenant_id,
        filename=f"auto_extracted_photo_{document.doc_type}.jpg",
        content_type=photo_content_type,
        storage_key=key,
        sha256=digest,
        doc_type="photograph",
        version=1,
        scan_status=scan_status,
        extraction={
            "status": "complete",
            "reviewed": True,
            "candidates": [],
            "accepted": {},
            "doc_type": "photograph",
            "notes": f"Auto-extracted from {document.doc_type} ({document.filename})",
            "source_document_id": document.id,
            "source_doc_type": document.doc_type,
        },
        size=len(photo_bytes),
    )
    db.add(photo_doc)

    # Update case data
    case_data = dict(case.data or {})
    case_data["has_photograph"] = True
    case_data["photograph_document_id"] = photo_id
    case_data["photo_auto_extracted_from"] = document.doc_type
    case_data["photo_filename"] = photo_doc.filename
    case.data = case_data

    service.audit(
        case_id,
        "document.photo_auto_extracted",
        {
            "photo_document_id": photo_id,
            "source_document_id": document.id,
            "source_doc_type": document.doc_type,
        },
    )
    logging.getLogger(__name__).info(
        "Auto-extracted portrait photo from %s → document %s", document.doc_type, photo_id
    )


def main():
    tenant, subject = os.getenv("WORKER_TENANT_ID"), os.getenv("WORKER_SUBJECT")
    if not tenant or not subject:
        raise SystemExit("WORKER_TENANT_ID and WORKER_SUBJECT must explicitly scope the worker")
    principal = Principal(subject, tenant, frozenset({"HR"}))
    while True:
        try:
            if not process_one(engine, principal):
                time.sleep(2)
        except OperationalError:
            # Sessions close/rollback in process_one; do not log connection secrets.
            logging.getLogger(__name__).warning("Worker database unavailable; retrying in 5 seconds")
            time.sleep(5)


if __name__ == "__main__":
    main()
