"""Read-only local production configuration inventory; never print secret values."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


def inspect(path):
    values = dotenv_values(path)
    required = [
        "AZURE_STORAGE_ACCOUNT_URL",
        "AZURE_STORAGE_CONTAINER",
        "ENTRA_TENANT_ID",
        "ENTRA_AUDIENCE",
        "ENTRA_SPA_CLIENT_ID",
        "ENTRA_API_SCOPE",
        "ENTRA_REDIRECT_URI",
        "WORKER_TENANT_ID",
        "WORKER_SUBJECT",
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_AGENT_NAME",
        "FOUNDRY_AGENT_VERSION",
        "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    ]
    missing = [key for key in required if not values.get(key)]
    checks = {
        "production_mode": values.get("ENVIRONMENT") == "production",
        "development_auth_disabled": str(values.get("ALLOW_DEV_AUTH", "false")).lower() == "false",
        "postgresql_configured": str(values.get("DATABASE_URL", "")).startswith("postgresql"),
        "https_redirect": urlparse(values.get("ENTRA_REDIRECT_URI") or "").scheme == "https",
    }
    return {
        "configuration_ready": not missing and all(checks.values()),
        "missing_variables": missing,
        "checks": checks,
        "live_services_verified": False,
        "note": "Configuration inventory only. Does not test permissions, TLS, sign-in, hosting, or malware scanning.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--report")
    args = parser.parse_args()
    if not Path(args.env_file).is_file():
        raise SystemExit("Environment file does not exist")
    result = inspect(args.env_file)
    text = json.dumps(result, indent=2)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["configuration_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
