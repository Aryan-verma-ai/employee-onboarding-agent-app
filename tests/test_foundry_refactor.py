"""Comprehensive tests proving the Microsoft Foundry refactor, MCP security boundaries,
deterministic validation, grounded RAG, and authorization controls.
"""

from types import SimpleNamespace as Obj
from unittest.mock import MagicMock, Mock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import Principal, get_principal
from app.foundry import INSTRUCTIONS, TOOL_SCHEMAS, redact_identity, safe_status
from app.knowledge_base import FALLBACK_REFUSAL_MESSAGE, COMPANY_POLICIES, search_company_knowledge
from app.mcp_server import MCP_TOOLS, MCPToolServer, mask_pii
from app.models import Base, Case, Document, Employee
from app.service import OnboardingService


# ── Fixtures ──

@pytest.fixture
def memory_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def hr_principal():
    return Principal("hr-admin-1", "tenant-alpha", frozenset({"HR", "Onboarding.HR"}))


@pytest.fixture
def non_hr_principal():
    return Principal("candidate-1", "tenant-alpha", frozenset({"User"}))


@pytest.fixture
def foreign_principal():
    return Principal("hr-other-tenant", "tenant-beta", frozenset({"HR"}))


# ── Proof 1: Non-HR identity cannot finalize an employee via tool ──

def test_non_hr_cannot_finalize_employee_via_tool(memory_db, non_hr_principal):
    service = OnboardingService(memory_db, non_hr_principal)
    case = service.create_case("Engineering", True)
    
    server = MCPToolServer(service, case.id)
    result = server.call_tool("finalize_employee_onboarding", {"hr_confirmed": True})
    
    assert result["success"] is False
    assert result["is_hr"] is False
    assert "Authorization denied" in result["error"]
    
    # Case must not be finalized
    db_case = service.get_case(case.id)
    assert db_case.status != "created"
    assert db_case.employee_id is None


# ── Proof 2: Unvalidated case cannot be finalized via tool ──

def test_unvalidated_case_cannot_be_finalized_via_tool(memory_db, hr_principal):
    service = OnboardingService(memory_db, hr_principal)
    case = service.create_case("Engineering", True)
    # Case is empty, missing all required fields (name, email, phone, identity proof)
    
    server = MCPToolServer(service, case.id)
    result = server.call_tool("finalize_employee_onboarding", {"hr_confirmed": True})
    
    assert result["success"] is False
    db_case = service.get_case(case.id)
    assert db_case is not None
    assert db_case.status != "created"


# ── Proof 3: Cross-tenant access is blocked deterministically ──

def test_cross_tenant_access_blocked(memory_db, hr_principal, foreign_principal):
    from fastapi import HTTPException
    service_a = OnboardingService(memory_db, hr_principal)
    case_a = service_a.create_case("Engineering", True)
    
    service_foreign = OnboardingService(memory_db, foreign_principal)
    
    # Direct access should fail with 404 (cross-tenant isolation)
    with pytest.raises(HTTPException) as exc:
        service_foreign.get_case(case_a.id)
    assert exc.value.status_code == 404
        
    # Tool server for foreign principal must fail on access
    server = MCPToolServer(service_foreign, case_a.id)
    with pytest.raises(HTTPException) as exc:
        server.call_tool("get_onboarding_status", {})
    assert exc.value.status_code == 404


# ── Proof 4: Raw identity numbers (PAN/Aadhaar) are masked in responses and chat redaction ──

def test_raw_identity_numbers_masked_in_tool_output():
    raw_data = {
        "full_name": "Rajesh Kumar",
        "email": "rajesh@example.com",
        "pan": "ABCDE1234F",
        "aadhaar": "987654321098",
        "nested": {
            "pan": "XYZPK9999Z",
            "aadhaar": "123412341234",
            "notes": "Candidate presented PAN ABCDE1234F and Aadhaar 9876 5432 1098.",
        },
    }
    
    masked = mask_pii(raw_data)
    assert masked["pan"] == "AB*****4F"
    assert masked["aadhaar"] == "********1098"
    assert masked["nested"]["pan"] == "XY*****9Z"
    assert masked["nested"]["aadhaar"] == "********1234"
    assert "ABCDE1234F" not in masked["nested"]["notes"]
    assert "9876 5432 1098" not in masked["nested"]["notes"]
    
    # Test chat redaction helper
    chat_redacted = redact_identity("Hello, my PAN is ABCDE1234F and Aadhaar is 1234 5678 9012.")
    assert "ABCDE1234F" not in chat_redacted
    assert "1234 5678 9012" not in chat_redacted
    assert "[PAN redacted]" in chat_redacted
    assert "[identity number redacted]" in chat_redacted


