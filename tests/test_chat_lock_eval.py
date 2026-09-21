from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.chat_lock import ChatBusyError, conversation_lock, lock_key
from evaluations.run import FixtureService, score


def pg_engine(results):
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    connection.execute.return_value.scalar.side_effect = results
    return engine, connection


def test_postgres_unlock_on_work_failure():
    engine, connection = pg_engine([True, True])
    with pytest.raises(ValueError):
        with conversation_lock(engine, "tenant", "case"):
            raise ValueError("work failed")
    assert "pg_advisory_unlock" in str(connection.execute.call_args.args[0])
    connection.invalidate.assert_not_called()


def test_postgres_lock_timeout_never_unlocks_unowned_lock():
    engine, connection = pg_engine([False])
    with pytest.raises(ChatBusyError):
        with conversation_lock(engine, "tenant", "case", timeout=0):
            pytest.fail("must not enter")
    assert connection.execute.call_count == 1


def test_unlock_failure_invalidates_connection():
    engine, connection = pg_engine([True, False])
    with conversation_lock(engine, "tenant", "case"):
        pass
    connection.invalidate.assert_called_once()


def test_unlock_exception_invalidates_connection():
    engine, connection = pg_engine([True, OSError("disconnected")])
    with pytest.raises(OSError):
        with conversation_lock(engine, "tenant", "case"):
            pass
    connection.invalidate.assert_called_once()


def test_sqlite_is_development_only_and_per_case():
    engine = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    with pytest.raises(RuntimeError, match="development-only"):
        with conversation_lock(engine, "tenant", "case"):
            pass
    with conversation_lock(engine, "tenant", "case", development=True):
        with pytest.raises(ChatBusyError):
            with conversation_lock(engine, "tenant", "case", development=True, timeout=0):
                pass
        with conversation_lock(engine, "tenant", "other", development=True):
            pass
    with conversation_lock(engine, "tenant", "case", development=True, timeout=0):
        pass


def test_key_is_tenant_scoped_and_unambiguous():
    assert lock_key("ab", "c") != lock_key("a", "bc")
    assert lock_key("tenant-a", "case") != lock_key("tenant-b", "case")
    assert lock_key("tenant-a", "case") == lock_key("tenant-a", "case")


def test_eval_detects_missing_tool_and_false_success():
    fixture = {
        "state": {"status": "received"},
        "after_validation": {"status": "validated"},
        "expected_status": "validated",
        "required_tools": ["validate_onboarding"],
        "forbidden": ["successfully created"],
        "escalate": True,
    }
    service = FixtureService(fixture)
    metrics = score(fixture, {"message": "Successfully created"}, service)
    assert not any(metrics[key] for key in ["tools", "workflow", "safety", "escalation"])
    service.validate_case("case")
    service.audit("case", "foundry.tool", {"tool": "validate_onboarding"})
    assert all(score(fixture, {"message": "Validated; HR review is required."}, service).values())
    assert fixture["state"]["status"] == "received"


def test_telemetry_excludes_exception_text(monkeypatch):
    pytest.importorskip("opentelemetry", reason="Optional observability dependencies are not installed")
    from opentelemetry import trace

    from app.foundry import telemetry_span

    tracer = MagicMock()
    monkeypatch.setenv("ONBOARDING_OTEL_ENABLED", "true")
    monkeypatch.setattr(trace, "get_tracer", lambda name: tracer)
    with pytest.raises(ValueError):
        with telemetry_span("onboarding.chat"):
            raise ValueError("PII must never be recorded")
    kwargs = tracer.start_as_current_span.call_args.kwargs
    assert kwargs["record_exception"] is False
    assert kwargs["set_status_on_exception"] is False
    span = tracer.start_as_current_span.return_value.__enter__.return_value
    span.set_attribute.assert_called_once_with("onboarding.failed", True)
    span.record_exception.assert_not_called()


@pytest.fixture
def live_pg_engine():
    import os

    from sqlalchemy import create_engine

    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL enables real PostgreSQL coordination checks")
    engine = create_engine(url, pool_size=4, max_overflow=0)
    try:
        yield engine
    finally:
        engine.dispose()


def test_real_postgres_lock_survives_other_transaction_commits(live_pg_engine):
    from uuid import uuid4

    from sqlalchemy import text

    tenant, case = "test-tenant", str(uuid4())
    with conversation_lock(live_pg_engine, tenant, case):
        with live_pg_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.commit()
            connection.execute(text("SELECT 1"))
            connection.rollback()
        with pytest.raises(ChatBusyError):
            with conversation_lock(live_pg_engine, tenant, case, timeout=0):
                pytest.fail("another database session acquired the lock")
        with conversation_lock(live_pg_engine, "other-tenant", case, timeout=0):
            pass
    with conversation_lock(live_pg_engine, tenant, case, timeout=0):
        pass


def test_real_postgres_unlocks_after_exception(live_pg_engine):
    from uuid import uuid4

    case = str(uuid4())
    with pytest.raises(ValueError):
        with conversation_lock(live_pg_engine, "test-tenant", case):
            raise ValueError("simulated model failure")
    with conversation_lock(live_pg_engine, "test-tenant", case, timeout=0):
        pass
