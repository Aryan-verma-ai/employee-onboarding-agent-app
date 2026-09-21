# Existing Azure deployment and remaining configuration

Use the existing subscription `47e1bcaf-28a0-466f-9f7b-d32d7289eebe`, tenant `a8780332-fecd-4f97-81b8-3782131af31a`, resource group `rg-richakadian077-2335`, resource `employee-onboarding-kc-resource`, project `employee-onboarding-agent-kc` in Korea Central. Do not create a replacement project or subscription.

## Verified services

The project endpoint is `https://employee-onboarding-kc-resource.services.ai.azure.com/api/projects/employee-onboarding-agent-kc`. Existing managed agent `employee-onboarding`, version `1`, uses `gpt-4.1-mini`. Live managed-agent reconnection and eight evaluation scenarios passed. The same resource root serves Document Intelligence prebuilt-read; both synthetic PDF and PNG fixtures extracted all five expected fields. No new Azure resources were created.

The ignored local `.env` selects `FOUNDRY_AUTH_MODE=api_key` and `AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE=api_key`. Credentials remain outside Git. The shared resource key is reused for OCR only after an exact HTTPS resource-host match. Entra remains the default auth mode for deployments using managed identity. See OBSERVABILITY.md and EXTRACTION.md.

## Production setup still required

1. Supply an approved private Blob account URL/container with Defender malware scanning enabled. The runtime must read blobs and trusted scanning tags; employees must not receive storage credentials or tag-write permissions. Downloads are authenticated proxies rather than public or signed blob URLs. Actual Blob/Defender acceptance remains unverified.
2. Supply a production PostgreSQL connection with TLS. Apply `python -m alembic upgrade head` using the migration identity. Grant a separate NOSUPERUSER/NOBYPASSRLS runtime role schema USAGE and business-table SELECT/INSERT/UPDATE. Include extraction_jobs. Runtime identities set tenant/subject/HR transaction-locally. Local real PostgreSQL migration, RLS, concurrent finalization and lock tests passed; this is not an Azure database deployment claim.
3. Configure an Entra API/SPA registration, API audience and delegated scope, HR app role assignment, and HTTPS redirect URI. See SSO.md. No app client secret belongs in the browser. Live tenant sign-in still needs these registration values.
4. Set ENVIRONMENT=production and ALLOW_DEV_AUTH=false. Deploy the existing image to approved compute. Run a separate tenant-scoped `python -m app.worker` process; see EXTRACTION.md. PostgreSQL conversation advisory locks and durable extraction leases permit coordinated workers; SQLite is development-only.
5. Configure the optional OpenTelemetry exporter and approved trace retention. Never capture prompts, identity values or credentials in traces. Configure organization-approved retention across documents, records, conversations and audit data before real employee use.
6. Run the full deployed synthetic flow: upload, trusted clean scan, OCR, HR corrections/review, validation, explicit HR finalization, receipt, sign-out and cross-user/tenant denial. Current live evidence covers Foundry and OCR, not the remaining infrastructure.

## Consent and migrations

Consent policy version 1 identifies the application's initial affirmative collection workflow; it is not legal approval. An employee can POST `/api/cases/{id}/consent/withdraw` with `confirmed=true`. Subsequent collection, OCR, validation and chat require active consent. Historical records/audits remain available for authorized review; withdrawal does not automatically erase records. Retention/deletion needs an approved organizational policy.

Use migrations for existing databases, including local databases created by an earlier version. `create_all` does not upgrade existing tables. Back up existing data first. Do not downgrade populated production tables. Migration0002 adds durable jobs;0003 adds consent and validation evidence.

Recovery guardrail: do not create duplicate agents or replace existing Azure resources. `scripts.provision_agent` is only a deliberate new-version promotion step, never a connectivity check.