# ── Proof 5: Arbitrary email recipient rejected ──

def test_arbitrary_email_recipient_rejected(memory_db, non_hr_principal):
    service = OnboardingService(memory_db, non_hr_principal)
    case = service.create_case("Engineering", True)
    case.data = {"email": "candidate@company.com", "full_name": "Candidate One"}
    memory_db.commit()
    
    server = MCPToolServer(service, case.id)
    # Non-HR attempts to send to an external arbitrary email
    result = server.call_tool("send_onboarding_welcome_email", {"email": "attacker@evil.com"})
    assert result.get("status") == "rejected"
    assert "Security violation" in result.get("error", "")


# ── Proof 6: Non-existent policy returns exact fallback message ──

def test_knowledge_base_non_existent_policy_fallback():
    # Query something completely absent from company policies
    result = search_company_knowledge("Can employees travel to the Moon on company expense?")
    assert result["found"] is False
    assert result["answer"] == FALLBACK_REFUSAL_MESSAGE
    assert result["answer"] == "That information is not available in the company knowledge base. Please contact HR."


# ── Proof 7: Grounded policy retrieval returns accurate policy context ──

def test_knowledge_base_grounded_retrieval():
    # Query annual leave entitlement
    result = search_company_knowledge("How many days of annual leave do employees get?")
    assert result["found"] is True
    assert "leave" in result["answer"].lower() or "annual" in result["answer"].lower()
    assert result["citations"]
    
    # Query probation period
    probation_result = search_company_knowledge("What is the probation period?")
    assert probation_result["found"] is True
    assert "probation" in probation_result["answer"].lower()
    
    # Query IT security password policy
    it_result = search_company_knowledge("What is the password requirement for IT security?")
    assert it_result["found"] is True
    assert "character" in it_result["answer"].lower() or "password" in it_result["answer"].lower()


# ── Proof 8: Conflicting documents stop finalization ──

def test_conflicting_documents_stop_finalization(memory_db, hr_principal):
    service = OnboardingService(memory_db, hr_principal)
    case = service.create_case("Engineering", True)
    
    # Provide valid profile data except mark a conflict
    case.data = {
        "full_name": "Priya Sharma",
        "email": "priya@example.com",
        "phone": "9876543210",
        "pan": "ABCDE1234F",
    }
    # Simulate conflict recorded during candidate review
    case.missing_fields = ["conflict:full_name"]
    memory_db.commit()
    
    server = MCPToolServer(service, case.id)
    result = server.call_tool("finalize_employee_onboarding", {"hr_confirmed": True})
    assert result["success"] is False
    assert "conflict:full_name" in result["error"] or "conflict" in result["error"].lower()


# ── Proof 9: Failed validation stops employee creation ──

def test_failed_validation_stops_employee_creation(memory_db, hr_principal):
    service = OnboardingService(memory_db, hr_principal)
    case = service.create_case("Engineering", True)
    # Missing phone and identity proof
    case.data = {"full_name": "Test Candidate", "email": "test@example.com"}
    memory_db.commit()
    
    server = MCPToolServer(service, case.id)
    
    # Tool: validate_onboarding
    val_result = server.call_tool("validate_onboarding", {})
    assert val_result["validation_passed"] is False
    assert "phone" in val_result["missing_fields"]
    assert "pan_or_aadhaar" in val_result["missing_fields"]
    
    # Attempting to finalize must be refused
    fin_result = server.call_tool("finalize_employee_onboarding", {"hr_confirmed": True})
    assert fin_result["success"] is False


# ── Proof 10: HR confirmation allows finalization of validated case ──

