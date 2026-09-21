"""Opt-in real PostgreSQL RLS checks. Uses a dedicated CI test database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import Case
from app.service import OnboardingService


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Dedicated PostgreSQL test service not configured"
)
def test_real_postgres_rls_and_migration():
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
    finally:
        runtime.dispose()
        with owner.begin() as connection:
            connection.execute(text("DROP OWNED BY onboarding_rls_test"))
            connection.execute(text("DROP ROLE onboarding_rls_test"))
        owner.dispose()
