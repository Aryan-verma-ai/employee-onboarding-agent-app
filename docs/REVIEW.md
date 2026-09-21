# Code review

Reviewed 2026-09-21 by the code-reviewer agent. Scope: Foundry gateway and tool boundary, tenant authorization/RLS, consent and human approval, document extraction, deterministic validation, employee creation, and dashboard behavior. This is a source review, not a deployment certification.

## Findings and disposition

| Priority | Finding | Disposition |
| --- | --- | --- |
| P1 | Background extraction sessions omitted the authenticated principal. PostgreSQL transaction context would be empty and RLS would prevent the worker from reading its case. | Fixed: `extraction_job` sets `db.info["principal"]` before its first query. |
| P1 | Dashboard selected the next case before its response arrived; previous form data and approval could target the wrong case. | Fixed: load generations discard stale responses, clear the selected case, hide the old form, and disable mutation controls during loading. |
| P2 | Creation accepted departments rejected by validation, with no department correction endpoint. | Fixed: creation now restricts departments to the deterministic allowlist. |
| P2 | Export ignored active queue filters. | Fixed: export sends department, status, and employee ID filters. |
| P2 | Employee Resume populates completed document IDs, but Save submits them as HR attestations; after the new authorization guard, employee corrections fail with 403. Successful employee save/validate also refreshes the HR-only detail endpoint. | Fixed in final integration: distinct employee/HR modes, employee saves no review IDs, and mode-appropriate refresh. Coordinator browser smoke verified employee create/resume/save/validate. |

Additional integrated controls inspected: only HR can attest document review or finalize; changing record data invalidates old attestations; finalization revalidates inside the transaction; tenant/PAN fingerprint uniqueness prevents duplicate employee creation; high-confidence conflicting evidence blocks validation; production startup rejects incomplete configuration, SQLite, and development authentication. The model has only status and deterministic-validation tools, with server-bound case identity and no employee-creation tool.

## Verification evidence and limits

The coordinating agent reported 35 passing offline tests at re-review start and one locally skipped PostgreSQL integration test. Later verification results belong in the final handoff/test report. This reviewer did not execute the test suite or independently verify the cloud.

- Azure Foundry provisioning, agent execution, conversation continuity, Document Intelligence OCR, Defender scan tags, managed identities, and deployment remain unverified against real resources.
- PostgreSQL RLS and transaction/concurrency behavior require the configured PostgreSQL integration job; SQLite does not demonstrate PostgreSQL row locking or RLS.
- Browser event ordering was reviewed in source. A browser smoke test should exercise case switching, employee corrections, HR attestation, uploads/retries, and filtered exports.
- Chat serialization is in-process and supports the documented single-worker deployment. Distributed coordination is necessary before multiple workers.
- OCR jobs use process-local background tasks. A process restart can interrupt work; users must retry extraction. A durable queue is a future operational improvement.
- Conflict handling intentionally fails closed. High-confidence OCR mistakes require corrected/replacement evidence; no audited override workflow exists.
- Identity-number patterns in chat are a limited redaction measure, not comprehensive personal-data detection. Resource retention, telemetry access, and deletion policies need deployment configuration.
- The dashboard currently uses pasted Entra access tokens. A production sign-in experience and broader accessibility/usability acceptance remain separate work.
- GitHub publication, issue comments, and closure were reported blocked by connector authorization (403); local completion does not establish remote completion.

No application code was changed by this reviewer.
