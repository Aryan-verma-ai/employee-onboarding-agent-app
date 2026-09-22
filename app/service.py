from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

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
        from .validation import REQUIRED_DOCUMENTS, validate_record

        errors = validate_record(case.data, case.department)
        evidence = {}
        for doc_type in REQUIRED_DOCUMENTS:
            doc = self.db.scalar(
                select(Document)
                .where(
                    Document.case_id == case.id,
                    Document.tenant_id == case.tenant_id,
                    Document.doc_type == doc_type,
                )
                .order_by(Document.version.desc(), Document.created_at.desc())
            )
            if not doc:
                errors.append("document:" + doc_type)
                continue
            clean = doc.scan_status == "clean" or (
                settings.environment == "development" and doc.scan_status == "local-unscanned-dev"
            )
            for candidate in doc.extraction.get("candidates", []):
                field = candidate.get("field")
                if field in {"pan", "aadhaar", "email"} and candidate.get("confidence", 0) >= 0.9:
                    evidence.setdefault(field, set()).add(candidate.get("value"))
            if not clean:
                errors.append("scan:" + doc_type)
            if doc.extraction.get("status") != "complete":
                errors.append("extraction:" + doc_type)
            if doc.extraction.get("reviewed") is not True:
                errors.append("review:" + doc_type)
        for field, values in evidence.items():
            if len(values) > 1 or (case.data.get(field) and values != {case.data[field]}):
                errors.append("conflict:" + field)
        if not case.consent_at or case.consent_withdrawn_at:
            errors.append("consent")
        if case.data.get("pan"):
            fingerprint = sha256(case.data["pan"].strip().upper().encode()).hexdigest()
            duplicate = self.db.scalar(
                select(Employee.id).where(
                    Employee.tenant_id == case.tenant_id,
                    Employee.pan_fingerprint == fingerprint,
                    Employee.case_id != case.id,
                )
            )
            if duplicate:
                errors.append("duplicate:pan")
        return list(dict.fromkeys(errors))

    def validate_case(self, case_id):
        case = self.get_case(case_id)
        if case.status == "created":
            return case
        self.require_consent(case)
        from .validation import rule_outcomes

        case.missing_fields = self.validation_errors(case)
        case.validation_outcomes = rule_outcomes(case.missing_fields)
        self.audit(case.id, "validation-completed", {"failed_rules": case.missing_fields})
        self.transition(case, "needs-information" if case.missing_fields else "validated")
        self.db.commit()
        return case

    def document_workflow(self, case_id):
        """Return document-processing facts suitable for an agent, without document contents."""
        case = self.get_case(case_id)
        self.require_consent(case)
        from .validation import REQUIRED_DOCUMENTS

        documents = self.db.scalars(
            select(Document)
            .where(Document.case_id == case.id, Document.tenant_id == case.tenant_id)
            .order_by(Document.doc_type, Document.version.desc(), Document.created_at.desc())
        ).all()
        latest = {}
        for document in documents:
            latest.setdefault(document.doc_type, document)

        def summary(document):
            extraction = document.extraction or {}
            candidates = extraction.get("candidates", [])
            fields = sorted(
                {
                    candidate.get("field")
                    for candidate in candidates
                    if isinstance(candidate, dict)
                    and isinstance(candidate.get("field"), str)
                    and candidate["field"] in {"full_name", "email", "phone", "pan", "aadhaar"}
                }
            )
            return {
                "doc_type": document.doc_type,
                "version": document.version,
                "scan_status": document.scan_status,
                "extraction_status": extraction.get("status", "pending"),
                "reviewed": extraction.get("reviewed") is True,
                "candidate_fields": fields,
            }

        return {
            "documents": [summary(document) for document in latest.values()],
            "missing_required_documents": [name for name in REQUIRED_DOCUMENTS if name not in latest],
        }

    def request_document_extraction(self, case_id):
        """Queue real extraction work for documents that have not completed successfully."""
        from .jobs import enqueue

        case = self.get_case(case_id)
        self.require_consent(case)
        if case.status == "created":
            raise HTTPException(409, "Completed cases are immutable")
        documents = self.db.scalars(
            select(Document).where(Document.case_id == case.id, Document.tenant_id == case.tenant_id)
        ).all()
        queued = []
        already_complete = []
        for document in documents:
            extraction = document.extraction or {}
            if extraction.get("status") == "complete":
                already_complete.append(document.doc_type)
                continue
            job = enqueue(self.db, self.principal, case, document)
            document.extraction = {"status": job.status, "retryable": True, "job_id": job.id}
            queued.append(document.doc_type)
            self.audit(case.id, "document.extraction_queued", {"document_id": document.id, "job_id": job.id})
        if queued:
            self.transition(case, "extracting")
        self.db.commit()
        return {
            "queued_document_types": sorted(queued),
            "already_complete_document_types": sorted(already_complete),
            "message": "Extraction is asynchronous; inspect documents or case status for completion.",
        }

    def profile_readiness(self, case_id):
        """Build a non-PII profile readiness view; authoritative fields remain HR-controlled."""
        from .validation import REQUIRED_FIELDS

        case = self.get_case(case_id)
        self.require_consent(case)
        present = [field for field in REQUIRED_FIELDS if isinstance(case.data.get(field), str) and case.data[field].strip()]
        errors = self.validation_errors(case)
        return {
            "department": case.department,
            "profile_fields_present": present,
            "profile_fields_missing": [field for field in REQUIRED_FIELDS if field not in present],
            "document_validation_issues": [
                issue
                for issue in errors
                if issue.startswith(("document:", "scan:", "extraction:", "review:", "conflict:"))
            ],
            "authoritative_profile_saved": len(present) == len(REQUIRED_FIELDS),
            "message": "The agent does not write OCR values into the employee record; HR uses the authenticated form to review and save authoritative data.",
        }

    def confirmation_readiness(self, case_id):
        """State whether the HR-only finalization endpoint may be presented, never invoke it."""
        case = self.get_case(case_id)
        self.require_consent(case)
        errors = self.validation_errors(case)
        return {
            "case_status": case.status,
            "employee_created": case.status == "created",
            "ready_for_hr_confirmation": case.status == "validated" and not errors,
            "blocking_issues": errors,
            "required_action": (
                "HR must use the authenticated Create employee action and explicitly confirm."
                if case.status != "created"
                else "Employee has already been created."
            ),
        }

    def finalize_case(self, case_id, confirmed, idempotency_key):
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
            return case
        # Revalidate within the finalization transaction; never trust a model's status.
        if case.status != "validated" or self.validation_errors(case):
            if case.status == "created":
                return case
            raise HTTPException(409, "Case must be validated before employee creation")
        employee_id = settings.employee_id_prefix + "-" + uuid4().hex.upper()
        self.db.add(
            Employee(
                id=employee_id,
                case_id=case.id,
                tenant_id=case.tenant_id,
                data=case.data,
                pan_fingerprint=sha256(case.data["pan"].strip().upper().encode()).hexdigest()
                if case.data.get("pan")
                else None,
            )
        )
        self.db.add(
            Idempotency(
                tenant_id=case.tenant_id, key=idempotency_key, case_id=case.id, employee_id=employee_id
            )
        )
        case.employee_id = employee_id
        self.transition(case, "created")
        self.audit(case.id, "employee-created", {"employee_id": employee_id})
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
            if case.status != "created":
                raise HTTPException(409, "Concurrent finalization; retry with the same key") from None
        return case
