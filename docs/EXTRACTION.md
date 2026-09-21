# Durable document extraction

`POST /api/cases/{case_id}/documents/{document_id}/extract` commits an `extraction_jobs` row and returns HTTP 202. The queue stores identifiers, scheduling metadata and safe error codes, never document bytes or OCR text. Duplicate requests return the existing active job. Failed/completed jobs can be explicitly queued again; human review is invalidated whenever extraction is requested.

Run migrations (`python -m alembic upgrade head`), then run a separately supervised process with:

```powershell
$env:WORKER_TENANT_ID = '<authorized tenant UUID>'
$env:WORKER_SUBJECT = '<dedicated worker actor ID>'
python -m app.worker
```

Use the existing runtime DATABASE_URL with a non-superuser, non-BYPASSRLS database role. Configure one worker per explicitly authorized tenant. The worker binds its tenant and HR service principal before every database transaction. Discovery only scans that tenant and the migration forces RLS; there is no global privileged discovery or silent RLS bypass. Restrict deployment configuration access: WORKER_TENANT_ID is an operator trust boundary, not caller input. The HR service principal is a backend actor, not an Entra token impersonation.

Workers use an atomic conditional update to claim jobs. A five-minute lease permits process-crash recovery; an expired worker's token cannot persist results after another worker claims the job. Results and job completion commit in one transaction. Failed extraction retries exponentially after 10, 20, 40 and 80 seconds (capped at five attempts); terminal failures remain available for explicit requeue. Polling is every two seconds when idle. Process supervision must restart a crashed worker. OCR provider timeouts should remain below the lease duration. At-least-once calls can occur after a crash or expired lease, so repeated Document Intelligence billing is possible; fencing ensures only the current lease persists results.

The worker checks active consent before reading content and again before saving results. Withdrawal cancels queued results. Production reads remain blocked until Defender marks the blob clean. Scan delays currently consume retry attempts; a user may retry after a clean scan. The worker never logs OCR/provider error text. It does not automatically populate the employee record: HR reviews extracted candidates and resolves disagreements.

`prebuilt-read` produces deterministic normalized candidates for labeled name/phone, email, PAN and Aadhaar. Every candidate records document, page, line offset, method and confidence. Unlabeled names and international naming variations require manual entry. This is format/evidence extraction, not government identity verification.

## Evidence

`tests/fixtures/synthetic-onboarding.pdf` and `.png` are actual synthetic documents bearing a conspicuous fixture disclaimer. `tests/test_jobs.py` verifies normalized fields using a mocked Document Intelligence response, retry persistence, consent cancellation, tenant discovery, concurrent claims and stale-worker fencing across independent database sessions. Live Azure Document Intelligence testing on 2026-09-21 additionally returned all five expected field types from each committed PDF and PNG fixture, with five candidates and no low-confidence flags. Sanitized results are recorded in `OCR_FIXTURE_RESULTS.json`. These two synthetic examples do not establish population-level OCR accuracy, malware integration or PostgreSQL RLS validation. The PDF contains native text and the PNG represents a raster scan; neither is a real employee identity document.

## Authentication

Identity authentication remains the default. Set `AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE=api_key` to explicitly use `AZURE_DOCUMENT_INTELLIGENCE_API_KEY`. If the dedicated key is absent, the existing `AZURE_OPENAI_API_KEY` is reused only when the Document Intelligence endpoint is HTTPS and its host exactly matches `FOUNDRY_PROJECT_ENDPOINT`. A different host fails before making the request. Never put keys in queued jobs, fixtures, source control or logs.
