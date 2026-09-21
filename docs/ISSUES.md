# Issue ownership and completion evidence

Existing implementation retained; existing Foundry agent employee-onboarding version1, Korea Central, gpt-4.1-mini. Live eight-scenario Foundry evaluation and synthetic PDF/PNG Document Intelligence extraction passed. Git authenticated transport successfully published feat/azure-foundry-onboarding; the separate connector returned403 and is not retried.

| Issue | Responsible agent role | Implemented and checked | Remaining |
|---|---|---|---|
|1 Foundation|Developer1 / architect|FastAPI configuration, health/readiness, CI, documented startup|Deployment acceptance separate from skeleton|
|2 Data|Developer1 / reviewer|Migrations0001-0003, provenance, consent/validation, real PostgreSQL RLS and concurrent transaction tests|Production DB connection|
|3 Documents|Developer2|Private authenticated downloads, upload validation/versioning/audit, scanner gate|Live private Blob/Defender setup; authenticated proxy intentionally replaces signed URL exposure|
|4 Extraction|Developer2|Durable leased jobs/retries/recovery; scoped worker; PDF/PNG live OCR all five fields; confidence/provenance review|Production worker hosting|
|5 Validation|Developer2 / root|Required fields/docs, conflicts, duplicates, deterministic outcomes/explanations, consent version/withdrawal|Organization retention policy before real employee data|
|6 Foundry|AI expert|Pinned managed agent, persisted conversations, two restricted tools, distributed lock, optional tracing, live8/8 evaluation|Original broad tool scope differs: uploads and finalization remain authenticated API/human controls; no model employee-creation tool|
|7 Records|Developer1 / reviewer|Transactional collision-safe IDs, HR confirmation, receipt, real PostgreSQL concurrent idempotence and rollback|Production release acceptance|
|8 Dashboard|Developer1|HR queue, filters, pagination, bounded exports, bundled MSAL sign-in, accessible focus/status, account-switch clearing|Live Entra sign-in and final browser accessibility acceptance|

Roles above are internal subagent responsibilities, not GitHub account assignments. Issues with remaining acceptance work must stay open. GitHub comments should link the implementation commit and distinguish local PostgreSQL tests from live Azure tests. No new subscription, resource group or Foundry project was created.

## Published checkpoint

Commit0806186 is pushed; PR#9 is open for review. GitHub Actions succeeded for this revision. Evidence comments were posted on all eight issues; issues1,2,4,5,7 closed after acceptance checks. Issues3,6,8 remain open for the specific gaps above.
