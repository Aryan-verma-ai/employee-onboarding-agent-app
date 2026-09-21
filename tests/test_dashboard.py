import pytest
from fastapi import HTTPException

from app.auth import Principal
from app.dashboard import EXPORT_FIELDS, hr_principal, safe_cell


@pytest.mark.parametrize("value", ["=SUM(1,2)", "+cmd", "-12", "@SUM(A1)", "  =1", "\t=1"])
def test_formula_injection(value):
    assert safe_cell(value).startswith("'")


def test_export_excludes_identity_numbers_and_hr_gate():
    assert not {"pan", "aadhaar"} & set(EXPORT_FIELDS)
    with pytest.raises(HTTPException):
        hr_principal(Principal("owner", "tenant"))
    assert hr_principal(Principal("hr", "tenant", frozenset({"HR"}))).subject == "hr"


def test_export_filters_and_tenant_isolation():
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.auth import get_principal
    from app.dashboard import router
    from app.db import get_db
    from app.models import Base, Case

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(router)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_principal] = lambda: Principal("hr", "tenant-a", frozenset({"HR"}))
    with Session(engine) as db:
        for tenant, department, name in [
            ("tenant-a", "engineering", "Visible"),
            ("tenant-a", "sales", "Filtered"),
            ("tenant-b", "engineering", "Other tenant"),
        ]:
            db.add(
                Case(
                    tenant_id=tenant,
                    owner_id="owner",
                    department=department,
                    consent_at=datetime.now(timezone.utc),
                    data={"full_name": name},
                )
            )
        db.commit()
    client = TestClient(app)
    response = client.get("/api/hr/export?format=csv&department=engineering")
    assert response.status_code == 200
    assert "Visible" in response.text
    assert "Filtered" not in response.text
    assert "Other tenant" not in response.text
    response = client.get("/api/hr/export?format=xlsx&department=engineering")
    assert response.status_code == 200 and response.content.startswith(b"PK")
