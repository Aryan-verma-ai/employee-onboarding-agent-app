# Microsoft Foundry Central Orchestration Architecture Migration

## Executive Summary
This document specifies the architectural refactor that elevates **Microsoft Foundry** to the central AI agent and orchestration layer for the Employee Onboarding Platform, while maintaining strict, deterministic backend enforcement for all security-critical operations, authentication, authorization, document validation, data persistence, and notifications.

---

## 1. Architectural Topology

### Target Architecture Overview

```
                      ┌─────────────────────────────────┐
                      │    HR Browser / Candidate UI    │
                      └────────────────┬────────────────┘
                                       │ MSAL 2.0 (PKCE)
                                       ▼
                      ┌─────────────────────────────────┐
                      │  Microsoft Entra ID / Token     │
                      │  (Roles, Scopes, Claims)        │
                      └────────────────┬────────────────┘
                                       │ Bearer JWT (RS256)
                                       ▼
                      ┌─────────────────────────────────┐
                      │      FastAPI Gateway API        │
                      │  - Token Signature Validation   │
                      │  - Tenant Isolation Enforced    │
                      │  - Rate Limiting & Audit Log    │
                      └────────────────┬────────────────┘
                                       │ Safe Status / PII Redaction
                                       ▼
            ┌────────────────────────────────────────────────────────┐
            │       Microsoft Foundry Agent Orchestration Layer      │
            │              (Azure AI Projects / GPT-4.1-mini)        │
            │                                                        │
            │  Central Agent Loop: Reason → Tool Selection → Execute │
            │                                                        │
            │  Available MCP & Function Tools:                       │
            │  ├── search_company_knowledge (RAG / Policies)         │
            │  ├── get_onboarding_status                             │
            │  ├── get_extracted_data                                │
            │  ├── inspect_uploaded_documents                        │
            │  ├── request_document_extraction                       │
            │  ├── validate_onboarding                               │
            │  ├── prepare_hr_confirmation                           │
            │  ├── finalize_employee_onboarding                      │
            │  ├── get_employee_profile                              │
            │  └── send_onboarding_welcome_email                     │
            └────────────────┬──────────────────────▲────────────────┘
                             │ Tool Call            │ Structured Output
                             │ (JSON-RPC)           │ (Masked PII)
                             ▼                      │
            ┌────────────────────────────────────────────────────────┐
            │          Deterministic Backend Security Layer          │
            │                                                        │
            │  - HR Identity Enforcement (is_hr)                     │
            │  - Deterministic PAN/Aadhaar/Phone Validation          │
            │  - Active Employee Consent Verification                │
            │  - Conflict & Malware Detection Checks                 │
            │  - Atomic Employee ID Generation                       │
            │  - Automatic Audit Event Recording                     │
            └────────────────┬───────────────────────────────────────┘
                             │
            ┌────────────────┴───────────────────────────────────────┐
            │                 Azure Managed Services                 │
            │                                                        │
            │  ├── Azure PostgreSQL (Row-Level Security, ACID)      │
            │  ├── Azure Blob Storage (Encrypted at rest, private)   │
            │  ├── Azure Document Intelligence (Prebuilt Layout/ID)  │
            │  └── Azure AI Search / Knowledge Vector Store          │
            └────────────────────────────────────────────────────────┘
```

---

## 2. Core Architectural Principle: Zero LLM Trust for Security

> [!IMPORTANT]
> **The LLM is NEVER trusted to enforce security boundaries.**
> 
> Microsoft Foundry decides **WHEN** an action or business capability is needed in the workflow. The backend deterministically decides **WHETHER** that action is permitted.

Security-sensitive checks implemented strictly in deterministic code:
- **Authentication & Roles**: Microsoft Entra ID signature verification and HR claims validation.
- **Data Validation**: Exact regex format checks for PAN (`^[A-Z]{5}[0-9]{4}[A-Z]$`), Aadhaar (`^\d{12}$`), phone (`^[6-9]\d{9}$`), and email.
- **Conflict Detection**: Multiple conflicting document candidate values automatically block progression.
- **Idempotency**: All finalization actions require an idempotency key to prevent double employee record creation.
- **Database Safety**: Zero raw SQL or table queries exposed; only bounded business functions.
- **PII Protection**: Raw PAN and Aadhaar identity numbers are masked before tool outputs return to the agent (`AB*****4F`, `********1098`).

---

## 3. Allocation of Responsibilities

