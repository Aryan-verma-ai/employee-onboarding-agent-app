"""Regression checks for review gates, conflicts, configuration and migrations."""

import os
import subprocess
import sys
from dataclasses import replace
from hashlib import sha256

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app import config
from app.auth import Principal
from app.models import Base, Document, Employee
from app.service import OnboardingService


def test_production_configuration_fails_without_cloud_settings(monkeypatch):
    monkeypatch.setattr(
        config,
        "settings",
        replace(config.settings, environment="production", entra_tenant_id="", entra_audience=""),
    )
    with pytest.raises(RuntimeError, match="Missing production configuration"):
        config.validate_configuration()


def test_invalid_department_is_rejected_before_case_creation():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        service = OnboardingService(db, Principal("alice", "tenant"))
        with pytest.raises(HTTPException) as error:
            service.create_case("Engineering typo", True)
        assert error.value.status_code == 422


def test_review_does_not_override_conflicting_identity_and_duplicates():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        service = OnboardingService(db, Principal("hr", "tenant", frozenset({"HR"})))
        case = service.create_case("Engineering", True)
        case.data = {"pan": "ABCDE1234F"}
        doc = Document(
            case_id=case.id,
            tenant_id="tenant",
            doc_type="pan",
            filename="test.pdf",
            content_type="application/pdf",
            storage_key="synthetic",
            sha256="0" * 64,
            scan_status="clean",
            extraction={
                "status": "complete",
                "reviewed": True,
                "candidates": [{"field": "pan", "value": "ZZZZZ1234Z", "confidence": 0.99}],
            },
        )
        db.add(doc)
        db.commit()
        assert "conflict:pan" in service.validation_errors(case)
        prior = service.create_case("Engineering", True)
        db.add(
            Employee(
                id="EMP-EXISTING",
                case_id=prior.id,
                tenant_id="tenant",
                data={},
                pan_fingerprint=sha256(b"ABCDE1234F").hexdigest(),
            )
        )
        db.commit()
        assert "duplicate:pan" in service.validation_errors(case)


def test_initial_migration_upgrade_downgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    env = {**os.environ, "DATABASE_URL": url}
    for revision in ("head",):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision], env=env, check=True, capture_output=True
        )
    engine = create_engine(url)
    assert {"onboarding_cases", "documents", "employees", "audit_events", "idempotency_keys"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"], env=env, check=True, capture_output=True
    )
    engine = create_engine(url)
    assert "employees" not in inspect(engine).get_table_names()
    engine.dispose()
