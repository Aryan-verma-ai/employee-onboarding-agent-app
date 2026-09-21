# Architecture and implementation boundaries

This repository implements a FastAPI application around a **managed Azure AI Foundry prompt agent**. The agent is created and versioned in a Foundry project, rather than instantiated as a local chatbot. The application owns access control, document processing, deterministic business rules and employee creation. No live Azure deployment or end-to-end cloud acceptance run has been completed. See [FOUNDRY.md](FOUNDRY.md) for provisioning and cloud verification, and [ISSUES.md](ISSUES.md) for implementation ownership and open acceptance work.

## Components and request flow

1. The browser uses `/hr` for the dashboard and authenticated `/api/cases` and `/api/hr` APIs for data. The HTML shell contains no employee records.
2. `app/auth.py` validates Microsoft Entra bearer token signature, issuer, audience, expiry and configured tenant. It derives the subject and HR roles from verified claims. Development identity requires both development mode and explicit opt-in; production rejects that configuration.
3. `app/service.py` checks tenant and case ownership before access. HR can access cases within its tenant. A database session carries this principal into transaction-local PostgreSQL settings.
4. Uploads go to private Azure Blob Storage. Clean, authorized documents are processed with Azure Document Intelligence. Candidates retain document/page/confidence provenance and require human review.
5. `app/foundry.py` authorizes the case before calling Foundry. It loads or creates the case's persisted conversation and invokes the configured agent name and version through the project's Responses client. Model deployment selection occurs during provisioning.
6. HR reviews extracted evidence and corrected fields, validates the case, and explicitly confirms employee creation. The model cannot execute creation.

Production uses `DefaultAzureCredential` for Azure access and requires PostgreSQL. Configure managed identity permissions, private resources, scanner permissions and database runtime roles during deployment; repository code does not provision that infrastructure.

## Foundry tool boundary

`scripts/provision_agent.py` creates a `PromptAgentDefinition` version with instructions and strict function schemas. Runtime pins `FOUNDRY_AGENT_NAME` and `FOUNDRY_AGENT_VERSION`. The only tools are `get_onboarding_status` and `validate_onboarding`, both with an empty argument object. The server binds the authenticated case; the model cannot provide tenant, subject, case or conversation IDs.

Tool results contain status, missing field names and an employee-created flag. Raw OCR, employee fields and identity numbers are excluded. PAN and twelve-digit identity-like values are redacted from chat input/output as defense in depth; this is not comprehensive PII classification. Unknown tools and nonempty arguments are rejected. The loop permits eight response rounds. Audit metadata records response IDs and the agent reference. There is no simulated production fallback.

Conversation IDs persist before the first response. An in-process lock serializes chat in the supported single-worker deployment. Multiple workers require distributed conversation coordination. Foundry conversations and application records need coordinated retention/deletion policies before production use.

## Six workflow states

The exact state names are defined by `TRANSITIONS` in `app/service.py`.

| State | Meaning | Allowed next states |
| --- | --- | --- |
| `received` | Consent recorded and case created | `extracting`, `needs-information`, `validated`, `failed` |
| `extracting` | Document extraction underway | `needs-information`, `validated`, `failed` |
| `needs-information` | Required fields, documents or review remain | `extracting`, `validated`, `failed` |
| `validated` | Current deterministic checks pass | `created`, `extracting`, `needs-information`, `failed` |
| `created` | Employee persisted; case immutable | None |
| `failed` | Retryable processing failure | `extracting` |

Same-state requests are harmless. Actual validation requires required fields, an allowed department, and the latest PAN and Aadhaar documents with completed extraction, acceptable scan state and HR review. Uploads and corrections move cases back to `needs-information`; failure retries pass through `extracting`. Finalization rechecks all gates inside its transaction instead of trusting previous validation or agent output.

## Consent, review and confirmation

Case creation requires `consent: true` and records `consent_at` plus an audit event. Upload requires recorded consent. OCR produces candidates; even high-confidence candidates do not silently populate authoritative identity data. HR alone may attest reviewed document IDs, and those documents must belong to the case and have completed extraction. Users submit corrections through the authenticated form API.

Finalization requires an HR role, explicit `confirmed: true`, a validated case and an `Idempotency-Key`. Employee, idempotency record, status change and audit event commit together. IDs are UUID-backed with a configurable prefix, not sequential human numbering. Unique employee and case constraints prevent duplicate records, with conflict handling for retries. PostgreSQL concurrency behavior still needs deployment-level verification.

## Data model

| Table | Purpose and significant fields |
| --- | --- |
| `onboarding_cases` | Tenant, owner, department, consent time, status, corrected data, missing fields, Foundry conversation and employee ID |
| `documents` | Case/tenant, category, version, original filename, private storage key, hash, size, MIME, scan status and extraction evidence |
| `employees` | Unique employee ID, unique case association, tenant and finalized data snapshot |
| `idempotency_keys` | Tenant-scoped unique key linked to case and employee |
| `audit_events` | Tenant/case, actor, action, timestamp and metadata |

Sensitive fields and extraction candidates remain in the database. Audit helpers deliberately use field names, IDs and status metadata rather than raw document contents. Access to database backups and retention need deployment controls.

## Database, files and scanning

Alembic migration `0001` creates the schema and enables and forces PostgreSQL row-level security. Policies scope cases to tenant plus owner/HR and dependent tables to an accessible case. The SQLAlchemy transaction hook sets tenant, subject and HR settings locally for each transaction, avoiding pooled-connection identity leakage. Background extraction sessions must carry the initiating principal too. Run the application under a non-superuser role without `BYPASSRLS`; SQLite development tests do not prove PostgreSQL policy enforcement.

Uploads accept bounded PDF, PNG and JPEG data with matching signatures. Generated storage keys avoid using original filenames as paths. Identical category/content uploads are detected using SHA-256; replacement documents have versions. Production verifies that the blob container is private. Downloads and OCR require the trusted Defender for Storage tag `Malware Scanning scan result=No threats found`. Missing or unsafe scan evidence blocks processing. Deployment must prevent untrusted clients from writing scanner tags.

Development stores local private files and labels them `local-unscanned-dev`; this mode deliberately does not certify malware scanning. Extraction uses in-process background tasks, with retryable failure state. A durable queue and worker recovery are needed before relying on processing across application restarts.

## Validation and HR output

Current extraction recognizes PAN, Aadhaar and email patterns from Document Intelligence `prebuilt-read`, retaining page and minimum word confidence. Other required fields are entered and reviewed in the form. Conflicting or low-confidence candidates are flagged. Current PAN/Aadhaar checks are format checks, not official identity verification; Aadhaar checksum is not implemented. High-confidence PAN/Aadhaar/email candidate disagreements across required documents or against corrected data block validation; an audited override workflow remains future work.

The HR dashboard supports scoped listing, filters, details, document review, chat and explicit finalization. CSV/XLSX export uses a small allowlist excluding PAN/Aadhaar and escapes spreadsheet formula-like cells. The API caps listings and exports; pagination beyond those limits remains an extension.

## Verification boundaries

Unit and mocked integration tests exercise local application contracts. They do not establish live Foundry behavior, OCR accuracy, Azure RBAC, Defender scanning or PostgreSQL RLS/concurrency. Before production acceptance, run the deployed workflow with authorized test identities, real synthetic documents, scanner transitions and Foundry traces; test cross-tenant denial and concurrent finalization; and verify retention, network access, recovery and telemetry configuration.
