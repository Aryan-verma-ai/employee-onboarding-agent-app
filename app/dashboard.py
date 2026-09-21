"""Tenant-scoped HR review and deliberately minimal exports."""

import csv
import io
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Principal, get_principal
from .db import get_db
from .models import AuditEvent, Case, Document
from .service import OnboardingService

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
        "case_id": case.id,
        "employee_id": case.employee_id,
        "department": case.department,
        "status": case.status,
        "full_name": case.data.get("full_name", ""),
        "email": case.data.get("email", ""),
        "missing_fields": case.missing_fields,
    }


@router.get("/hr", response_class=HTMLResponse)
def dashboard_shell():
    # This shell contains no personal data. Every data endpoint validates Entra.
    return Path(__file__).with_name("static").joinpath("dashboard.html").read_text(encoding="utf-8")


@router.get("/api/hr/cases")
def list_cases(
    department: str | None = None,
    status: str | None = None,
    employee_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
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
    return [summary(case) for case in db.scalars(query.order_by(Case.created_at.desc()).limit(limit))]


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
    cases = db.scalars(query.limit(5000)).all()
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
