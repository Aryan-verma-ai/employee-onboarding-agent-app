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


def test_delete_case_and_clear_failed():
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
        c1 = Case(id="case-to-delete", tenant_id="tenant-a", owner_id="owner", department="hr", consent_at=datetime.now(timezone.utc), status="received")
        c2 = Case(id="case-failed-1", tenant_id="tenant-a", owner_id="owner", department="hr", consent_at=datetime.now(timezone.utc), status="failed")
        c3 = Case(id="case-failed-2", tenant_id="tenant-a", owner_id="owner", department="hr", consent_at=datetime.now(timezone.utc), status="failed")
        db.add_all([c1, c2, c3])
        db.commit()

    client = TestClient(app)
    # Test delete single case
    res = client.delete("/api/cases/case-to-delete")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"

    # Verify case-to-delete is gone
    res_queue = client.get("/api/hr/cases")
    case_ids = [c["id"] for c in res_queue.json()]
    assert "case-to-delete" not in case_ids

    # Test clear failed cases
    res_clear = client.post("/api/hr/cases/clear-failed")
    assert res_clear.status_code == 200
    assert res_clear.json()["deleted_count"] == 2

    # Verify no failed cases remain
    res_queue_after = client.get("/api/hr/cases")
    assert len(res_queue_after.json()) == 0