def test_hr_confirmation_allows_finalization(memory_db, hr_principal):
    service = OnboardingService(memory_db, hr_principal)
    case = service.create_case("Engineering", True)
    case.data = {
        "full_name": "Amit Patel",
        "email": "amit.patel@company.com",
        "phone": "9876543210",
        "pan": "ABCDE1234F",
        "start_date": "2026-10-01",
    }
    memory_db.commit()
    
    # Validate case
    val_case = service.validate_case(case.id)
    assert val_case.status == "validated"
    
    # Finalize via tool as HR
    server = MCPToolServer(service, case.id)
    result = server.call_tool("finalize_employee_onboarding", {"hr_confirmed": True, "idempotency_key": "test-key-101"})
    
    assert result["success"] is True
    assert result["employee_created"] is True
    assert result["employee_id"].startswith("EMP-")
    assert result["department"] == "Engineering"
    assert result["status"] == "created"
    
    # Check database persistence
    final_case = service.get_case(case.id)
    assert final_case.status == "created"
    assert final_case.employee_id == result["employee_id"]


# ── Proof 11: Email sent only for authorized case ──

def test_email_sent_only_for_authorized_case(memory_db, hr_principal):
    service = OnboardingService(memory_db, hr_principal)
    case = service.create_case("Engineering", True)
    case.data = {
        "full_name": "Sunita Rao",
        "email": "sunita.rao@example.com",
        "phone": "9876543210",
        "pan": "ABCDE1234F",
    }
    memory_db.commit()
    
    server = MCPToolServer(service, case.id)
    # Call send_onboarding_welcome_email without candidate override (uses registered candidate email)
    result = server.call_tool("send_onboarding_welcome_email", {"email": ""})
    assert result["recipient"] == "sunita.rao@example.com"
    assert result["status"] in ("sent", "simulated", "logged")


# ── Proof 12: No SQL tool exists in MCP registry ──

def test_no_sql_tools_exist_in_mcp_registry():
    # Ensure neither SQL, query, execute, nor database tools are exposed to the agent
    forbidden_substrings = ["sql", "query", "eval", "exec", "select", "database_raw", "shell", "bash"]
    for tool_name in MCP_TOOLS:
        for forbidden in forbidden_substrings:
            assert forbidden not in tool_name.lower(), f"Forbidden tool name exposed: {tool_name}"
        
        # Verify schema doesn't permit raw SQL inputs
        tool_spec = MCP_TOOLS[tool_name]
        props = tool_spec.get("inputSchema", {}).get("properties", {})
        for prop in props:
            assert "sql" not in prop.lower()
            assert "query" not in prop.lower() or tool_name == "search_company_knowledge"


# ── Proof 13: Token without HR role has is_hr == False ──

def test_token_without_hr_role_has_is_hr_false(monkeypatch):
    import app.auth as auth
    from types import SimpleNamespace
    
    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(
            environment="production",
            allow_dev_auth=False,
            entra_audience="test-aud",
            entra_tenant_id="tenant-xyz",
        ),
    )
    mock_key = MagicMock()
    mock_key.key = "fake-key"
    mock_signing = MagicMock()
    mock_signing.get_signing_key_from_jwt.return_value = mock_key
    monkeypatch.setattr(auth, "signing_keys", lambda tid: mock_signing)

    def fake_decode(token, *args, **kwargs):
        if kwargs.get("options", {}).get("verify_signature") is False:
            return {"tid": "tenant-xyz"}
        if "user" in token:
            return {
                "sub": "user-123",
                "oid": "user-123",
                "tid": "tenant-xyz",
                "iss": "https://login.microsoftonline.com/tenant-xyz/v2.0",
                "roles": ["User"],
                "email": "user@example.com",
            }
        return {
            "sub": "hr-123",
            "oid": "hr-123",
            "tid": "tenant-xyz",
            "iss": "https://login.microsoftonline.com/tenant-xyz/v2.0",
            "roles": ["HR"],
            "email": "hr@example.com",
        }

    monkeypatch.setattr(auth.jwt, "decode", fake_decode)

    user_principal = auth.get_principal(authorization="Bearer user-token")
    assert user_principal.is_hr is False
    assert "HR" not in user_principal.roles

    hr_principal = auth.get_principal(authorization="Bearer hr-token")
    assert hr_principal.is_hr is True
    assert "HR" in hr_principal.roles

