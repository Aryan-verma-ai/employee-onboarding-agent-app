# Issue ownership and acceptance ledger

Repository: [Aryan-verma-ai/employee-onboarding-agent-app](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app)

This is the **local execution ledger**, not a claim that GitHub assignees or issue states changed. GitHub connector mutation attempts returned HTTP 403. Remote assignment, completion comments and closure therefore remain pending. No issue is marked fully accepted here, and no live Azure deployment has been completed. Issue numbers below follow the retrieved issue inventory; labels summarize scope.

The architect defines contracts and documents the design. The Azure AI expert owns Foundry integration. Developer 1 owns the API/data foundation and employee creation. Developer 2 owns document ingestion, extraction, rules and HR experience. The code reviewer evaluates the integrated result independently. These are agent-role assignments, not GitHub user assignments.

| Issue | Local accountable role | Implementation present | Acceptance work still open |
| --- | --- | --- | --- |
| [#1 Foundation](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/1) | Developer 1; architect consults | FastAPI, configuration checks, Entra principal, case APIs, health/readiness, Docker scaffolding | Deployed Entra token/role checks and runtime readiness; production release acceptance |
| [#2 Persistence and RLS](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/2) | Developer 1; reviewer validates | SQLAlchemy models, Alembic migration, PostgreSQL RLS policies and transaction-local principal | Live PostgreSQL migration/rollback and owner/HR/cross-tenant policy tests using a non-bypass runtime role; background-session verification |
| [#3 Private document upload](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/3) | Developer 2 | Consent gates, bounded signature checks, private blobs, hashes, versions, authorized download and scanner gate | Real Blob RBAC/private-container and Defender tag tests; scanner-tag write restrictions; concurrent version behavior and storage recovery |
| [#4 OCR extraction](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/4) | Developer 2; AI expert consults | Document Intelligence adapter, background execution, PAN/Aadhaar/email candidates, provenance/confidence and retry state | Live synthetic-document accuracy and retry checks; broader field coverage; durable queue/restart recovery |
| [#5 Validation and consent](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/5) | Developer 2; architect consults | Explicit initial consent, required fields/documents, syntax checks, uncertain/conflicting candidate review, HR review gate | Business acceptance of rule set; Aadhaar checksum if required; cross-document reconciliation; consent versioning/withdrawal and retention policy |
| [#6 Foundry orchestration](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/6) | Azure AI expert | Managed agent version provisioning, pinned runtime reference, persisted conversations, bounded restricted tools, metadata audit and synthetic evaluation runner | Provision real Azure version and model, execute cloud evaluations/traces and restart continuity; configure monitoring and retention; distributed lock before multi-worker scaling |
| [#7 Employee IDs and creation](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/7) | Developer 1; reviewer validates | UUID-backed prefixed IDs, unique case/employee constraints, transactional finalization, HR confirmation and tenant-scoped idempotency | PostgreSQL concurrent finalization/retry acceptance; confirm UUID format meets business numbering requirements |
| [#8 HR dashboard/export](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/issues/8) | Developer 2 | HR-scoped dashboard APIs, details/audit, filters, review/finalize UI, CSV/XLSX allowlist and formula escaping | Browser acceptance with live Entra identities; accessibility and request-race verification; pagination/export limit acceptance |

## Review follow-through

The reviewer identified background extraction principal propagation for RLS, a dashboard request race, and handling of invalid departments. Fixes are being integrated; final verification evidence belongs in the root test report. Do not interpret the existence of a patch or this ledger as successful cloud verification. Reviewer findings must be checked against the final integrated revision before any completion claim.

## Conditions for a completion comment and closure

For each issue, attach the relevant implementation revision, tests actually run, their results and remaining acceptance limits. Verify its original acceptance criteria rather than closing on code presence alone. GitHub comments must clearly distinguish offline tests from live Azure verification. Close only after required acceptance work is satisfied and write access succeeds; otherwise leave the issue open with an honest progress comment.

Once repository permissions are available, reconcile this local ledger with GitHub, apply actual user assignments as appropriate, post per-issue progress/completion evidence, and close only genuinely completed issues. Do not post blanket completion comments for all eight issues.
