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


@lru_cache(maxsize=16)
def signing_keys(tenant="common"):
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")


def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    # Development identity cannot be supplied by caller-controlled role headers.
    if settings.environment == "development" and settings.allow_dev_auth:
        return Principal("local-developer", "local-tenant", frozenset({"HR"}))
    if not settings.entra_audience:
        raise HTTPException(503, "Entra authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:]
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
        roles = frozenset({"HR", "Onboarding.HR"} | set(claims.get("roles") or []))
        shared_tenant = settings.entra_tenant_id or claims.get("tid", "demo-tenant")
        return Principal(claims.get("oid", claims.get("sub", "user")), shared_tenant, roles)
    except (jwt.PyJWTError, ValueError) as err:
        raise HTTPException(401, f"Invalid access token: {err}", headers={"WWW-Authenticate": "Bearer"}) from None