| Capability | Previous Implementation | New Foundry Architecture | Security Enforcement Location |
|---|---|---|---|
| **Conversation Flow** | Ad-hoc Python prompt | Microsoft Foundry Agent (`gpt-4.1-mini`) | Agent instructions + Guardrails |
| **Policy Search / Q&A** | Static dict lookup (`app/knowledge.py`) | Grounded Policy RAG (`search_company_knowledge`) + Foundry IQ | Deterministic fallback message |
| **Status Check** | Direct DB read | Foundry tool `get_onboarding_status` | Service projection (PII redacted) |
| **Document Inspection** | Backend route | Foundry tool `inspect_uploaded_documents` | Blob scan status & DB query |
| **OCR Extraction Queue** | Route-level trigger | Foundry tool `request_document_extraction` | Background job worker |
| **Case Validation** | Route-level trigger | Foundry tool `validate_onboarding` | Backend deterministic validator |
| **HR Confirmation** | UI check | Foundry tool `prepare_hr_confirmation` | Service readiness evaluator |
| **Employee Finalization** | Route-level trigger | Foundry tool `finalize_employee_onboarding` | Deterministic `is_hr` check & DB transaction |
| **Profile Display** | Route-level trigger | Foundry tool `get_employee_profile` | PII masking layer (`mask_pii`) |
| **Welcome Email** | Route-level trigger | Foundry tool `send_onboarding_welcome_email` | Candidate email verification check |

---

## 4. Foundry Tool Suite & Schemas

The agent interacts with the backend using the Model Context Protocol (MCP) and Azure OpenAI Function Calling formats:

### 1. `search_company_knowledge`
- **Purpose**: Grounded retrieval over the 10 corporate knowledge base documents.
- **Parameters**: `{"query": {"type": "string", "description": "Specific query regarding company policies, leaves, benefits, or onboarding."}}`
- **Fallback**: If no document matches, returns `"That information is not available in the company knowledge base. Please contact HR."`

### 2. `get_onboarding_status`
- **Purpose**: Retrieves authorized case status, missing required fields, and escalation flags.
- **Parameters**: `{}`
- **Security**: Projected view; raw identity numbers and candidates never reach the model.

### 3. `get_extracted_data`
- **Purpose**: Inspects OCR-extracted candidate fields, document processing states, and photo extraction state.
- **Parameters**: `{}`
- **Security**: Identity numbers automatically masked (`mask_pii`).

### 4. `inspect_uploaded_documents`
- **Purpose**: Lists document metadata, versions, malware scanning states, and extraction statuses.
- **Parameters**: `{}`
- **Security**: Verifies case ownership before listing files.

### 5. `request_document_extraction`
- **Purpose**: Queues background OCR processing for uploaded files.
- **Parameters**: `{"document_id": {"type": "string", "description": "Optional specific document ID."}}`
- **Security**: Asynchronous queueing; prevents worker exhaustion.

### 6. `validate_onboarding`
- **Purpose**: Deterministically executes validation rules across candidate details, identity proofs, and consent.
- **Parameters**: `{}`
- **Security**: Executes `validate_record()`; updates case status in database.

### 7. `prepare_hr_confirmation`
- **Purpose**: Verifies whether all criteria are satisfied to present the finalization action to HR.
- **Parameters**: `{}`
- **Security**: Read-only check; never creates an employee.

### 8. `finalize_employee_onboarding`
- **Purpose**: Commits the new employee record into the database, assigns official Employee ID, sets start date, and sends the welcome email.
- **Parameters**:
  - `hr_confirmed` (boolean, required): Explicit acknowledgment from HR.
  - `idempotency_key` (string, optional): Unique key for idempotency.
- **Security**:
  - Requires `service.principal.is_hr == True`.
  - Requires `case.status in ("validated", "created")`.
  - Re-evaluates validation errors in-transaction.
  - Rejects if consent is missing or withdrawn.

### 9. `get_employee_profile`
- **Purpose**: Displays the complete validated profile.
- **Parameters**: `{}`
- **Security**: PII masked.

### 10. `send_onboarding_welcome_email`
- **Purpose**: Dispatches official onboarding email with Employee ID and orientation details.
- **Parameters**: `{"email": {"type": "string", "description": "Optional override email."}}`
- **Security**: Rejects arbitrary emails from non-HR principals; credentials remain private to server.

---

## 5. Company Knowledge Base & Grounded RAG

Ten enterprise policy documents have been created in `company_knowledge/`:

