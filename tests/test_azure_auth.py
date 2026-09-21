from unittest.mock import Mock

import pytest

from app import azure_auth


def test_empty_env_placeholders_allow_cli_fallback(monkeypatch):
    for name in (
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_CLIENT_CERTIFICATE_PATH",
    ):
        monkeypatch.setenv(name, "")
    factory = Mock()
    monkeypatch.setattr(azure_auth, "DefaultAzureCredential", factory)
    azure_auth.azure_credential()
    factory.assert_called_once_with(exclude_environment_credential=True)


def test_complete_service_principal_uses_environment(monkeypatch):
    for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        monkeypatch.setenv(name, "synthetic-test-only")
    factory = Mock()
    monkeypatch.setattr(azure_auth, "DefaultAzureCredential", factory)
    azure_auth.azure_credential()
    factory.assert_called_once_with(exclude_environment_credential=False)


def test_partial_secret_configuration_fails_without_exposing_value(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "synthetic-sensitive-placeholder")
    monkeypatch.setenv("AZURE_CLIENT_ID", "")
    monkeypatch.setenv("AZURE_TENANT_ID", "")
    with pytest.raises(ValueError) as failure:
        azure_auth.azure_credential()
    assert "synthetic-sensitive-placeholder" not in str(failure.value)
