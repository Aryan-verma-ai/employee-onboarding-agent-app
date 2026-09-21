from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.sso as sso


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        sso,
        "settings",
        SimpleNamespace(
            entra_tenant_id="11111111-1111-1111-1111-111111111111",
            environment="production",
            allow_dev_auth=False,
        ),
    )
    monkeypatch.setenv("ENTRA_SPA_CLIENT_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.setenv("ENTRA_API_SCOPE", "api://test/access_as_user")
    monkeypatch.setenv("ENTRA_REDIRECT_URI", "https://onboarding.example/auth/callback")
    app = FastAPI()
    app.include_router(sso.router)
    return TestClient(app)


def test_public_configuration_contains_only_public_values(client, monkeypatch):
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "never-expose-me")
    response = client.get("/api/auth/config")
    assert response.json()["configured"] is True
    assert set(response.json()) == {
        "configured",
        "development",
        "client_id",
        "authority",
        "scope",
        "redirect_uri",
    }
    assert "never-expose-me" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "uri",
    [
        "http://onboarding.example/auth/callback",
        "https://evil@example.com/auth/callback",
        "https://example.com/auth/callback?next=evil",
        "https://example.com/other",
    ],
)
def test_unsafe_redirect_configuration_disabled(client, monkeypatch, uri):
    monkeypatch.setenv("ENTRA_REDIRECT_URI", uri)
    assert client.get("/api/auth/config").json() == {"configured": False, "development": False}


def test_missing_configuration_disabled(client, monkeypatch):
    monkeypatch.delenv("ENTRA_SPA_CLIENT_ID")
    assert client.get("/api/auth/config").json()["configured"] is False


def test_local_bundle_and_callback_do_not_handle_tokens(client):
    script = client.get("/assets/msal-browser.min.js")
    assert script.status_code == 200
    assert script.headers["x-content-type-options"] == "nosniff"
    callback = client.get("/auth/callback?code=sensitive")
    assert "sensitive" not in callback.text
    assert callback.headers["referrer-policy"] == "no-referrer"
