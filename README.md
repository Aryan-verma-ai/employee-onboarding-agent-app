# Onboardly — AI-Powered Employee Onboarding with Azure AI Foundry

[![Tests](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/actions/workflows/test.yml/badge.svg?branch=feat/azure-foundry-onboarding)](https://github.com/Aryan-verma-ai/employee-onboarding-agent-app/actions)

An enterprise-grade employee onboarding service powered by **Azure AI Foundry Agent Service**. The system automates document intake, OCR-based field extraction, deterministic validation, and employee record creation — all orchestrated through a conversational AI agent with strict human-in-the-loop guardrails.

> **Built with:** FastAPI · Azure AI Foundry · Azure Document Intelligence · Azure Blob Storage · Microsoft Entra ID · PostgreSQL · SQLAlchemy · Alembic · OpenTelemetry

---

## ✨ Key Features

| Category | Capabilities |
|---|---|
| **AI Agent** | Azure AI Foundry managed agent (`gpt-4.1-mini`) with 6 server-side tools, PII redaction, and prompt injection resistance |
| **Document Processing** | Secure upload to private Azure Blob Storage with Microsoft Defender malware scanning and Azure Document Intelligence OCR |
| **Field Extraction** | Automated extraction of Name, Phone, Email, PAN, and Aadhaar from PDF/PNG/JPEG with confidence scores and page-level provenance |
| **Smart Classification** | Intelligent PAN/Aadhaar photo classification distinguishing identity card photos from candidate portrait headshots |
| **Validation** | Deterministic rules engine detecting missing fields, cross-document conflicts, duplicate PAN checks, and consent policy enforcement |
| **Authentication** | Microsoft Entra ID SSO via MSAL PKCE with JWT RSA signature verification, tenant isolation, and HR role authorization |
| **HR Dashboard** | Full-featured single-page HR workspace with queue management, case filtering, real-time extraction polling, and CSV/XLSX export |
| **Notifications** | Automated welcome email dispatch via SMTP with Day 1 instructions and credentials |
| **Data Security** | PostgreSQL Row-Level Security (RLS) on all tables, SHA-256 document fingerprinting, and tenant/subject isolation |
| **Idempotent Finalization** | Atomic employee creation with `Idempotency-Key` headers preventing duplicate records |

---

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│   Browser/SPA   │────▶│   FastAPI Backend     │────▶│  Azure AI Foundry Agent │
│  (MSAL PKCE)    │     │  (Auth · Service ·    │     │  (gpt-4.1-mini, v9)     │
│                 │◀────│   Validation · API)   │◀────│  6 tools, 8-round max   │
└─────────────────┘     └──────────┬───────────┘     └─────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
           ┌──────────────┐ ┌───────────┐ ┌─────────────────┐
           │ Azure Blob   │ │PostgreSQL │ │ Azure Document  │
           │ Storage +    │ │ (RLS) /   │ │ Intelligence    │
           │ Defender     │ │ SQLite    │ │ (prebuilt-read) │
           └──────────────┘ └───────────┘ └─────────────────┘
```

The AI agent **cannot** create employees, bypass validation, or override conflicting evidence. All state mutations go through deterministic business logic with explicit HR confirmation. See [Architecture and Security Boundaries](docs/ARCHITECTURE.md) for full details.

---

## 🔄 Workflow

1. **Create Case** — HR creates an onboarding case in an allowed department with recorded consent (policy version tracked).
2. **Upload Documents** — Upload PAN, Aadhaar, and supporting PDF/PNG/JPEG documents. Downloads wait for a clean Defender malware scan result.
3. **Extract Fields** — Start OCR extraction via the durable background worker. Candidates retain document, page, and confidence provenance. HR reviews and corrects fields.
4. **Validate** — Save corrections, attest document review, and run validation. Missing fields, incomplete scans, unreviewed documents, conflicting high-confidence evidence, and duplicate PAN checks block finalization.
5. **Finalize** — Explicit HR confirmation with `Idempotency-Key` atomically creates a unique `EMP-XXXXXX` employee record. Retries return the same ID.
6. **Export** — Download confirmation receipt from `GET /api/cases/{case_id}/confirmation`. Filter the HR queue and export authorized fields as CSV/XLSX (identity numbers excluded).

> Changing record fields invalidates prior document review attestations. High-confidence conflicting evidence requires a corrected source document — the AI model cannot override it. The chat agent can explain missing data and request validation but cannot approve or create employee records.

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.11+** (tested with 3.12)
- Azure services for full functionality (Foundry, Document Intelligence, Blob Storage, Entra ID)
- PostgreSQL for production; SQLite works for local development

### Local Setup

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.[test]'
Copy-Item .env.example .env
# Edit .env with your Azure credentials
python -m dotenv run -- python -m alembic upgrade head
python -m dotenv run -- python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

**macOS / Linux:**

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
cp .env.example .env
# Edit .env with your Azure credentials
python -m dotenv run -- python -m alembic upgrade head
python -m dotenv run -- python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Once running, open:
- **HR Dashboard:** [`http://127.0.0.1:8000/hr`](http://127.0.0.1:8000/hr)
- **API Documentation:** [`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs)
- **Health Check:** `/health` (liveness) · `/ready` (configuration + database)

> **Note:** The local example enables a development HR identity and local file storage. Bind only to localhost and use synthetic documents. Azure OCR and chat require configured Azure services — missing cloud configuration produces an error, never fabricated responses. Production defaults fail closed without Entra, PostgreSQL, storage, OCR, and pinned Foundry settings.

---

## 🤖 Azure AI Foundry Agent Setup

Follow [Foundry Setup Guide](docs/FOUNDRY.md) to configure the agent. Then:

```bash
python -m dotenv run -- python -m scripts.provision_agent
```

Set `FOUNDRY_AGENT_VERSION` in `.env` to the returned version. Restart the service.

The runtime references the registered agent by pinned name/version and persists Foundry conversation IDs per case. It does not create agents per request.

**Run live evaluations** (optional):

```bash
python -m dotenv run -- python -m evaluations.run
```

This executes 8 synthetic scenarios testing workflow correctness, safety (prompt injection resistance, PII non-disclosure), tool execution, and escalation behavior.

---

## 📁 Project Structure

```
├── app/
│   ├── main.py              # FastAPI application factory and API routers
│   ├── auth.py              # Entra JWT validation, Principal model, dev auth fallback
│   ├── config.py            # Settings dataclass and production config validation
│   ├── models.py            # SQLAlchemy ORM (Case, Document, Employee, AuditEvent, etc.)
│   ├── service.py           # Business logic, state machine, HR attestation
│   ├── foundry.py           # Foundry agent gateway, tool dispatch, chat loop
│   ├── validation.py        # Deterministic validation rules and format checkers
│   ├── extraction.py        # Document Intelligence OCR and field normalization
│   ├── documents.py         # Upload validation, blob storage, Defender scan check
│   ├── jobs.py              # Durable extraction job queue with CAS leases
│   ├── worker.py            # Background worker process for queued OCR
│   ├── dashboard.py         # HR dashboard routes, pagination, stats, export
│   ├── confirmation.py      # Employee confirmation receipt generation
│   ├── notifications.py     # Welcome email formatting and SMTP delivery
│   ├── photo_extract.py     # Portrait extraction from PAN/Aadhaar/Resume
│   ├── chat_lock.py         # PostgreSQL advisory lock / SQLite in-process lock
│   ├── db.py                # SQLAlchemy engine, session factory, RLS hook
│   ├── sso.py               # SSO config endpoint for frontend MSAL
│   ├── knowledge.py         # Company handbook knowledge base
│   └── static/              # Vendored frontend (MSAL Browser 4.27.0, dashboard, landing page)
├── migrations/              # Alembic migrations (0001–0003)
├── tests/                   # 100+ automated tests (15 test modules, 2 fixtures)
├── scripts/                 # Provisioning, verification, and production check utilities
├── evaluations/             # 8 synthetic agent evaluation scenarios
├── docs/                    # Architecture, deployment, SSO, extraction, and review docs
├── Dockerfile               # Production container (python:3.12-slim, UID 10001)
├── pyproject.toml           # Dependencies and tool configuration
└── .github/workflows/       # CI pipeline (Ruff lint/format, Pytest with PostgreSQL)
```

---

## ✅ Tests and CI

```bash
# Run the full test suite
python -m pytest -q

# Lint and format checks
python -m ruff check .
python -m ruff format --check .
```

**Test coverage includes:**
- API route lifecycle (cases, consent, documents, chat, finalization)
- Core service state machine transitions and audit logging
- Foundry agent tool dispatch and 8-round loop enforcement
- Durable job queue with CAS race conditions and exponential retry
- PAN/Aadhaar regex parsing and OCR field extraction
- Dashboard filtering, pagination, and export limits
- PostgreSQL RLS tenant isolation and concurrent finalization
- SSO configuration and PKCE verification
- Prompt injection resistance and PII non-disclosure

**CI pipeline** (GitHub Actions) provisions PostgreSQL 16 and runs:
1. `ruff check .` — Linting
2. `ruff format --check .` — Formatting
3. `pytest -q` — Full test suite including RLS isolation tests with a non-superuser

> Local PostgreSQL tests are skipped unless `TEST_POSTGRES_URL` points at a dedicated database named `onboarding_test`.

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and configure. Key variables:

| Variable | Description |
|---|---|
| `ENVIRONMENT` | `development` or `production` |
| `ALLOW_DEV_AUTH` | `true` in dev; **must be** `false` in production |
| `DATABASE_URL` | SQLite (dev) or PostgreSQL (production) connection string |
| `FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint URL |
| `FOUNDRY_AGENT_NAME` | Registered agent name (default: `employee-onboarding`) |
| `FOUNDRY_AGENT_VERSION` | Pinned agent version |
| `FOUNDRY_AUTH_MODE` | `entra` (default) or `api_key` |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | Azure Document Intelligence endpoint |
| `AZURE_STORAGE_ACCOUNT_URL` | Azure Blob Storage account URL |
| `ENTRA_TENANT_ID` / `ENTRA_SPA_CLIENT_ID` | Microsoft Entra SSO configuration |
| `WORKER_TENANT_ID` / `WORKER_SUBJECT` | Background extraction worker identity |

See [`.env.example`](.env.example) for the complete list.

---

## 🐳 Deployment

### Docker

```bash
docker build -t onboardly .
docker run -p 8000:8000 --env-file .env onboardly
```

The container runs as an unprivileged user (`UID 10001`) on `python:3.12-slim` with a single Uvicorn worker for correct chat serialization and advisory lock behavior.

### Azure Container Apps

The application is deployed on **Azure Container Apps** (Korea Central, Consumption tier) with `minReplicas=0`. Run the background extraction worker separately:

```bash
python -m app.worker
```

Run database migrations before starting against an existing database:

```bash
python -m alembic upgrade head
```

See [Deployment Checklist](docs/DEPLOYMENT.md) and [Production Setup](docs/PRODUCTION-SETUP.md) for the full provisioning sequence.

---

## 📚 Documentation

| Document | Description |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | System design, request flow, security boundaries, and Foundry tool contracts |
| [Foundry Setup](docs/FOUNDRY.md) | Azure AI Foundry agent provisioning, authentication, and conversation locking |
| [Deployment](docs/DEPLOYMENT.md) | Azure deployment checklist and verified services |
| [Production Setup](docs/PRODUCTION-SETUP.md) | Step-by-step Azure provisioning (Storage, Entra, PostgreSQL, Container Apps) |
| [Extraction](docs/EXTRACTION.md) | Durable OCR job queue architecture and worker execution |
| [SSO](docs/SSO.md) | Microsoft Entra browser sign-in with MSAL PKCE |
| [Observability](docs/OBSERVABILITY.md) | OpenTelemetry tracing and conversation coordination |
| [Verification](docs/VERIFICATION.md) | Live verification results for Foundry, OCR, and PostgreSQL |
| [Code Review](docs/REVIEW.md) | Code review findings and dispositions |
| [Issues](docs/ISSUES.md) | Issue ownership, completion matrix, and acceptance ledger |

---

## 🔒 Security Boundaries

- **AI guardrails:** The Foundry agent cannot create employees, bypass validation, approve cases, or disclose raw PAN/Aadhaar numbers. Prompt injection attempts are rejected.
- **Tenant isolation:** PostgreSQL RLS enforces tenant boundaries on all 6 tables. Session-local settings (`app.tenant_id`, `app.subject`, `app.is_hr`) are set per request.
- **Document security:** Uploads go to private Azure Blob Storage. Downloads use authenticated proxies — no signed URLs or public access. Defender for Storage scans block processing until clean.
- **Authentication:** Entra JWT tokens are validated for signature (RSA), issuer, audience, expiry, tenant, and HR role claims. Dev auth is disabled by default in production.
- **Idempotency:** Finalization uses atomic transactions with `Idempotency-Key` and `(tenant_id, pan_fingerprint)` unique constraints to prevent duplicate employee records.

---

## 📊 Project Status

| Metric | Value |
|---|---|
| **Issues Completed** | 12/12 (Issues #1–8, #10–13) |
| **Automated Tests** | 100+ passing |
| **CI Status** | All checks green (Ruff clean, Pytest passing) |
| **Agent Version** | v9 (`Azure Onboardly` / `employee-onboarding`) |
| **Live Evaluations** | 8/8 synthetic scenarios passed |
| **OCR Verification** | PDF and PNG fixtures — all 5 fields extracted |
| **Branch** | `feat/azure-foundry-onboarding` |

### Current Boundaries

- Extraction jobs run in-process and require manual retry after a restart.
- Chat is supported with one application worker (advisory lock serialization).
- OCR maps PAN/Aadhaar/email candidates; other fields are entered by HR.
- Document download uses an authenticated proxy rather than signed URLs.
- See the [issue ledger](docs/ISSUES.md) for remaining work before production rollout.

---

## 👥 Contributors

- [Aryan Verma](https://github.com/Aryan-verma-ai)
- [Neelabh](https://github.com/neelabh16)
- [Nitin Kataria](https://github.com/NitinKataria)

---

## 📄 License

This project is part of the Azure AI Foundry employee onboarding demonstration. Credentials remain in ignored `.env` — never commit them.
