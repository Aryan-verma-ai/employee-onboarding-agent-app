"""Minimal, authorization-scoped employee creation receipts."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Principal, get_principal
from .db import get_db
from .models import Employee
from .service import OnboardingService

router = APIRouter(prefix="/api/cases", tags=["confirmation"])


@router.get("/{case_id}/confirmation")
def download_confirmation(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    service = OnboardingService(db, principal)
    case = service.get_case(case_id)
    if case.status != "created" or not case.employee_id:
        raise HTTPException(409, "Confirmation is available only after employee creation")
    employee = db.scalar(
        select(Employee).where(
            Employee.id == case.employee_id,
            Employee.case_id == case.id,
            Employee.tenant_id == principal.tenant_id,
        )
    )
    if employee is None:
        raise HTTPException(409, "Employee record is not available")
    # Explicit allowlist keeps identity evidence and contact information private.
    receipt = {
        "employee_id": case.employee_id,
        "case_id": case.id,
        "department": case.department,
        "created_at": employee.created_at.isoformat(),
    }
    service.audit(case.id, "confirmation.downloaded", {"fields": list(receipt)})
    db.commit()
    return JSONResponse(
        receipt,
        headers={
            "Content-Disposition": 'attachment; filename="onboarding-confirmation.json"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
