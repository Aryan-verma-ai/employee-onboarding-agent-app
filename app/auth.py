from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException

from .config import settings


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    roles: frozenset[str] = frozenset()

    @property
    def is_hr(self):
        return bool(self.roles & {"HR", "Onboarding.HR"})


@lru_cache(maxsize=4)
def signing_keys(tenant):
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")


def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    # Development identity cannot be supplied by caller-controlled role headers.
    if settings.environment == "development" and settings.allow_dev_auth:
        return Principal("local-developer", "local-tenant", frozenset({"HR"}))
    if not settings.entra_tenant_id or not settings.entra_audience:
        raise HTTPException(503, "Entra authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
    try:
        key = signing_keys(settings.entra_tenant_id).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=settings.entra_audience,
            issuer=f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0",
            options={"require": ["exp", "iat", "sub", "tid"]},
        )
        if claims["tid"] != settings.entra_tenant_id:
            raise ValueError("Unexpected tenant")
        return Principal(claims.get("oid", claims["sub"]), claims["tid"], frozenset(claims.get("roles", [])))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(401, "Invalid access token", headers={"WWW-Authenticate": "Bearer"}) from None
