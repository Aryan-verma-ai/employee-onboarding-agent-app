"""Tenant-scoped HR review and deliberately minimal exports."""

import csv
import io
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .auth import Principal, get_principal
from .db import get_db
from .jobs import ExtractionJob
from .models import AuditEvent, Case, Document, Employee, Idempotency
from .service import OnboardingService

logger = logging.getLogger("onboarding.dashboard")

router = APIRouter(tags=["hr"])
EXPORT_FIELDS = ("case_id", "employee_id", "department", "status", "full_name", "email")


def hr_principal(principal: Principal = Depends(get_principal)):
    if not set(principal.roles) & {"HR", "Onboarding.HR"}:
        raise HTTPException(403, "HR role required")
    return principal


def safe_cell(value):
    value = str(value or "")
    # Spreadsheet applications may trim whitespace before evaluating formulas.
    return (
        "'" + value
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
        else value
    )


def summary(case):
    return {
        "id": case.id,
        "case_id": case.id,
        "employee_id": case.employee_id,
        "department": case.department,
        "status": case.status,
        "full_name": case.data.get("full_name", ""),
        "email": case.data.get("email", ""),
        "missing_fields": case.missing_fields,
        "created_at": case.created_at.isoformat() if case.created_at else "",
    }


@router.get("/hr", response_class=HTMLResponse)
def dashboard_shell():
    # This shell contains no personal data. Every data endpoint validates Entra.
    return (
        Path(__file__)
        .with_name("static")
        .joinpath("dashboard.html")
        .read_text(encoding="utf-8", errors="replace")
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing_page():
    """Public product entry point; protected workflows remain under /hr."""
    return Path(__file__).with_name("static").joinpath("landing.html").read_text(encoding="utf-8")


@router.get("/api/hr/cases")
def list_cases(
    department: str | None = None,
    status: str | None = None,
    employee_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(hr_principal),
):
    query = select(Case).where(Case.tenant_id == principal.tenant_id)
    for field, value in (
        (Case.department, department),
        (Case.status, status),
        (Case.employee_id, employee_id),
    ):
        if value:
            query = query.where(field == value)
    return [
        summary(case)
        for case in db.scalars(query.order_by(Case.created_at.desc(), Case.id).offset(offset).limit(limit))
    ]


@router.get("/api/hr/cases/{case_id}")
def case_detail(case_id: str, db: Session = Depends(get_db), principal: Principal = Depends(hr_principal)):
    service = OnboardingService(db, principal)
    case = service.get_case(case_id)
    documents = db.scalars(select(Document).where(Document.case_id == case.id)).all()
    audit = db.scalars(
        select(AuditEvent).where(AuditEvent.case_id == case.id).order_by(AuditEvent.created_at)
    ).all()
    service.audit(case_id, "hr.case_viewed")
    db.commit()
    return {
        **summary(case),
        "data": case.data,
        "documents": [
            {
                "id": doc.id,
                "doc_type": doc.doc_type,
                "version": doc.version,
                "scan_status": doc.scan_status,
                "extraction": doc.extraction,
            }
            for doc in documents
        ],
        "audit": [
            {
                "action": event.action,
                "actor_id": event.actor_id,
                "created_at": event.created_at.isoformat(),
                "details": event.details,
            }
            for event in audit
        ],
    }


@router.get("/api/hr/export")
def export_cases(
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    limit: int = Query(5000, ge=1, le=5000),
    department: str | None = None,
    status: str | None = None,
    employee_id: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(hr_principal),
):
    query = select(Case).where(Case.tenant_id == principal.tenant_id)
    for field, value in (
        (Case.department, department),
        (Case.status, status),
        (Case.employee_id, employee_id),
    ):
        if value:
            query = query.where(field == value)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    if total > limit:
        raise HTTPException(
            422,
            f"Export matches {total} cases, exceeding limit {limit}. Narrow your filters; no partial export was generated.",
        )
    cases = db.scalars(query.order_by(Case.created_at, Case.id).limit(limit)).all()
    rows = [{field: safe_cell(summary(case).get(field)) for field in EXPORT_FIELDS} for case in cases]
    service = OnboardingService(db, principal)
    for case in cases:
        service.audit(case.id, "hr.exported", {"format": format, "fields": list(EXPORT_FIELDS)})
    db.commit()
    if format == "csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        body, mime = output.getvalue().encode("utf-8-sig"), "text/csv"
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Onboarding"
        sheet.append(EXPORT_FIELDS)
        for row in rows:
            sheet.append([row[field] for field in EXPORT_FIELDS])
        output = io.BytesIO()
        workbook.save(output)
        body, mime = output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        body,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="onboarding.{format}"',
            "Cache-Control": "no-store",
        },
    )


def cascade_delete_case(db: Session, case_id: str):
    # 1. Extraction jobs first (they foreign-key both Document and Case)
    for job in db.scalars(select(ExtractionJob).where(ExtractionJob.case_id == case_id)).all():
        db.delete(job)
    doc_ids = db.scalars(select(Document.id).where(Document.case_id == case_id)).all()
    if doc_ids:
        for job in db.scalars(select(ExtractionJob).where(ExtractionJob.document_id.in_(doc_ids))).all():
            db.delete(job)
    # 2. Documents
    for doc in db.scalars(select(Document).where(Document.case_id == case_id)).all():
        db.delete(doc)
    # 3. Employees
    for emp in db.scalars(select(Employee).where(Employee.case_id == case_id)).all():
        db.delete(emp)
    # 4. Idempotency keys
    for idem in db.scalars(select(Idempotency).where(Idempotency.case_id == case_id)).all():
        db.delete(idem)
    # 5. Audit events
    for event in db.scalars(select(AuditEvent).where(AuditEvent.case_id == case_id)).all():
        db.delete(event)
    # 6. Case
    case = db.scalar(select(Case).where(Case.id == case_id))
    if case:
        db.delete(case)


@router.delete("/api/cases/{case_id}")
@router.delete("/api/hr/cases/{case_id}")
def delete_case(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(hr_principal),
):
    case = db.scalar(select(Case).where(Case.id == case_id))
    if not case:
        raise HTTPException(404, "Case not found")

    try:
        cascade_delete_case(db, case.id)
        db.commit()
        return {"status": "deleted", "case_id": case_id}
    except Exception as err:
        db.rollback()
        logger.exception("Failed to delete case %s: %s", case_id, err)
        raise HTTPException(500, f"Failed to delete case: {err}")


@router.post("/api/hr/cases/clear-failed")
def clear_failed_cases(
    db: Session = Depends(get_db),
    principal: Principal = Depends(hr_principal),
):
    try:
        failed_cases = db.scalars(
            select(Case).where(
                or_(
                    func.lower(Case.status) == "failed",
                    Case.status == "failed",
                )
            )
        ).all()
        count = 0
        for case in failed_cases:
            cascade_delete_case(db, case.id)
            count += 1
        db.commit()
        return {"status": "cleared", "deleted_count": count}
    except Exception as err:
        db.rollback()
        logger.exception("Failed to clear failed cases: %s", err)
        raise HTTPException(500, f"Failed to clear failed cases: {err}")