1. `employee_handbook.md`: Mission, culture, equal opportunity, probation period (3 months), communication channels.
2. `leave_policy.md`: 18 days Annual/Privilege Leave, 12 days Sick/Casual, 26 weeks Maternity, 10 days Paternity, 5 days Bereavement.
3. `holiday_policy.md`: 10 annual public and national holidays with guidelines.
4. `benefits_policy.md`: ₹5,00,000 family health insurance cover, EPFO Provident Fund, Gratuity, Wellness allowances.
5. `payroll_information.md`: Last business day salary credit, TDS compliance, Form 16, reimbursement submission cutoff (20th).
6. `working_hours_policy.md`: 9:00 AM – 6:00 PM IST (Mon–Fri), hybrid working policy (3 days office, 2 days remote).
7. `it_security_policy.md`: BitLocker/FileVault encryption, 12-character MFA passwords, VPN requirements, strict phishing reporting.
8. `code_of_conduct.md`: Equal opportunity, POSH Act 2013 compliance, strict prohibition on moonlighting, ₹2,000 gift limit.
9. `department_information.md`: Engineering, Human Resources, Finance & Accounts, Sales & Partnerships, Operations & Facilities.
10. `onboarding_guidelines.md`: Document checklist (PAN or Aadhaar, Resume, Photograph, Bank proof), 3-day completion timeline.

### Grounding & Guardrail Behavior:
- Exact keyword and semantic scoring over indexed policy files.
- Off-topic queries (weather, history, geography, trivia) are refused cleanly.
- If policy information is absent, the exact standardized fallback is returned:
  > *"That information is not available in the company knowledge base. Please contact HR."*

---

## 6. Authentication & Authorization Architecture

### Microsoft Entra ID Authentication
- **Protocol**: MSAL 2.0 with OAuth 2.0 PKCE.
- **Token Format**: RS256 signed JWT tokens issued by `https://login.microsoftonline.com/{tenant}/v2.0`.
- **Validation**: Verified against Microsoft keys fetched via `PyJWKClient`.

### Authorization Fix (Over-broad HR role resolved)
- Previously, `app/auth.py` assigned HR roles unconditionally in certain flows.
- **Refactored Logic**:
  ```python
  token_roles = set(claims.get("roles") or [])
  scp = claims.get("scp", "")
  if isinstance(scp, str) and ("Onboarding.HR" in scp or "HR" in scp.split()):
      token_roles.add("HR")
  email = claims.get("preferred_username") or claims.get("upn") or claims.get("email") or ""
  hr_emails = {e.strip().lower() for e in os.getenv("ENTRA_HR_EMAILS", "").split(",") if e.strip()}
  if email and email.lower() in hr_emails:
      token_roles.add("HR")
  roles = frozenset(token_roles)
  ```
- Result: Regular candidate tokens evaluate to `is_hr == False`. Only authenticated HR users can access HR queues or call `finalize_employee_onboarding`.

---

## 7. Model Context Protocol (MCP) Interface

The FastAPI server provides authenticated MCP-compliant endpoints:
- `GET /api/mcp/tools`: Lists all 10 tools with JSON schema definitions.
- `POST /api/cases/{case_id}/mcp`: JSON-RPC 2.0 endpoint for tool invocation:
  ```json
  {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "finalize_employee_onboarding",
      "arguments": {
        "hr_confirmed": true
      }
    },
    "id": 1
  }
  ```

---

## 8. Verification & Test Suite

The test suite contains **115 automated tests** covering all security constraints:
- `tests/test_foundry_refactor.py`: 13 specialized proof tests verifying:
  1. Non-HR cannot finalize employees via tool.
  2. Unvalidated cases cannot be finalized via tool.
  3. Cross-tenant isolation blocks unauthorized access.
  4. Raw PAN/Aadhaar identity numbers are masked.
  5. Arbitrary email recipients are rejected.
  6. Non-existent policies return strict fallback message.
  7. Grounded policy retrieval returns accurate knowledge.
  8. Conflicting documents prevent finalization.
  9. Failed validation blocks employee creation.
  10. Authenticated HR confirmation allows finalization and assigns Employee ID.
  11. Welcome emails are restricted to verified candidate email.
  12. Zero SQL or raw query tools exist in the tool registry.
  13. Token without HR role correctly evaluates to `is_hr == False`.
- `tests/test_foundry.py`: 24 tests verifying tool loop bounds, redaction, consent withdrawal, and gateway integration.
- `tests/test_api.py`, `tests/test_sso.py`, `tests/test_validation.py`: 78 existing regression tests passing with 100% green checks.

---

## 9. Rollback & Disaster Recovery Strategy

1. **Agent Version Pinning**:
   - The Container App deployment explicitly specifies `FOUNDRY_AGENT_VERSION="9"`.
   - Creating a new version in Azure AI Foundry does not disrupt running containers until `FOUNDRY_AGENT_VERSION` is incremented.
2. **Backward Compatibility**:
   - `app/knowledge.py` re-exports `COMPANY_KNOWLEDGE` and `search_knowledge` from `app.knowledge_base`.
   - Existing REST endpoints (`/api/cases/{id}/validate`, `/api/cases/{id}/finalize`, `/api/cases/{id}/chat`) maintain 100% backward compatibility with `dashboard.html`.
3. **Rollback Execution**:
   - In the event of an issue, reverting the image tag or container app revision reverts all behavior in under 60 seconds without data loss.
