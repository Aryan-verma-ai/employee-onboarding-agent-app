"""Public, nonsecret configuration for MSAL authorization code with PKCE."""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import settings

router = APIRouter(tags=["sign-in"])


@router.get("/api/auth/config")
def auth_config():
    tenant = settings.entra_tenant_id
    client = os.getenv("ENTRA_SPA_CLIENT_ID", "")
    scope = os.getenv("ENTRA_API_SCOPE", "")
    redirect = os.getenv("ENTRA_REDIRECT_URI", "")
    parsed = urlparse(redirect)
    secure = parsed.scheme == "https" or (
        settings.environment == "development"
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
    )

    def valid_id(value):
        return bool(
            re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value
            )
        )

    configured = bool(
        valid_id(tenant)
        and valid_id(client)
        and scope.startswith("api://")
        and not any(c.isspace() for c in scope)
        and secure
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and parsed.path == "/auth/callback"
        and not parsed.query
        and not parsed.fragment
    )
    body = {
        "configured": configured,
        "development": settings.environment == "development" and settings.allow_dev_auth,
    }
    if configured:
        authority = os.getenv("ENTRA_AUTHORITY") or "https://login.microsoftonline.com/common"
        body.update(
            client_id=client,
            authority=authority,
            scope=scope,
            redirect_uri=redirect,
        )
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/auth/callback", response_class=HTMLResponse)
def callback():
    return HTMLResponse(
        '<!doctype html><html lang="en"><title>Completing sign-in</title><p>Completing Microsoft sign-in. This window closes automatically.</p></html>',
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/assets/msal-browser.min.js")
def msal_library():
    return FileResponse(
        Path(__file__).with_name("static") / "msal-browser.min.js",
        media_type="text/javascript",
        headers={"X-Content-Type-Options": "nosniff"},
    )
