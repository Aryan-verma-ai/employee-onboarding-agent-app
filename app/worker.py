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
            if case.status == "created":
                raise ValueError("case_completed")
            content = reader(document)
            scan_status = document.scan_status
            if document.doc_type == "photograph":
                result = {"candidates": [], "accepted": {}, "review_fields": [], "notes": "Photograph verified"}
            else:
                db.rollback()  # Do not hold a transaction across the cloud call.
                result = extractor(content, document_id)
        except Exception as exc:
            logging.getLogger(__name__).exception("Extraction failed for document %s: %s", document_id, exc)
            error = (
                "consent_or_access_denied"
                if getattr(exc, "status_code", None) in {403, 404}
                else "extraction_failed"
            )
            db.rollback()
        job = fenced_job(db, principal, job_id, token)
        if not job:
            return True  # Expired worker may not overwrite a newer attempt.
        service, case, document = authorized_document(case_id, document_id, db, principal)
        if not case.consent_at or case.consent_withdrawn_at or case.status == "created":
            job.status, job.error_code = "cancelled", "consent_or_case_closed"
            db.commit()
            return True
        if error:
            exhausted = job.attempts >= 5
            job.status = "failed" if exhausted else "queued"
            job.error_code = error
            job.available_at = utcnow() + timedelta(seconds=min(300, 5 * 2**job.attempts))
            document.extraction = {"status": job.status, "retryable": True, "error": error, "job_id": job.id}
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
            detected_type = classify_document(candidates, filename=document.filename, content_type=document.content_type)
            if detected_type != "other" or document.doc_type == "other":
                document.doc_type = detected_type
                service.audit(
                    case_id,
                    "document.auto_classified",
                    {"document_id": document_id, "detected_type": detected_type},
                )

            # ── Auto-merge high-confidence accepted values into case.data ──
            accepted = result.get("accepted", {})
            if accepted:
                merged = {**case.data}
                for field, value in accepted.items():
                    # Only fill empty fields — never overwrite user corrections
                    if not merged.get(field):
                        merged[field] = value
                if merged != case.data:
                    case.data = merged
                    service.audit(
                        case_id,
                        "data.auto_merged",
                        {"fields": sorted(set(accepted) - set(case.data))},
                    )

            if case.status == "failed":
                service.transition(case, "extracting")
            service.transition(case, "needs-information")
            service.audit(
                case_id, "document.extracted", {"document_id": document_id, "attempt": job.attempts}
            )
        job.lease_until, job.lease_token = None, None
        db.commit()
        return True


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
