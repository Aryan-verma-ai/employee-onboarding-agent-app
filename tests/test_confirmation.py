"""Confirmation access, status gating, and privacy contracts."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import Principal, get_principal
from app.confirmation import router
from app.db import get_db
from app.models import AuditEvent, Base, Case, Employee


@pytest.fixture
def confirmation_api(monkeypatch):
    import app.auth as auth

    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(
            environment="production",
            allow_dev_auth=False,
            entra_tenant_id="tenant-a",
            entra_audience="test-api",
        ),
    )
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            Case(
                id="case-one",
                tenant_id="tenant-a",
                owner_id="owner",
                department="Engineering",
                consent_at=datetime.now(timezone.utc),
                status="created",
                employee_id="EMP-TEST",
                data={"pan": "ABCDE1234F", "aadhaar": "234567890123", "email": "private@example.com"},
            )
        )
        db.flush()
        db.add(
            Employee(
                id="EMP-TEST",
                case_id="case-one",
                tenant_id="tenant-a",
                data={},
                created_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            )
        )
        db.commit()
    app = FastAPI()
    app.include_router(router)
    actor = {"principal": Principal("owner", "tenant-a")}

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_principal] = lambda: actor["principal"]
    with TestClient(app) as client:
        yield client, actor, engine, app
    engine.dispose()


def test_confirmation_download_is_minimal_and_audited(confirmation_api):
    client, actor, engine, _ = confirmation_api
    response = client.get("/api/cases/case-one/confirmation")
    assert response.status_code == 200
    assert set(response.json()) == {"employee_id", "case_id", "department", "created_at"}
    assert response.json()["employee_id"] == "EMP-TEST"
    assert response.json()["created_at"].startswith("2026-09-21T00:00:00")
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert all(value not in response.text for value in ("ABCDE1234F", "234567890123", "private@example.com"))
    with Session(engine) as db:
        event = db.scalar(select(AuditEvent))
        assert event.action == "confirmation.downloaded"
        assert event.actor_id == "owner"
        assert event.tenant_id == "tenant-a"
    actor["principal"] = Principal("hr", "tenant-a", frozenset({"HR"}))
    assert client.get("/api/cases/case-one/confirmation").status_code == 200


def test_confirmation_rejects_cross_owner_tenant_and_unauthenticated(confirmation_api):
    client, actor, engine, app = confirmation_api
    for principal in (Principal("other", "tenant-a"), Principal("hr", "tenant-b", frozenset({"HR"}))):
        actor["principal"] = principal
        assert client.get("/api/cases/case-one/confirmation").status_code == 404
    app.dependency_overrides.pop(get_principal)
    assert client.get("/api/cases/case-one/confirmation").status_code == 401
    with Session(engine) as db:
        assert db.scalar(select(AuditEvent)) is None


@pytest.mark.parametrize("status", ["received", "extracting", "needs-information", "validated", "failed"])
def test_confirmation_requires_completed_creation(confirmation_api, status):
    client, _, engine, _ = confirmation_api
    with Session(engine) as db:
        db.get(Case, "case-one").status = status
        db.commit()
    assert client.get("/api/cases/case-one/confirmation").status_code == 409
