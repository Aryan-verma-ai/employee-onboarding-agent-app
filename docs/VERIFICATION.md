# Local verification and remaining work

Current project: employee-onboarding-agent-kc, Korea Central; model deployment gpt-4.1-mini.
The existing application was retained and updated.

## Evidence

- 49 offline tests passed; 1 PostgreSQL integration test skipped because a local PostgreSQL service is unavailable.
- Ruff lint passed; final formatting check recorded at handoff.
- Editable package installation and dependency checks succeeded.
- SQLite Alembic upgrade/downgrade tested.
- Browser smoke tested consent/case creation, resume, employee corrections and validation that blocks missing documents.
- Supplied Azure endpoint reached; no-credential request returned 401.
- Live API-key model test passed: gpt-4.1-mini returned CONNECTION_OK. Evidence: outputs/model-verification.json (outside the source repository).
- No live managed-agent verification has completed. Do not interpret offline tests or HTTP 401 as cloud success.

## Manual configuration

The Git-ignored .env exists at:
C:\Users\richa\Documents\Codex\2026-09-20\https-github-com-aryan-verma-ai\work\repo\.env

1. Add your resource API key to AZURE_OPENAI_API_KEY in that file for the model-only smoke test. Do not paste it into chat.
2. For the actual managed agent, use your Entra-authenticated PowerShell terminal to run outputs/Connect-Azure.ps1. It targets the supplied project/deployment, creates an agent version, verifies real tool calls and conversation reuse, writes the evidence report, and updates only non-secret agent settings in .env.
3. AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET are optional service-principal alternatives. Leave the client-secret fields empty when using Azure CLI or managed identity. ENTRA_TENANT_ID/ENTRA_AUDIENCE separately configure user authentication to the application.
4. Production document processing additionally needs private Blob Storage, Defender scanning, Document Intelligence, PostgreSQL and the matching identity permissions. Development mode remains localhost-only with synthetic data until these are configured.

The sandbox cannot read the user's existing Windows Azure CLI profile. Running the connection helper under that authenticated user avoids copying credential caches or disabling encryption.

Authenticated confirmation download now implemented at GET /api/cases/{case_id}/confirmation; receipt uses actual employee issuance time and excludes identity/contact evidence. Seven tests cover authorization, status gates and audit. Optional empty Entra placeholders no longer break CLI credential fallback.

## Pending work

- Live Foundry model/agent verification, evaluations, tracing and restart continuity.
- Live Blob/Defender/OCR and PostgreSQL RLS/concurrency acceptance.
- Durable extraction worker/recovery, broader OCR fixtures/field coverage, production SSO and accessibility acceptance.
- GitHub branch publication, PR, issue completion comments and closure: connected GitHub writes returned 403; no issue was closed.

Local role ownership and acceptance gaps are documented in docs/ISSUES.md; review fixes in docs/REVIEW.md. Only close issues after their required acceptance criteria and publication are satisfied.
