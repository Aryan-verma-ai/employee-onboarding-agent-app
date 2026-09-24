"""Model Context Protocol (MCP) Tool Server for Microsoft Foundry Agent.

Exposes controlled, authenticated business capabilities to the Microsoft Foundry Agent:
  - Knowledge: Company Knowledge Base retrieval (Foundry IQ / Grounded Policy RAG)
  - Onboarding: Status, OCR extraction inspect/request, document workflow
  - Validation: Deterministic validation, HR confirmation readiness
  - Employee: Finalization with deterministic HR authorization, profile readiness
  - Communication: Onboarding welcome email dispatch

SECURITY GUARANTEES:
  1. The LLM NEVER enforces security, validation, duplicate checks, or HR authorization.
  2. Tools decide WHETHER an operation is permitted; Foundry decides WHEN to call a tool.
  3. No arbitrary SQL or raw database query execution is exposed.
  4. Identity numbers (PAN/Aadhaar) are masked before returning to the model.
  5. Cross-tenant isolation is enforced on every operation via PostgreSQL RLS and Principal.
  6. All tool executions are audited automatically by the backend.
"""

import json
import logging
import re
import secrets
from typing import Any, Callable, Optional

from .knowledge_base import search_company_knowledge

log = logging.getLogger(__name__)

# Mask raw PAN and Aadhaar identity numbers from all tool responses
def mask_pii(data: Any) -> Any:
    if isinstance(data, dict):
        masked = {}
        for k, v in data.items():
            if k in ("pan", "aadhaar") and isinstance(v, str) and v:
                if k == "pan":
                    masked[k] = f"{v[:2]}*****{v[-2:]}" if len(v) >= 4 else "[REDACTED]"
                else:
                    masked[k] = f"********{v[-4:]}" if len(v) >= 4 else "[REDACTED]"
            else:
                masked[k] = mask_pii(v)
        return masked
    if isinstance(data, list):
        return [mask_pii(item) for item in data]
    if isinstance(data, str):
        # PAN mask
        data = re.sub(r"\b([A-Z]{2})[A-Z]{3}[0-9]{4}([A-Z])\b", r"\1*****\2", data, flags=re.I)
        # Aadhaar mask
        data = re.sub(r"\b(?:\d[ -]?){8}(\d{4})\b", r"********\1", data)
    return data


# ── MCP Tool Definitions ──

