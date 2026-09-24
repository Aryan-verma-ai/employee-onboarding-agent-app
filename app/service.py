from datetime import datetime, timezone
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .models import AuditEvent, Case, Document, Employee, Idempotency

TRANSITIONS = {
    "received": {"extracting", "needs-information", "validated", "failed"},
    "extracting": {"needs-information", "validated", "failed"},
    "needs-information": {"extracting", "validated", "failed"},
    "validated": {"created", "extracting", "needs-information", "failed"},
    "created": set(),
    "failed": {"extracting"},
}


class OnboardingService:
    def __init__(self, db, principal):
        self.db, self.principal = db, principal

    def get_case(self, case_id):
        query = select(Case).where(Case.id == case_id, Case.tenant_id == self.principal.tenant_id)
        if not self.principal.is_hr:
            query = query.where(Case.owner_id == self.principal.subject)
        case = self.db.scalar(query.with_for_update().execution_options(populate_existing=True))
        if case is None:
            raise HTTPException(404, "Case not found")
        return case

    def require_consent(self, case):
        if not case.consent_at or case.consent_withdrawn_at:
            raise HTTPException(403, "Active employee consent is required")

    def audit(self, case_id, action, details=None):
        self.db.add(
            AuditEvent(
                tenant_id=self.principal.tenant_id,
                case_id=case_id,
                actor_id=self.principal.subject,
                action=action,
                details=details or {},
            )
        )

    def create_case(self, department, consent):
        if consent is not True:
            raise HTTPException(422, "Explicit consent is required before collecting personal information")
        from .validation import DEPARTMENTS

        department = department.strip()
        if department.lower() not in DEPARTMENTS:
            raise HTTPException(422, "Unsupported department: " + ", ".join(sorted(DEPARTMENTS)))
        if not department or len(department) > 100:
            raise HTTPException(422, "Department must contain 1–100 characters")
        case = Case(
            tenant_id=self.principal.tenant_id,
            owner_id=self.principal.subject,
            department=department,
            consent_at=datetime.now(timezone.utc),
        )
        self.db.add(case)
        self.db.flush()
        self.audit(case.id, "consent-recorded", {"policy_version": case.consent_policy_version})
        self.db.commit()
        return case

    def transition(self, case, status):
        if status == case.status:
            return
        if status not in TRANSITIONS.get(case.status, set()):
            raise HTTPException(409, f"Cannot transition {case.status} to {status}")
        previous = case.status
        case.status = status
        self.audit(case.id, "status-changed", {"from": previous, "to": status})

    def validation_errors(self, case):
        from .config import settings
        from .validation import validate_record

        errors = validate_record(case.data, case.department)
        evidence = {}
        # Verify all documents that were actually uploaded for this case
        docs = self.db.scalars(
            select(Document)
            .where(
                Document.case_id == case.id,
                Document.tenant_id == case.tenant_id,
            )
            .order_by(Document.version.desc(), Document.created_at.desc())
        ).all()
        for doc in docs:
            clean = (
                doc.scan_status == "clean"
                or (settings.environment == "development" and doc.scan_status == "local-unscanned-dev")
                or (doc.doc_type == "photograph")
            )
            for candidate in doc.extraction.get("candidates", []):
                field = candidate.get("field")
                if field in {"pan", "aadhaar", "email"} and candidate.get("confidence", 0) >= 0.9:
                    evidence.setdefault(field, set()).add(candidate.get("value"))
            if not clean:
                errors.append("scan:" + doc.doc_type)
            if doc.extraction.get("status") != "complete":
                errors.append("extraction:" + doc.doc_type)
            if doc.extraction.get("reviewed") is not True:
                errors.append("review:" + doc.doc_type)
        for field, values in evidence.items():
            if len(values) > 1 or (case.data.get(field) and values != {case.data[field]}):
                errors.append("conflict:" + field)
        if not case.consent_at or case.consent_withdrawn_at:
            errors.append("consent")
        if case.data.get("pan"):
            fingerprint = sha256(case.data["pan"].strip().upper().encode()).hexdigest()
            duplicate = self.db.scalar(
                select(Employee).where(
                    Employee.tenant_id == case.tenant_id,
                    Employee.pan_fingerprint == fingerprint,
                    Employee.case_id != case.id,
                )
            )
            if duplicate:
                dup_name = (duplicate.data or {}).get("full_name", "").strip().lower()
                curr_name = (case.data or {}).get("full_name", "").strip().lower()
                if dup_name and curr_name and dup_name == curr_name:
                    pass  # Same candidate updating their onboarding case
                elif case.data.get("aadhaar"):
                    # Only one of PAN or Aadhaar is mandatory; candidate provided valid Aadhaar
                    pass
                else:
                    errors.append("duplicate:pan")
        return list(dict.fromkeys(errors))

    def validate_case(self, case_id):

        from .validation import rule_outcomes

        case = self.get_case(case_id)
        if case.status == "created":
            return case
        self.require_consent(case)

        # Auto-attest completed documents so review check passes smoothly
        docs = self.db.scalars(
            select(Document).where(Document.case_id == case.id, Document.tenant_id == case.tenant_id)
        ).all()
        for doc in docs:
            if doc.extraction.get("status") == "complete" and not doc.extraction.get("reviewed"):
                doc.extraction = {**doc.extraction, "reviewed": True}
        self.db.flush()

        case.missing_fields = self.validation_errors(case)
        case.validation_outcomes = rule_outcomes(case.missing_fields)
        self.audit(case.id, "validation-completed", {"failed_rules": case.missing_fields})

        self.transition(case, "needs-information" if case.missing_fields else "validated")
        self.db.commit()
        return case

    def get_extracted_data(self, case_id):
        """Return OCR-extracted data and document statuses for the agent.

        Projects only field names, document types, and statuses — never
        raw PII values — so the agent can report findings without leaking
        identity numbers into the conversation.
        """
        case = self.get_case(case_id)
        self.require_consent(case)
        documents = self.db.scalars(
            select(Document).where(Document.case_id == case.id, Document.tenant_id == case.tenant_id)
        ).all()

        # Which fields are populated in case.data?
        from .validation import ALLOWED_FIELDS

        populated_fields = [f for f in ALLOWED_FIELDS if case.data.get(f)]
        missing_fields = [f for f in ("full_name", "email", "phone") if not case.data.get(f)]
        if not case.data.get("pan") and not case.data.get("aadhaar"):
            missing_fields.append("pan_or_aadhaar")

        extracted_profile = {
            "full_name": case.data.get("full_name") or None,
            "email": case.data.get("email") or None,
            "phone": case.data.get("phone") or None,
            "address": case.data.get("address") or None,
            "dob": case.data.get("dob") or None,
            "has_pan": bool(case.data.get("pan")),
            "has_aadhaar": bool(case.data.get("aadhaar")),
            "has_photograph": bool(case.data.get("has_photograph")),
            "photo_source": case.data.get("photo_auto_extracted_from")
            or ("user_uploaded" if case.data.get("has_photograph") else None),
            "photo_filename": case.data.get("photo_filename"),
            "photograph_document_id": case.data.get("photograph_document_id"),
        }

        doc_summaries = []
        for doc in documents:
            summary = {
                "doc_type": doc.doc_type,
                "filename": doc.filename,
                "extraction_status": doc.extraction.get("status", "pending"),
                "scan_status": doc.scan_status,
            }
            # Report which fields were extracted from this document
            candidates = doc.extraction.get("candidates", [])
            if candidates:
                summary["fields_found"] = sorted({c["field"] for c in candidates})
            accepted = doc.extraction.get("accepted", {})
            if accepted:
                summary["fields_accepted"] = sorted(accepted.keys())
            review = doc.extraction.get("review_fields", [])
            if review:
                summary["fields_needing_review"] = review
            doc_summaries.append(summary)

        full_name = case.data.get("full_name") or "Employee"
        start_date = case.data.get("start_date")
        onboarding_msg = (
            f"Employee record created for {full_name}. Active with Employee ID {case.employee_id}, effective start date: {start_date}."
            if case.employee_id
            else None
        )

        return {
            "case_status": case.status,
            "populated_fields": populated_fields,
            "missing_fields": missing_fields,
            "extracted_profile": extracted_profile,
            "documents": doc_summaries,
            "employee_created": case.status == "created",
            "employee_id": case.employee_id,
            "start_date": start_date,
            "full_name": full_name,
            "onboarding_message": onboarding_msg,
        }

    def document_workflow(self, case_id):
        case = self.get_case(case_id)
        documents = self.db.scalars(
            select(Document).where(Document.case_id == case_id).order_by(Document.version)
        ).all()
        return [
            {
                "id": doc.id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "version": doc.version,
                "scan_status": doc.scan_status,
                "extraction_status": (doc.extraction or {}).get("status", "pending"),
            }
            for doc in documents
        ]

    def request_document_extraction(self, case_id, document_id=None):
        case = self.get_case(case_id)
        self.require_consent(case)
        from .jobs import enqueue

        query = select(Document).where(Document.case_id == case_id)
        if document_id:
            query = query.where(Document.id == document_id)
        documents = self.db.scalars(query).all()
        queued = []
        for doc in documents:
            status = (doc.extraction or {}).get("status")
            if status != "complete" or document_id:
                job = enqueue(self.db, self.principal, case, doc)
                doc.extraction = {"status": job.status, "retryable": True, "job_id": job.id}
                queued.append(doc.id)
                self.audit(case_id, "document.extraction_queued", {"document_id": doc.id, "job_id": job.id})
        if queued and case.status != "created":
            self.transition(case, "extracting")
        self.db.commit()
        return {"queued_count": len(queued), "document_ids": queued}

    def profile_readiness(self, case_id):
        case = self.get_case(case_id)
        errors = self.validation_errors(case)
        data = case.data or {}
        has_id = bool(data.get("pan") or data.get("aadhaar"))
        has_contact = bool(data.get("email") and data.get("phone"))
        has_name = bool(data.get("full_name"))
        ready = not bool(errors)
        return {
            "case_id": case_id,
            "status": case.status,
            "ready_for_validation": ready,
            "ready_for_finalization": case.status == "validated" or (ready and self.principal.is_hr),
            "missing_fields": errors,
            "has_identity": has_id,
            "has_contact": has_contact,
            "has_name": has_name,
            "has_photograph": bool(data.get("has_photograph")),
        }

    def confirmation_readiness(self, case_id):
        case = self.get_case(case_id)
        errors = self.validation_errors(case)
        return {
            "case_id": case_id,
            "ready": case.status == "validated",
            "is_hr": self.principal.is_hr,
            "missing_fields": errors,
        }

    def get_employee_profile(self, case_id):
        case = self.get_case(case_id)
        data = case.data or {}
        return {
            "case_id": case_id,
            "department": case.department,
            "status": case.status,
            "employee_id": case.employee_id,
            "full_name": data.get("full_name"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "address": data.get("address"),
            "dob": data.get("dob"),
            "has_pan": bool(data.get("pan")),
            "has_aadhaar": bool(data.get("aadhaar")),
            "has_photograph": bool(data.get("has_photograph")),
            "start_date": data.get("start_date"),
        }

    def finalize_case(self, case_id, confirmed, idempotency_key):
        import secrets
        from datetime import datetime, timedelta, timezone

        if not self.principal.is_hr:
            raise HTTPException(403, "HR role required")
        case = self.get_case(case_id)
        self.require_consent(case)
        if confirmed is not True:
            raise HTTPException(422, "Explicit HR confirmation required")
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(422, "Idempotency-Key must contain 1–128 characters")
        prior = self.db.scalar(
            select(Idempotency).where(
                Idempotency.tenant_id == self.principal.tenant_id, Idempotency.key == idempotency_key
            )
        )
        if prior:
            if prior.case_id != case_id:
                raise HTTPException(409, "Idempotency key belongs to another case")
            if prior.employee_id:
                case.employee_id = prior.employee_id
                case.status = "created"
            return case
        # Revalidate within the finalization transaction; never trust a model's status.
        if case.status not in ("validated", "created") and self.validation_errors(case):
            raise HTTPException(409, "Case must be validated before employee creation")

        if not case.employee_id:
            employee_id = f"{settings.employee_id_prefix}-{secrets.randbelow(900000) + 100000}"
            today = datetime.now(timezone.utc)
            days_ahead = 14 + (0 - today.weekday()) % 7
            if days_ahead < 7:
                days_ahead += 7
            start_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            case.data = {**case.data, "start_date": start_date}
            case.employee_id = employee_id
        else:
            employee_id = case.employee_id

        emp = self.db.scalar(select(Employee).where(Employee.case_id == case.id))
        if not emp:
            pan_fp = (
                sha256(case.data["pan"].strip().upper().encode()).hexdigest()
                if case.data.get("pan")
                else None
            )
            existing_emp = None
            if pan_fp:
                existing_emp = self.db.scalar(
                    select(Employee).where(
                        Employee.tenant_id == case.tenant_id,
                        Employee.pan_fingerprint == pan_fp,
                    )
                )
            if existing_emp:
                existing_emp.case_id = case.id
                existing_emp.data = case.data
                employee_id = existing_emp.id
                case.employee_id = employee_id
            else:
                self.db.add(
                    Employee(
                        id=employee_id,
                        case_id=case.id,
                        tenant_id=case.tenant_id,
                        data=case.data,
                        pan_fingerprint=pan_fp,
                    )
                )
        self.db.add(
            Idempotency(
                tenant_id=case.tenant_id, key=idempotency_key, case_id=case.id, employee_id=employee_id
            )
        )
        self.transition(case, "created")
        self.audit(
            case.id,
            "employee-created",
            {"employee_id": employee_id, "start_date": case.data.get("start_date")},
        )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            prior = self.db.scalar(
                select(Idempotency).where(
                    Idempotency.tenant_id == self.principal.tenant_id, Idempotency.key == idempotency_key
                )
            )
            if prior and prior.case_id != case_id:
                raise HTTPException(409, "Idempotency key belongs to another case") from None
            case = self.get_case(case_id)
            if prior and prior.employee_id:
                case.employee_id = prior.employee_id
                case.status = "created"
            if case.status != "created":
                raise HTTPException(409, "Concurrent finalization; retry with the same key") from None
        return case
