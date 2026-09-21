import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    employee_id_prefix: str = os.getenv("EMPLOYEE_ID_PREFIX", "EMP")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./onboarding.db")
    environment: str = os.getenv("ENVIRONMENT", "production")
    allow_dev_auth: bool = os.getenv("ALLOW_DEV_AUTH", "false").lower() == "true"
    entra_tenant_id: str = os.getenv("ENTRA_TENANT_ID", "")
    entra_audience: str = os.getenv("ENTRA_AUDIENCE", "")
    storage_path: str = os.getenv("STORAGE_PATH", "./private-documents")
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))


def validate_configuration():
    import re

    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", settings.employee_id_prefix):
        raise RuntimeError("EMPLOYEE_ID_PREFIX must be 1-10 uppercase letters/digits")
    if settings.max_upload_bytes < 1 or settings.max_upload_bytes > 25 * 1024 * 1024:
        raise RuntimeError("MAX_UPLOAD_BYTES must be between 1 byte and 25 MiB")
    if settings.environment not in {"development", "production"}:
        raise RuntimeError("ENVIRONMENT must be development or production")
    if settings.environment == "production":
        required = {
            "ENTRA_TENANT_ID": settings.entra_tenant_id,
            "ENTRA_AUDIENCE": settings.entra_audience,
            **{
                key: os.getenv(key, "")
                for key in (
                    "FOUNDRY_PROJECT_ENDPOINT",
                    "FOUNDRY_AGENT_NAME",
                    "FOUNDRY_AGENT_VERSION",
                    "AZURE_STORAGE_ACCOUNT_URL",
                    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
                )
            },
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise RuntimeError("Missing production configuration: " + ", ".join(missing))
        if settings.allow_dev_auth or not settings.database_url.startswith("postgresql"):
            raise RuntimeError("Production requires PostgreSQL and ALLOW_DEV_AUTH=false")


settings = Settings()
