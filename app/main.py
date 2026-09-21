from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel, Field

from .auth import get_principal
from .config import settings, validate_configuration
from .dashboard import router as dashboard_router
from .db import engine, get_db
from .documents import router as documents_router
from .foundry import router as foundry_router
from .models import Base
from .service import OnboardingService


@asynccontextmanager
async def lifespan(app):
    validate_configuration()
    if settings.environment == "development":
        Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Foundry Employee Onboarding", lifespan=lifespan)


def service(db=Depends(get_db), principal=Depends(get_principal)):
    return OnboardingService(db, principal)


class CreateCase(BaseModel):
    department: str = Field(min_length=1, max_length=100)
    consent: bool


class Confirm(BaseModel):
    confirmed: bool


def case_response(case):
    return {
        "id": case.id,
        "department": case.department,
        "status": case.status,
        "data": case.data,
        "missing_fields": case.missing_fields,
        "employee_id": case.employee_id,
        "created_at": case.created_at,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/cases", status_code=201)
def create_case(body: CreateCase, svc=Depends(service)):
    return case_response(svc.create_case(body.department, body.consent))


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, svc=Depends(service)):
    return case_response(svc.get_case(case_id))


@app.post("/api/cases/{case_id}/validate")
def validate_case(case_id: str, svc=Depends(service)):
    return case_response(svc.validate_case(case_id))


@app.post("/api/cases/{case_id}/finalize")
def finalize_case(
    case_id: str, body: Confirm, idempotency_key: str = Header(alias="Idempotency-Key"), svc=Depends(service)
):
    return case_response(svc.finalize_case(case_id, body.confirmed, idempotency_key))


app.include_router(documents_router)
app.include_router(dashboard_router)
app.include_router(foundry_router)


class UpdateCase(BaseModel):
    data: dict[str, str] = Field(default_factory=dict)
    reviewed_document_ids: list[str] = Field(default_factory=list)


@app.patch("/api/cases/{case_id}")
def update_case(case_id: str, body: UpdateCase, svc=Depends(service)):
    from fastapi import HTTPException
    from sqlalchemy import select

    from .models import Document
    from .validation import REQUIRED_FIELDS

    case = svc.get_case(case_id)
    if case.status == "created":
        raise HTTPException(409, "Completed cases are immutable")
    if set(body.data) - set(REQUIRED_FIELDS) or any(len(value) > 500 for value in body.data.values()):
        raise HTTPException(422, "Unsupported field or excessive field length")
    if body.reviewed_document_ids and not svc.principal.is_hr:
        raise HTTPException(403, "Only HR can attest document review")
    docs = []
    for document_id in set(body.reviewed_document_ids):
        doc = svc.db.scalar(
            select(Document).where(
                Document.id == document_id, Document.case_id == case.id, Document.tenant_id == case.tenant_id
            )
        )
        if not doc or doc.extraction.get("status") != "complete":
            raise HTTPException(
                422, "Reviewed document must belong to this case and have completed extraction"
            )
        docs.append(doc)
    if any(case.data.get(key) != value for key, value in body.data.items()):
        # Corrections invalidate prior attestations; HR must review new values.
        for old in svc.db.scalars(select(Document).where(Document.case_id == case.id)):
            old.extraction = {**old.extraction, "reviewed": False}
    case.data = {**case.data, **body.data}
    for doc in docs:
        doc.extraction = {**doc.extraction, "reviewed": True}
    if case.status == "failed":
        svc.transition(case, "extracting")
    svc.transition(case, "needs-information")
    svc.audit(
        case.id,
        "record-corrected",
        {"fields": sorted(body.data), "reviewed_document_ids": sorted(body.reviewed_document_ids)},
    )
    svc.db.commit()
    return case_response(case)


@app.get("/ready")
def ready():
    from fastapi import HTTPException
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        validate_configuration()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM onboarding_cases LIMIT 0"))
    except (RuntimeError, SQLAlchemyError):
        raise HTTPException(503, "Service configuration or database migration is not ready") from None
    return {"status": "ready", "cloud_connectivity": "not_probed"}
