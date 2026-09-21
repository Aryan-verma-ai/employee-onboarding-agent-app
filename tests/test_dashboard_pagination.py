from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import Principal, get_principal
from app.dashboard import router
from app.db import get_db
from app.models import Base, Case


@pytest.fixture
def paginated_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for i in range(3):
            db.add(
                Case(
                    id=f"case-{i}",
                    tenant_id="tenant",
                    owner_id="owner",
                    department="engineering",
                    consent_at=datetime.now(timezone.utc),
                )
            )
        db.commit()

    def database():
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_principal] = lambda: Principal("hr", "tenant", frozenset({"HR"}))
    with TestClient(app) as client:
        yield client
    engine.dispose()


def test_pages_are_disjoint_and_complete(paginated_client):
    first = paginated_client.get("/api/hr/cases?limit=2&offset=0").json()
    second = paginated_client.get("/api/hr/cases?limit=2&offset=2").json()
    assert len(first) == 2 and len(second) == 1
    assert len({row["case_id"] for row in first + second}) == 3
    assert paginated_client.get("/api/hr/cases?offset=-1").status_code == 422


def test_export_rejects_truncation(paginated_client):
    response = paginated_client.get("/api/hr/export?limit=2")
    assert response.status_code == 422
    assert "3 cases" in response.json()["detail"]
    assert paginated_client.get("/api/hr/export?limit=3").status_code == 200
