"""Opt-in real PostgreSQL RLS checks. Uses a dedicated CI test database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import db as security_context  # Registers transaction-local PostgreSQL identity hooks.
from app.auth import Principal
from app.models import Case, Document, Employee, Idempotency
from app.service import OnboardingService


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Dedicated PostgreSQL test service not configured"
)
def test_real_postgres_rls_and_migration():
    assert security_context.set_security_context
    url = os.environ["TEST_POSTGRES_URL"]
    assert make_url(url).database == "onboarding_test", "Only a dedicated test database may be used"
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        check=True,
        cwd=Path(__file__).parents[1],
    )
    owner = create_engine(url)
    with owner.begin() as connection:
        connection.execute(
            text(
                "CREATE ROLE onboarding_rls_test LOGIN PASSWORD 'test-only-password' NOSUPERUSER NOBYPASSRLS"
            )
        )
        connection.execute(text("GRANT USAGE ON SCHEMA public TO onboarding_rls_test"))
        connection.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO onboarding_rls_test")
        )
    app_url = make_url(url).set(username="onboarding_rls_test", password="test-only-password")
    runtime = create_engine(app_url)
    try:
        with Session(runtime) as db:
            db.info["principal"] = Principal("alice", "tenant-a")
            case = OnboardingService(db, db.info["principal"]).create_case("Engineering", True)
            case_id = case.id
        # Query the table directly: isolation must hold without service filters.
        for principal, visible in [
            (Principal("alice", "tenant-a"), True),
            (Principal("bob", "tenant-a"), False),
            (Principal("hr", "tenant-a", frozenset({"HR"})), True),
            (Principal("hr", "tenant-b", frozenset({"HR"})), False),
            (None, False),
        ]:
            with Session(runtime) as db:
                db.info["principal"] = principal
                found = db.scalar(select(Case.id).where(Case.id == case_id))
                assert (found == case_id) is visible
        # Real concurrent requests serialize on the case row and reuse one ID.
        from concurrent.futures import ThreadPoolExecutor
        from uuid import uuid4

        from sqlalchemy import func

        hr = Principal("hr", "tenant-" + uuid4().hex, frozenset({"HR"}))
        with Session(runtime) as db:
            db.info["principal"] = hr
            svc = OnboardingService(db, hr)
            case = svc.create_case("Engineering", True)
            case.data = {
                "full_name": "Synthetic Person",
                "email": "synthetic@example.com",
                "phone": "+919876543210",
                "pan": "ABCDE1234F",
                "aadhaar": "234567890123",
            }
            for kind in ("pan", "aadhaar"):
                db.add(
                    Document(
                        case_id=case.id,
                        tenant_id=hr.tenant_id,
                        doc_type=kind,
                        scan_status="clean",
                        extraction={"status": "complete", "reviewed": True},
                        filename="synthetic.pdf",
                        content_type="application/pdf",
                        storage_key="synthetic",
                        sha256="0" * 64,
                    )
                )
            db.commit()
            case_id = case.id
            assert svc.validate_case(case_id).status == "validated"

        def finalize():
            with Session(runtime) as db:
                db.info["principal"] = hr
                return OnboardingService(db, hr).finalize_case(case_id, True, "concurrent").employee_id

        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(lambda _: finalize(), range(4)))
        assert len(set(ids)) == 1
        with Session(runtime) as db:
            db.info["principal"] = hr
            assert db.scalar(select(func.count()).select_from(Employee)) == 1
            assert db.scalar(select(func.count()).select_from(Idempotency)) == 1

        # Abort after INSERTs flush: no employee, issuance key or state can survive.
        rollback_actor = Principal("hr", "tenant-" + uuid4().hex, frozenset({"HR"}))
        with Session(runtime) as db:
            db.info["principal"] = rollback_actor
            svc = OnboardingService(db, rollback_actor)
            case = svc.create_case("Engineering", True)
            case_id = case.id
            try:
                db.add(
                    Employee(
                        id="EMP-" + uuid4().hex, case_id=case_id, tenant_id=rollback_actor.tenant_id, data={}
                    )
                )
                db.add(
                    Idempotency(
                        tenant_id=rollback_actor.tenant_id,
                        key="rollback",
                        case_id=case_id,
                        employee_id="synthetic",
                    )
                )
                case.status = "created"
                db.flush()
                raise RuntimeError("synthetic failure before commit")
            except RuntimeError:
                db.rollback()
            assert db.scalar(select(func.count()).select_from(Employee)) == 0
            assert db.scalar(select(func.count()).select_from(Idempotency)) == 0
            assert svc.get_case(case_id).status == "received"
    finally:
        runtime.dispose()
        with owner.begin() as connection:
            connection.execute(text("DROP OWNED BY onboarding_rls_test"))
            connection.execute(text("DROP ROLE onboarding_rls_test"))
        owner.dispose()