MCP_TOOLS = {
    "get_onboarding_status": {
        "name": "get_onboarding_status",
        "description": "Read the current authorized onboarding case status, missing required fields, and workflow state.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    "get_extracted_data": {
        "name": "get_extracted_data",
        "description": "Retrieve all OCR-extracted employee data (Name, Email, Phone, Address, DOB, ID proof status, photo status) and document statuses for the current case.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    "inspect_uploaded_documents": {
        "name": "inspect_uploaded_documents",
        "description": "Inspect metadata, versions, malware scan status, and extraction status for documents uploaded to the current case.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    "request_document_extraction": {
        "name": "request_document_extraction",
        "description": "Queue real backend OCR extraction work for incomplete documents in the current case. Extraction remains asynchronous.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "Optional specific document ID to extract. If omitted, all incomplete documents are queued.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "validate_onboarding": {
        "name": "validate_onboarding",
        "description": "Run deterministic backend validation on the current case. Validates required fields, identity proof, consent, duplicate records, and conflicts. Returns structured validation outcomes.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    "prepare_hr_confirmation": {
        "name": "prepare_hr_confirmation",
        "description": "Check whether the onboarding case has passed validation and is eligible for HR finalization approval. Never creates an employee.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    "finalize_employee_onboarding": {
        "name": "finalize_employee_onboarding",
        "description": "Finalize employee onboarding and create the official employee record. Enforces authenticated HR identity, validation success, consent, duplicate constraints, and idempotency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hr_confirmed": {
                    "type": "boolean",
                    "description": "Explicit HR confirmation acknowledging that documents and data have been verified.",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Unique key to ensure idempotent execution. Pass empty string to auto-generate.",
                },
            },
            "required": ["hr_confirmed"],
            "additionalProperties": False,
        },
    },
    "get_employee_profile": {
        "name": "get_employee_profile",
        "description": "Build an authorized, non-PII employee profile view from the authoritative case state.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    "send_onboarding_welcome_email": {
        "name": "send_onboarding_welcome_email",
        "description": "Send or re-send the official onboarding welcome and congratulations email to the candidate's verified email address with their Employee ID, start date, and onboarding instructions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Optional recipient email address. If empty string \"\", candidate's registered onboarding email is used.",
                }
            },
            "required": ["email"],
            "additionalProperties": False,
        },
    },
    "search_company_knowledge": {
        "name": "search_company_knowledge",
        "description": "Search company policies, employee handbook, benefits, leaves, holidays, work hours, IT guidelines, code of conduct, and onboarding rules from the company knowledge base.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Specific query regarding company policies, leaves, benefits, work hours, or onboarding procedures.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


# ── MCP Tool Handlers (Deterministic Execution) ──

def handle_get_onboarding_status(service, case_id: str, args: dict) -> dict:
    from .foundry import safe_status
    case = service.get_case(case_id)
    return safe_status(case)


def handle_get_extracted_data(service, case_id: str, args: dict) -> dict:
    data = service.get_extracted_data(case_id)
    return mask_pii(data)


def handle_inspect_uploaded_documents(service, case_id: str, args: dict) -> dict:
    docs = service.document_workflow(case_id)
    return {"documents": docs, "total_count": len(docs)}


def handle_request_document_extraction(service, case_id: str, args: dict) -> dict:
    doc_id = args.get("document_id") or None
    return service.request_document_extraction(case_id, doc_id)


def handle_validate_onboarding(service, case_id: str, args: dict) -> dict:
    case = service.validate_case(case_id)
    from .foundry import safe_status
    status = safe_status(case)
    return {
        "validation_passed": case.status in ("validated", "created"),
        "status": case.status,
        "missing_fields": case.missing_fields or [],
        "validation_outcomes": case.validation_outcomes or [],
        "safe_status": status,
    }


def handle_prepare_hr_confirmation(service, case_id: str, args: dict) -> dict:
    return service.confirmation_readiness(case_id)


def handle_finalize_employee_onboarding(service, case_id: str, args: dict) -> dict:
    hr_confirmed = args.get("hr_confirmed")
    idempotency_key = args.get("idempotency_key") or f"mcp-{secrets.token_hex(8)}"

    # ── DETERMINISTIC SECURITY ENFORCEMENT ──
    # 1. Enforce Authenticated HR Role
    if not service.principal.is_hr:
        service.audit(case_id, "foundry.finalization_denied", {"reason": "hr_role_required"})
        return {
            "success": False,
            "error": "Authorization denied: Only authenticated HR personnel can finalize employee onboarding and create official records.",
            "is_hr": False,
        }

    # 2. Enforce Explicit HR Confirmation
    if hr_confirmed is not True:
        return {
            "success": False,
            "error": "Explicit HR confirmation is required to finalize employee creation.",
        }

    # 3. Enforce Case and Validation
    case = service.get_case(case_id)
    service.require_consent(case)
    errors = list(dict.fromkeys((service.validation_errors(case) or []) + (case.missing_fields or [])))
    if errors or case.status not in ("validated", "created"):
        error_msg = (
            f"Validation failed: missing or invalid requirements: {', '.join(errors)}"
            if errors
            else "Case must be validated before employee creation."
        )
        service.audit(case_id, "foundry.finalization_denied", {"reason": "validation_failed", "errors": errors})
        return {
            "success": False,
            "error": error_msg,
            "missing_fields": errors,
        }

    # 4. Perform Backend Finalization Transaction
    try:
        finalized_case = service.finalize_case(case_id, True, idempotency_key)
        # Automatically dispatch welcome email
        from .notifications import send_welcome_email
        email_res = send_welcome_email(
            case_data=finalized_case.data,
            employee_id=finalized_case.employee_id,
            department=finalized_case.department,
        )
        service.audit(case_id, "foundry.auto_welcome_email", email_res)

        return {
            "success": True,
            "employee_created": True,
            "employee_id": finalized_case.employee_id,
            "department": finalized_case.department,
            "start_date": (finalized_case.data or {}).get("start_date"),
            "full_name": (finalized_case.data or {}).get("full_name"),
            "email": (finalized_case.data or {}).get("email"),
            "welcome_email_status": email_res.get("status"),
            "status": finalized_case.status,
        }
    except Exception as exc:
        log.exception("Finalization failed for case %s: %s", case_id, exc)
        return {"success": False, "error": str(exc)}


def handle_get_employee_profile(service, case_id: str, args: dict) -> dict:
    profile = service.get_employee_profile(case_id)
    return mask_pii(profile)


def handle_send_onboarding_welcome_email(service, case_id: str, args: dict) -> dict:
    case = service.get_case(case_id)
    service.require_consent(case)
    case_data = case.data if hasattr(case, "data") else (case.get("data", {}) if isinstance(case, dict) else {})
    emp_id = getattr(case, "employee_id", None) or (case.get("employee_id") if isinstance(case, dict) else None)
    dept = getattr(case, "department", None) or (case.get("department") if isinstance(case, dict) else None)
    recipient = (args.get("email") or "").strip() or None

    # Recipient validation: must match candidate email or be verified
    registered_email = (case_data.get("email") or "").strip().lower()
    if recipient:
        recipient_clean = recipient.lower()
        if recipient_clean != registered_email and not service.principal.is_hr:
            return {
                "status": "rejected",
                "error": "Security violation: Welcome email can only be dispatched to the candidate's verified email address.",
            }

    from .notifications import send_welcome_email
    email_res = send_welcome_email(
        case_data=case_data,
        employee_id=emp_id or "PENDING",
        department=dept,
        override_recipient=recipient,
    )
    service.audit(case_id, "foundry.welcome_email", email_res)
    return {
        "status": email_res.get("status"),
        "recipient": email_res.get("recipient"),
        "subject": email_res.get("subject"),
        "preview": email_res.get("preview"),
    }


def handle_search_company_knowledge(service, case_id: str, args: dict) -> dict:
    query = args.get("query", "")
    return search_company_knowledge(str(query))


# ── Tool Registry Mapping ──

TOOL_HANDLERS: dict[str, Callable] = {
    "get_onboarding_status": handle_get_onboarding_status,
    "get_extracted_data": handle_get_extracted_data,
    "inspect_uploaded_documents": handle_inspect_uploaded_documents,
    "request_document_extraction": handle_request_document_extraction,
    "validate_onboarding": handle_validate_onboarding,
    "prepare_hr_confirmation": handle_prepare_hr_confirmation,
    "finalize_employee_onboarding": handle_finalize_employee_onboarding,
    "get_employee_profile": handle_get_employee_profile,
    "send_onboarding_welcome_email": handle_send_onboarding_welcome_email,
    "search_company_knowledge": handle_search_company_knowledge,
}


class MCPToolServer:
    """Model Context Protocol (MCP) server managing tool execution for Foundry."""

    def __init__(self, service, case_id: str):
        self.service = service
        self.case_id = case_id

    @classmethod
    def list_tools(cls) -> list[dict[str, Any]]:
        """Return MCP-compliant tool specifications."""
        return list(MCP_TOOLS.values())

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict[str, Any]:
        """Execute an MCP tool within the authenticated case and principal context."""
        if name not in TOOL_HANDLERS:
            self.service.audit(self.case_id, "mcp.tool_rejected", {"tool": name, "reason": "unknown_tool"})
            raise ValueError(f"Tool not permitted: {name}")

        args = arguments or {}
        if not isinstance(args, dict):
            self.service.audit(self.case_id, "mcp.tool_rejected", {"tool": name, "reason": "invalid_arguments"})
            raise ValueError(f"Arguments for tool '{name}' must be a JSON object")

        handler = TOOL_HANDLERS[name]
        try:
            result = handler(self.service, self.case_id, args)
            self.service.audit(self.case_id, "mcp.tool_executed", {"tool": name})
            return result
        except Exception as exc:
            log.exception("MCP tool execution error for '%s': %s", name, exc)
            self.service.audit(self.case_id, "mcp.tool_error", {"tool": name, "error": str(exc)})
            raise

    def handle_jsonrpc(self, request_payload: dict) -> dict:
        """Handle standard JSON-RPC 2.0 MCP protocol requests."""
        req_id = request_payload.get("id")
        method = request_payload.get("method")
        params = request_payload.get("params") or {}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self.list_tools()},
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments") or {}
            try:
                out = self.call_tool(tool_name, tool_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(out)}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                        "isError": True,
                    },
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
