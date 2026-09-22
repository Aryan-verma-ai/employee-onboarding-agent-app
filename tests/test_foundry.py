"""Offline contract tests; these do not claim Azure deployment verification."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.foundry import TOOL_DESCRIPTIONS, FoundryGateway, redact_identity


def setup_gateway(outputs):
    client = Mock()
    client.conversations.create.return_value = NS(id="conv-test")
    client.responses.create.side_effect = outputs
    service = Mock()
    service.get_case.return_value = {
        "status": "needs-information",
        "missing_fields": ["email"],
        "data": {"pan": "ABCDE1234F"},
    }
    return FoundryGateway("https://example", "onboarding", "7", client), client, service


def reply(calls=(), text="Please supply the missing email."):
    return NS(id="resp-test", output=list(calls), output_text=text)


def test_pinned_agent_tool_loop_and_minimal_output():
    call = NS(type="function_call", name="get_onboarding_status", arguments="{}", call_id="call-1")
    gateway, client, service = setup_gateway([reply([call]), reply()])
    saved = Mock()
    result = gateway.chat(None, "Check ABCDE1234F", service, "case-1", saved)
    saved.assert_called_once_with("conv-test")
    assert result["conversation_id"] == "conv-test"
    first, second = client.responses.create.call_args_list
    assert first.kwargs["extra_body"]["agent_reference"]["version"] == "7"
    assert "ABCDE1234F" not in first.kwargs["input"]
    assert "ABCDE1234F" not in str(second.kwargs)
    assert second.kwargs["input"][0]["call_id"] == "call-1"
    service.finalize_case.assert_not_called()


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("finalize_case", "{}"),
        ("get_onboarding_status", '{"case_id":"other"}'),
        ("validate_onboarding", "[]"),
        ("validate_onboarding", "broken"),
    ],
)
def test_untrusted_tools_and_arguments_rejected(name, arguments):
    gateway, client, service = setup_gateway(
        [reply([NS(type="function_call", name=name, arguments=arguments, call_id="x")]), reply()]
    )
    gateway.chat("existing", "approve", service, "bound-case")
    service.validate_case.assert_not_called()
    service.finalize_case.assert_not_called()
    assert "not permitted" in client.responses.create.call_args.kwargs["input"][0]["output"]
    client.conversations.create.assert_not_called()


def test_authorization_before_cloud_call():
    gateway, client, service = setup_gateway([])
    service.get_case.side_effect = PermissionError()
    with pytest.raises(PermissionError):
        gateway.chat(None, "hello", service, "other-case")
    client.conversations.create.assert_not_called()


def test_bounded_tool_loop():
    call = NS(type="function_call", name="get_onboarding_status", arguments="{}", call_id="x")
    gateway, client, service = setup_gateway([reply([call])] * 8)
    with pytest.raises(RuntimeError, match="iteration limit"):
        gateway.chat("conv", "loop", service, "case")
    assert client.responses.create.call_count == 8


def test_identity_redaction():
    assert redact_identity("ABCDE1234F 1234 5678 9012") == "[PAN redacted] [identity number redacted]"


def test_validation_uses_server_case():
    call = NS(type="function_call", name="validate_onboarding", arguments="{}", call_id="v")
    gateway, client, service = setup_gateway([reply([call]), reply()])
    gateway.chat("conv", "validate", service, "server-bound-case")
    service.validate_case.assert_called_once_with("server-bound-case")


@pytest.mark.parametrize(
    ("name", "service_method"),
    [
        ("inspect_uploaded_documents", "document_workflow"),
        ("request_document_extraction", "request_document_extraction"),
        ("build_employee_profile", "profile_readiness"),
        ("prepare_hr_confirmation", "confirmation_readiness"),
    ],
)
def test_workflow_tools_invoke_only_the_server_bound_case(name, service_method):
    call = NS(type="function_call", name=name, arguments="{}", call_id="workflow")
    gateway, _, service = setup_gateway([reply([call]), reply()])
    getattr(service, service_method).return_value = {"tool": name}

    gateway.chat("conv", "continue", service, "server-bound-case")

    getattr(service, service_method).assert_called_once_with("server-bound-case")
    service.finalize_case.assert_not_called()


def test_foundry_agent_exposes_full_orchestration_toolset():
    assert set(TOOL_DESCRIPTIONS) == {
        "get_onboarding_status",
        "inspect_uploaded_documents",
        "request_document_extraction",
        "build_employee_profile",
        "validate_onboarding",
        "prepare_hr_confirmation",
    }


def test_configuration_required_no_fallback():
    with pytest.raises(ValueError, match="FOUNDRY_AGENT_VERSION"):
        FoundryGateway("endpoint", "name", "")


def test_excessive_tool_fanout_is_rejected_before_execution():
    call = NS(type="function_call", name="validate_onboarding", arguments="{}", call_id="x")
    gateway, client, service = setup_gateway([reply([call] * 33)])
    with pytest.raises(RuntimeError, match="tool call limit"):
        gateway.chat("conv", "validate", service, "case")
    service.validate_case.assert_not_called()


def test_failed_response_not_presented_as_success():
    response = reply(text="Created")
    response.status = "failed"
    gateway, client, service = setup_gateway([response])
    with pytest.raises(RuntimeError, match="did not complete"):
        gateway.chat("conv", "hello", service, "case")


def test_key_adapter_rejects_untrusted_endpoint():
    from app.foundry import project_key_client

    for endpoint in [
        "http://x.services.ai.azure.com/api/projects/p",
        "https://attacker.example/api/projects/p",
        "https://x.services.ai.azure.com/api/projects/p?redirect=x",
        "https://x.services.ai.azure.com/api/projects/p/other",
    ]:
        with pytest.raises(ValueError):
            project_key_client(endpoint, "synthetic-key")


def test_key_adapter_scoped_header_and_agent_reference(monkeypatch):
    import httpx

    from app.foundry import project_key_client

    seen = []
    real_client = httpx.Client

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "conv-test", "object": "conversation", "created_at": 1})

    class TestClient(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", TestClient)
    client = project_key_client("https://unit.services.ai.azure.com/api/projects/p", "synthetic-key")
    try:
        assert client.conversations.create().id == "conv-test"
        assert seen[0].headers["api-key"] == "synthetic-key"
        assert "authorization" not in seen[0].headers
        assert str(seen[0].url) == "https://unit.services.ai.azure.com/api/projects/p/openai/v1/conversations"
        client.base_url = "https://attacker.example/"
        with pytest.raises(Exception):
            client.conversations.create()
        assert len(seen) == 1
    finally:
        client.close()


def test_withdrawn_consent_stops_before_cloud_call():
    gateway, client, service = setup_gateway([])
    service.require_consent.side_effect = PermissionError("consent withdrawn")
    with pytest.raises(PermissionError):
        gateway.chat(None, "hello", service, "case")
    client.conversations.create.assert_not_called()


@pytest.mark.parametrize(
    "status,problems,model_text,expected",
    [
        (
            "needs-information",
            ["conflict:full_name"],
            "Please fill in your full name.",
            "Conflicting evidence for full name",
        ),
        ("failed", ["extraction:pan"], "Please provide your PAN.", "Document processing failed"),
    ],
)
def test_escalation_uses_authoritative_evidence(status, problems, model_text, expected):
    from app.foundry import safe_status

    case = {"status": status, "missing_fields": problems, "data": {"pan": "ABCDE1234F"}}
    projection = safe_status(case)
    assert projection["missing_fields"] == []
    assert projection["escalation_required"] is True
    assert projection["next_action"]
    gateway, client, service = setup_gateway([reply(text=model_text)])
    service.get_case.return_value = case
    raw = []
    result = gateway.chat("conv", "What next?", service, "case", on_model_response=raw.append)
    assert expected in result["message"]
    assert "HR review" in result["message"]
    assert result["escalation_enforced"] is True
    assert raw == [model_text]
    assert "ABCDE1234F" not in str(result)


def test_consent_rechecked_after_conversation_persistence():
    gateway, client, service = setup_gateway([reply()])

    def revoke(_):
        service.require_consent.side_effect = PermissionError("withdrawn")

    with pytest.raises(PermissionError):
        gateway.chat(None, "hello", service, "case", revoke)
    client.responses.create.assert_not_called()


def test_consent_rechecked_after_tool_commit():
    call = NS(type="function_call", name="validate_onboarding", arguments="{}", call_id="c")
    gateway, client, service = setup_gateway([reply([call]), reply()])

    def revoke(_):
        service.require_consent.side_effect = PermissionError("withdrawn")

    service.validate_case.side_effect = revoke
    with pytest.raises(PermissionError):
        gateway.chat("existing", "validate", service, "case")
    assert client.responses.create.call_count == 1
