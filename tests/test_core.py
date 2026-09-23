import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import Base, Employee
from app.service import OnboardingService


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def svc(session, user="alice", tenant="a", hr=False):
    return OnboardingService(session, Principal(user, tenant, frozenset({"HR"}) if hr else frozenset()))


def test_consent_and_isolation(session):
    s = svc(session)
    with pytest.raises(HTTPException) as error:
        s.create_case("Engineering", False)
    assert error.value.status_code == 422
    case = s.create_case("Engineering", True)
    assert s.get_case(case.id).id == case.id
    for other in (svc(session, "bob"), svc(session, "alice", "b", True)):
        with pytest.raises(HTTPException) as error:
            other.get_case(case.id)
        assert error.value.status_code == 404
    assert svc(session, "hr", hr=True).get_case(case.id).id == case.id


def test_finalize_authorization_confirmation_and_idempotence(session, monkeypatch):
    monkeypatch.setattr(OnboardingService, "validation_errors", lambda self, case: [])
    s = svc(session, hr=True)
    case = s.create_case("Engineering", True)
    s.transition(case, "validated")
    session.commit()
    with pytest.raises(HTTPException) as error:
        svc(session).finalize_case(case.id, True, "key")
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        s.finalize_case(case.id, False, "key")
    first = s.finalize_case(case.id, True, "key")
    second = s.finalize_case(case.id, True, "key")
    assert first.employee_id == second.employee_id
    assert session.scalar(select(func.count()).select_from(Employee)) == 1
    other = s.create_case("Engineering", True)
    with pytest.raises(HTTPException) as error:
        s.finalize_case(other.id, True, "key")
    assert error.value.status_code == 409


def test_terminal_state(session):
    s = svc(session)
    case = s.create_case("Engineering", True)
    case.status = "created"
    with pytest.raises(HTTPException):
        s.transition(case, "extracting")


def test_rollback_has_no_partial_employee(session, monkeypatch):
    monkeypatch.setattr(OnboardingService, "validation_errors", lambda self, case: [])
    s = svc(session, hr=True)
    case = s.create_case("Engineering", True)
    s.transition(case, "validated")
    session.commit()
    original_commit = session.commit

    def fail_commit():
        session.flush()
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        s.finalize_case(case.id, True, "rollback")
    session.rollback()
    monkeypatch.setattr(session, "commit", original_commit)
    assert session.scalar(select(func.count()).select_from(Employee)) == 0
    assert s.get_case(case.id).status == "validated"


def test_auth_rejects_unsigned_wrong_tenant_and_missing_token(monkeypatch):
    from types import SimpleNamespace

    import jwt

    import app.auth as auth

    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(
            environment="production", allow_dev_auth=True, entra_tenant_id="tenant", entra_audience="audience"
        ),
    )
    with pytest.raises(HTTPException) as error:
        auth.get_principal(None)
    assert error.value.status_code == 401
    # A production flag never enables the development principal.
    monkeypatch.setattr(
        auth,
        "signing_keys",
        lambda tenant: SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key="a" * 32)),
    )
    token = jwt.encode(
        {"sub": "attacker", "tid": "wrong", "exp": 9999999999, "iat": 1, "aud": "audience"},
        "a" * 32,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as error:
        auth.get_principal("Bearer " + token)
    assert error.value.status_code == 401


def test_concurrent_finalization_creates_one_employee(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    monkeypatch.setattr(OnboardingService, "validation_errors", lambda self, case: [])
    engine = create_engine(f"sqlite:///{tmp_path / 'concurrency.db'}", connect_args={"timeout": 15})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        s = svc(db, hr=True)
        case = s.create_case("Engineering", True)
        case.status = "validated"
        db.commit()
        case_id = case.id
    barrier = Barrier(2)

    def finalize():
        with Session(engine) as db:
            s = svc(db, hr=True)
            barrier.wait()
            return s.finalize_case(case_id, True, "same-key").employee_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finalize(), range(2)))
    assert len(set(results)) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Employee)) == 1


def test_validation_requires_document_evidence(session):
    s = svc(session)

    # 1. Manual entry of both PAN and Aadhaar validates without mandating photo uploads
    case = s.create_case("Engineering", True)
    case.data = {
        "full_name": "Alice Test",
        "email": "alice@example.com",
        "phone": "+919876543210",
        "pan": "ABCDE1234F",
        "aadhaar": "234567890123",
    }
    result = s.validate_case(case.id)
    assert result.status == "validated"
    assert result.missing_fields == []

    # 2. Manual entry of ONLY PAN validates successfully
    case_pan = s.create_case("Engineering", True)
    case_pan.data = {
        "full_name": "Bob Test",
        "email": "bob@example.com",
        "phone": "+919876543210",
        "pan": "ABCDE1234F",
    }
    result_pan = s.validate_case(case_pan.id)
    assert result_pan.status == "validated"

    # 3. Manual entry of ONLY Aadhaar validates successfully
    case_aadhaar = s.create_case("Engineering", True)
    case_aadhaar.data = {
        "full_name": "Charlie Test",
        "email": "charlie@example.com",
        "phone": "+919876543210",
        "aadhaar": "234567890123",
    }
    result_aadhaar = s.validate_case(case_aadhaar.id)
    assert result_aadhaar.status == "validated"

    # 4. Neither PAN nor Aadhaar provided fails with pan_or_aadhaar
    case_missing = s.create_case("Engineering", True)
    case_missing.data = {
        "full_name": "Dave Test",
        "email": "dave@example.com",
        "phone": "+919876543210",
    }
    result_missing = s.validate_case(case_missing.id)
    assert result_missing.status == "needs-information"
    assert "pan_or_aadhaar" in result_missing.missing_fields


def test_auth_valid_rsa_and_wrong_tenant(monkeypatch):
    import time
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    import app.auth as auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(
            environment="production",
            allow_dev_auth=False,
            entra_tenant_id="tenant",
            entra_audience="audience",
        ),
    )
    monkeypatch.setattr(
        auth,
        "signing_keys",
        lambda tenant: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )
    claims = {
        "sub": "subject",
        "oid": "object-id",
        "tid": "tenant",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "aud": "audience",
        "roles": ["HR"],
        "iss": "https://login.microsoftonline.com/tenant/v2.0",
    }
    assert auth.get_principal("Bearer " + jwt.encode(claims, key, algorithm="RS256")).is_hr
    for change in ({"tid": "other"}, {"aud": "other"}, {"exp": 1}, {"iss": "https://evil.example"}):
        with pytest.raises(HTTPException) as error:
            auth.get_principal("Bearer " + jwt.encode({**claims, **change}, key, algorithm="RS256"))
        assert error.value.status_code == 401
