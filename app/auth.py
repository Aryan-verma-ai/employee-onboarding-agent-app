import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException, Query

from .config import settings


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    roles: frozenset[str] = frozenset()

    @property
    def is_hr(self):
        return bool(self.roles & {"HR", "Onboarding.HR"})


@lru_cache(maxsize=16)
def signing_keys(tenant="common"):
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")


def get_principal(
    authorization: str | None = Header(default=None),
    token_param: str | None = Query(default=None, alias="token"),
) -> Principal:
    # Development identity cannot be supplied by caller-controlled role headers.
    if settings.environment == "development" and settings.allow_dev_auth:
        return Principal("local-developer", "local-tenant", frozenset({"HR"}))
    if not settings.entra_audience:
        raise HTTPException(503, "Entra authentication is not configured")
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif token_param:
        token = token_param
    else:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        token_tid = unverified.get("tid", "common")
        try:
            key = signing_keys(token_tid).get_signing_key_from_jwt(token)
        except Exception:
            key = signing_keys("common").get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=settings.entra_audience,
            options={"require": ["exp", "iat", "sub"], "verify_iss": False},
        )
        iss = claims.get("iss", "")
        if iss not in (
            f"https://login.microsoftonline.com/{token_tid}/v2.0",
            f"https://sts.windows.net/{token_tid}/",
        ):
            raise ValueError(f"Invalid issuer: {iss}")
        # Derive roles strictly from Entra token claims: app roles ('roles'), scopes ('scp'), or configured HR identities
        token_roles = set(claims.get("roles") or [])
        scp = claims.get("scp", "")
        if isinstance(scp, str) and ("Onboarding.HR" in scp or "HR" in scp.split()):
            token_roles.add("HR")
        email = claims.get("preferred_username") or claims.get("upn") or claims.get("email") or ""
        hr_emails = {e.strip().lower() for e in os.getenv("ENTRA_HR_EMAILS", "").split(",") if e.strip()}
        if email and email.lower() in hr_emails:
            token_roles.add("HR")
        roles = frozenset(token_roles)
        shared_tenant = settings.entra_tenant_id or claims.get("tid", "demo-tenant")
        return Principal(claims.get("oid", claims.get("sub", "user")), shared_tenant, roles)
    except (jwt.PyJWTError, ValueError) as err:
        raise HTTPException(
            401, f"Invalid access token: {err}", headers={"WWW-Authenticate": "Bearer"}
        ) from None
