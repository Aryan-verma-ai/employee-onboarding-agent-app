# Employee onboarding with Azure AI Foundry

An employee-onboarding service whose conversational agent is created and versioned in **Azure AI Foundry Agent Service**. FastAPI implements authenticated tools and the HR workflow; Azure Document Intelligence extracts document candidates and private Azure Blob Storage holds uploads. There is no replacement local chatbot.

## Start locally

Requires Python 3.11+ (tested with 3.12). From this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[test]'
Copy-Item .env.example .env
python -m dotenv run -- python -m alembic upgrade head
python -m dotenv run -- python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

On macOS/Linux use `source .venv/bin/activate` and `cp .env.example .env`; the remaining commands are the same. Open `http://127.0.0.1:8000/hr` for the dashboard and `/docs` for the API. `/health` is liveness; `/ready` checks configuration and migrated database availability, not Azure connectivity.

The example explicitly enables a development HR identity and local private file storage. Bind it only to localhost and use synthetic documents. Azure OCR and chat still require actual configured Azure services; missing cloud configuration produces an error, not fabricated extraction or agent responses. Production defaults fail closed without Entra, PostgreSQL, storage, OCR and pinned Foundry settings.

## Create the actual Foundry agent

Follow [Foundry setup](docs/FOUNDRY.md). Sign into Azure using an identity permitted to access the selected project, fill in the endpoint and model deployment name in `.env`, then run:

```powershell
python -m dotenv run -- python -m scripts.provision_agent
```

Set `FOUNDRY_AGENT_VERSION` to the returned version. Restart the service and use the case chat. The runtime references this registered agent by name/version and persists its Foundry conversation ID. It does not create agents per request. Run the optional live synthetic evaluation with `python -m dotenv run -- python -m evaluations.run` after provisioning.

## Workflow

1. Create a case in an allowed department with consent.
2. Upload PAN/Aadhaar and supporting PDF/PNG/JPEG documents. Production download and extraction wait for a clean Defender scan result.
3. Start extraction and refresh the case. Candidates retain document, page and confidence provenance; HR reviews them and supplies missing fields.
4. Save corrections, attest review, and validate. Required fields/documents, scanning, review, conflicting high-confidence evidence and duplicate PAN checks block finalization.
5. Use the explicit HR confirmation to create one employee record. Retries return the same ID. Download the minimal issuance receipt from `GET /api/cases/{case_id}/confirmation`. Chat can explain missing data and request validation; it cannot approve or create employee records.
6. Filter the HR queue and export authorized fields as CSV/XLSX. Identity numbers are excluded from exports.

Changing record fields invalidates prior document review. High-confidence conflicting evidence requires a corrected/replacement source document; the model cannot override it. Identifier syntax checks do not verify identity with a government service.

## Tests and implementation records

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

CI also provisions PostgreSQL and runs direct-table RLS isolation tests with a non-superuser. The local PostgreSQL test is skipped unless `TEST_POSTGRES_URL` points at a dedicated database named `onboarding_test`. Tests use synthetic records; passing offline tests is not evidence of a live Azure deployment.

- [Architecture and security boundaries](docs/ARCHITECTURE.md)
- [Azure deployment checklist](docs/DEPLOYMENT.md)
- [Issue ownership and acceptance ledger](docs/ISSUES.md)
- [Code review](docs/REVIEW.md)
- [Verification results](docs/VERIFICATION.md)

Current boundaries: extraction jobs run in-process and require manual retry after a restart; chat is supported with one application worker; OCR maps PAN/Aadhaar/email candidates, with other fields entered by HR; document download uses an authenticated proxy rather than signed URLs. See the issue ledger for remaining work before production rollout.
