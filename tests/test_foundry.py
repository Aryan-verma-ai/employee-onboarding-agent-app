"""Offline contract tests; these do not claim Azure deployment verification."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.foundry import FoundryGateway, redact_identity


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


def test_configuration_required_no_fallback():
    with pytest.raises(ValueError, match="FOUNDRY_AGENT_VERSION"):
        FoundryGateway("endpoint", "name", "")
