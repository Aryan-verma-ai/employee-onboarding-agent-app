"""Azure identity selection with safe handling of optional .env placeholders."""

import os

from azure.identity import DefaultAzureCredential


def azure_credential():
    tenant = os.getenv("AZURE_TENANT_ID", "").strip()
    client = os.getenv("AZURE_CLIENT_ID", "").strip()
    secret = os.getenv("AZURE_CLIENT_SECRET", "").strip()
    certificate = os.getenv("AZURE_CLIENT_CERTIFICATE_PATH", "").strip()
    if (secret or certificate) and not (tenant and client):
        raise ValueError("Service-principal authentication requires AZURE_TENANT_ID and AZURE_CLIENT_ID")
    # dotenv exports empty placeholders as present variables. EnvironmentCredential
    # otherwise interprets those as a configured (but invalid) service principal.
    use_environment = bool(tenant and client and (secret or certificate))
    return DefaultAzureCredential(exclude_environment_credential=not use_environment)
