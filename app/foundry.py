"""Managed Azure AI Foundry agent invocation and restricted tool execution."""

import json
import os
import re
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_principal
from app.chat_lock import ChatBusyError, conversation_lock
from app.config import settings
from app.db import get_db
from app.knowledge import is_in_domain_query, search_company_knowledge
from app.service import OnboardingService


@contextmanager
def telemetry_span(name, attributes=None):
    """Opt-in manual spans; never record prompts, tool arguments or exceptions."""
    if os.getenv("ONBOARDING_OTEL_ENABLED", "false").lower() != "true":
        yield None
        return
    from opentelemetry import trace

    with trace.get_tracer("onboarding.foundry").start_as_current_span(
        name,
        attributes=attributes or {},
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception:
            span.set_attribute("onboarding.failed", True)
            raise


INSTRUCTIONS = """You are the Employee Onboarding & Company Policy Orchestrator powered by Azure AI Foundry.

CRITICAL DOMAIN RESTRICTIONS & REFUSAL POLICY:
You are an enterprise assistant authorized ONLY to discuss:
1. Employee Onboarding: Document uploads, document verification, missing fields, validation, and profile creation.
2. Company Data & Policies: Employee handbook, leave policy, work hours, holidays, payroll, health insurance, benefits, IT security, code of conduct, and department descriptions.

FOR ANY GENERAL KNOWLEDGE, GEOGRAPHY, TRIVIA, OR OFF-TOPIC QUESTIONS:
You MUST POLITELY REFUSE. You must NEVER answer questions about general geography (e.g., "Where is Kolkata"), world history, weather, celebrities, sports, trivia, general math, or any topics unrelated to company onboarding.
Always respond to off-topic questions with this refusal:
"I am an onboarding assistant specifically dedicated to company policies and employee onboarding. I can only answer questions related to your onboarding process, required documents, and company guidelines. How can I assist you with your onboarding or company information today?"

RAG & COMPANY KNOWLEDGE RETRIEVAL:
- Whenever a user asks about company policies, leave entitlements, holidays, working hours, benefits, salary schedule, IT rules, or department structures, ALWAYS use the `search_company_knowledge` tool with their question as the query.
- Ground your answers strictly on the retrieved company knowledge. Do not invent policies. If no policy is found, state that the information is not present in the company handbook and suggest contacting HR.

ONBOARDING ORCHESTRATION & DOCUMENT EXTRACTION:
- Guide users through the complete onboarding process.
- Direct users to drag and drop documents (PAN card, Aadhaar card, resume, photograph) directly into the chat area.
- Our intelligent OCR and Document Intelligence system automatically extracts:
  * From Resumes: Full Name, Email, Phone number.
  * From PAN Cards: PAN number, Full Name, Date of Birth.
  * From Aadhaar Cards: Aadhaar number, Full Name, Residential Address, Date of Birth.
  * From Photographs: Profile picture for company ID.
- Whenever documents are uploaded or extraction completes, ALWAYS call `get_extracted_data` to inspect the latest populated fields and document statuses.
- Report all extracted findings clearly to the user (e.g. "I've extracted your Name: X, Email: Y, Phone: Z, Address: W from your uploaded documents").
- Inform the user which documents or fields are still required to complete their profile.
- Use `get_onboarding_status` to check overall workflow state and missing fields.
- Use `validate_onboarding` to validate the case and finalize employee creation.
- When `validate_onboarding` is executed:
  * The system validates the case, generates a new unique Employee ID (e.g. EMP-XXXXXX), assigns an official start working date, and dispatches the official onboarding congratulations email to the person's email address.
  * ALWAYS celebrate the milestone enthusiastically! Address the employee directly by their full name (e.g., '🎉 Welcome aboard, [Name]!').
  * Clearly display their new Employee ID and assigned start working date.
  * Explicitly confirm that their official onboarding congratulations email has been sent to their email address with their Employee ID, start date, and first-day instructions!
  * Inform them that their official employee profile is active, and they can view the full profile and download their updated Excel record on the dashboard.
- You can also execute the `send_onboarding_welcome_email` tool to send or re-send the congratulations email to the candidate's email address.
- Never repeat raw PAN or Aadhaar numbers in chat.
"""

TOOL_SCHEMAS = {
    "get_onboarding_status": {
        "description": "Read the current authorized onboarding case status and missing field names.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    "validate_onboarding": {
        "description": "Run deterministic validation on the onboarding case, generate Employee ID, assign start date, create official employee profile, and send welcome email.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    "get_extracted_data": {
        "description": "Retrieve all OCR-extracted employee data and document statuses for the current case.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    "send_onboarding_welcome_email": {
        "description": "Send or re-send the official onboarding congratulations and welcome email to the candidate's email address with their Employee ID, start date, and onboarding instructions.",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Recipient email address. Pass empty string \"\" to use candidate's registered email from onboarding documents."
                }
            },
            "required": ["email"],
            "additionalProperties": False,
        },
    },
    "search_company_knowledge": {
        "description": "Search company policies, employee handbook, benefits, leaves, work hours, IT guidelines, and onboarding rules.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Specific query regarding company policies, leaves, benefits, work hours, or onboarding procedures."
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
TOOL_DESCRIPTIONS = {k: v["description"] for k, v in TOOL_SCHEMAS.items()}


def tool_definitions():
    from azure.ai.projects.models import FunctionTool

    return [
        FunctionTool(
            name=name,
            description=info["description"],
            strict=True,
            parameters=info["parameters"],
        )
        for name, info in TOOL_SCHEMAS.items()
    ]


def redact_identity(text: str) -> str:
    """Defense in depth for accidental identity numbers entered into chat."""
    text = re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", "[PAN redacted]", text, flags=re.I)
    return re.sub(r"(?<!\d)(?:\d[ -]?){11}\d(?!\d)", "[identity number redacted]", text)


def safe_status(case: Any) -> dict:
    # Explicit projection: raw identity numbers and OCR candidates never reach tools.
    def field(name, default=None):
        return case.get(name, default) if isinstance(case, dict) else getattr(case, name, default)

    problems = field("missing_fields", []) or []
    conflicts = [item.partition(":")[2] for item in problems if item.startswith("conflict:")]
    extraction = [item.partition(":")[2] for item in problems if item.startswith("extraction:")]
    escalation = bool(conflicts or extraction or field("status") == "failed")
    if conflicts:
        next_action = "HR must review and reconcile conflicting document evidence before validation."
    elif extraction or field("status") == "failed":
        next_action = (
            "HR must review the processing failure; retry document processing or upload a clearer document."
        )
    elif field("status") == "created":
        next_action = "Onboarding complete! Congratulate the employee by name, share their generated Employee ID and start date."
    else:
        next_action = None

    data = field("data", {}) or {}
    email_sent_to = data.get("welcome_email_sent_to") or (data.get("email") if field("status") == "created" else None)
    return {
        "status": field("status"),
        "missing_fields": [item for item in problems if not item.startswith(("conflict:", "extraction:"))],
        "employee_created": field("status") == "created",
        "employee_id": field("employee_id"),
        "employee_name": data.get("full_name"),
        "start_date": data.get("start_date"),
        "department": field("department"),
        "email": data.get("email"),
        "welcome_email_sent_to": email_sent_to,
        "conflicting_fields": conflicts,
        "failed_extractions": extraction,
        "escalation_required": escalation,
        "next_action": next_action,
    }


def grounded_message(message, status):
    """Workflow failures cannot be paraphrased away by a model response."""
    if not status["escalation_required"]:
        return redact_identity(message)
    if status["conflicting_fields"]:
        # Only schema field names are projected; never reflect document values.
        labels = [
            name.replace("_", " ")
            for name in status["conflicting_fields"]
            if re.fullmatch(r"[a-z_]{1,50}", name)
        ]
        subject = ", ".join(labels) or "employee details"
        return f"Conflicting evidence for {subject} requires HR review. Reconcile the documents before validating or creating the employee."
    return "Document processing failed and requires HR review. Retry processing or upload a clearer document before continuing; the employee has not been created by this action."


def validated_project_target(endpoint):
    target = urlsplit(endpoint)
    if (
        target.scheme != "https"
        or not target.hostname
        or not target.hostname.endswith(".services.ai.azure.com")
        or target.port not in (None, 443)
        or target.username
        or target.password
        or target.query
        or target.fragment
        or not re.fullmatch(r"/api/projects/[A-Za-z0-9_.-]+/?", target.path)
    ):
        raise ValueError("API-key mode requires a standard HTTPS Azure Foundry project endpoint")
    return target


def project_key_client(endpoint, key):
    """Resource-key transport scoped strictly to one Azure Foundry project."""
    import httpx
    from openai import OpenAI

    target = validated_project_target(endpoint)
    base_url = endpoint.rstrip("/") + "/openai/v1/"

    def enforce_target(request):
        if (
            request.url.scheme != "https"
            or request.url.host != target.hostname
            or request.url.port not in (None, 443)
            or not request.url.path.startswith(target.path.rstrip("/") + "/openai/v1/")
        ):
            raise ValueError("Refusing to send resource key outside configured Foundry project")
        # Apply at transport boundary: OpenAI SDK versions differ in auth hooks.
        request.headers.pop("authorization", None)
        request.headers["api-key"] = key

    return OpenAI(
        api_key=key,
        base_url=base_url,
        timeout=45.0,
        max_retries=1,
        http_client=httpx.Client(follow_redirects=False, event_hooks={"request": [enforce_target]}),
    )


class FoundryGateway:
    def __init__(self, endpoint: str, agent_name: str, agent_version: str, client=None, credential=None):
        if not all((endpoint, agent_name, agent_version)):
            raise ValueError(
                "Configure FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_AGENT_NAME and FOUNDRY_AGENT_VERSION"
            )
        self.reference = {"type": "agent_reference", "name": agent_name, "version": agent_version}
        self.endpoint = endpoint
        self.project = None
        self.credential = None
        self._owns_credential = credential is None
        if client is None and os.getenv("FOUNDRY_AUTH_MODE", "entra") == "api_key":
            client = project_key_client(endpoint, os.environ["AZURE_OPENAI_API_KEY"])
        if client is None:
            from azure.ai.projects import AIProjectClient

            from app.azure_auth import azure_credential

            self.credential = credential or azure_credential()
            self.project = AIProjectClient(endpoint=endpoint, credential=self.credential)
            client = self.project.get_openai_client(timeout=45.0, max_retries=1)
        self.client = client

    def agent_metadata(self):
        """Verify the exact managed version for either supported authentication mode."""
        if self.project is not None:
            return self.project.agents.get_version(
                agent_name=self.reference["name"], agent_version=self.reference["version"]
            ).as_dict()
        if os.getenv("FOUNDRY_AUTH_MODE", "entra") != "api_key":
            raise ValueError("Agent metadata needs an authenticated project")
        import httpx

        validated_project_target(self.endpoint)
        name = quote(self.reference["name"], safe="")
        version = quote(self.reference["version"], safe="")
        with httpx.Client(follow_redirects=False, timeout=30.0) as client:
            response = client.get(
                self.endpoint.rstrip("/") + f"/agents/{name}/versions/{version}",
                params={"api-version": "v1"},
                headers={"api-key": os.environ["AZURE_OPENAI_API_KEY"]},
            )
            response.raise_for_status()
            return response.json()

    def close(self):
        self.client.close()
        if self.project:
            self.project.close()
        if self.credential and self._owns_credential:
            self.credential.close()

    def chat(self, conversation_id, message, service, case_id, on_conversation=None, on_model_response=None):
        case = service.get_case(case_id)  # Authorization happens before any cloud call.
        service.require_consent(case)
        if not conversation_id:
            conversation_id = self.client.conversations.create().id
            if on_conversation:
                on_conversation(conversation_id)
        inputs: Any = redact_identity(message)
        tool_count = 0
        for _ in range(8):
            # Tool execution or conversation persistence may have committed the lock.
            service.require_consent(service.get_case(case_id))
            with telemetry_span("foundry.response", {"agent.version": self.reference["version"]}) as span:
                response = self.client.responses.create(
                    conversation=conversation_id, input=inputs, extra_body={"agent_reference": self.reference}
                )
                if span:
                    span.set_attribute("foundry.response_id", response.id)
            service.audit(
                case_id,
                "foundry.response",
                {"response_id": response.id, "conversation_id": conversation_id, "agent": self.reference},
            )
            calls = [item for item in response.output if item.type == "function_call"]
            if getattr(response, "status", "completed") in {"failed", "incomplete", "cancelled"}:
                raise RuntimeError("Foundry response did not complete")
            tool_count += len(calls)
            if tool_count > 32:
                raise RuntimeError("Foundry tool call limit exceeded")
            if not calls:
                if not response.output_text:
                    raise RuntimeError("Foundry returned no assistant response")
                if on_model_response:
                    on_model_response(redact_identity(response.output_text))
                status = safe_status(service.get_case(case_id))
                final_message = grounded_message(response.output_text, status)
                if status["escalation_required"]:
                    service.audit(case_id, "foundry.escalation_enforced", {"status": status["status"]})
                return {
                    "message": final_message,
                    "escalation_enforced": status["escalation_required"],
                    "conversation_id": conversation_id,
                    "response_id": response.id,
                }
            inputs = []
            for call in calls:
                try:
                    arguments = json.loads(call.arguments)
                    if call.name not in TOOL_SCHEMAS:
                        raise ValueError("Tool not permitted")
                    if call.name == "validate_onboarding":
                        service.validate_case(case_id)
                        case = service.get_case(case_id)
                        case_data = case.data if hasattr(case, "data") else (case.get("data", {}) if isinstance(case, dict) else {})
                        emp_id = getattr(case, "employee_id", None) or (case.get("employee_id") if isinstance(case, dict) else None)
                        dept = getattr(case, "department", None) or (case.get("department") if isinstance(case, dict) else None)
                        from .notifications import send_welcome_email
                        email_res = send_welcome_email(case_data, emp_id, dept)
                        service.audit(case_id, "foundry.welcome_email", email_res)
                        result = {
                            **safe_status(case),
                            "welcome_email": email_res,
                        }
                    elif call.name == "send_onboarding_welcome_email":
                        case = service.get_case(case_id)
                        case_data = case.data if hasattr(case, "data") else (case.get("data", {}) if isinstance(case, dict) else {})
                        emp_id = getattr(case, "employee_id", None) or (case.get("employee_id") if isinstance(case, dict) else None)
                        dept = getattr(case, "department", None) or (case.get("department") if isinstance(case, dict) else None)
                        recipient = arguments.get("email") if isinstance(arguments, dict) else None
                        from .notifications import send_welcome_email
                        email_res = send_welcome_email(
                            case_data=case_data,
                            employee_id=emp_id or "PENDING",
                            department=dept,
                            recipient_override=recipient
                        )
                        service.audit(case_id, "foundry.welcome_email", email_res)
                        result = {
                            "status": email_res.get("status"),
                            "recipient": email_res.get("recipient"),
                            "subject": email_res.get("subject"),
                            "message": email_res.get("preview"),
                        }
                    elif call.name == "get_extracted_data":
                        result = service.get_extracted_data(case_id)
                    elif call.name == "get_onboarding_status":
                        result = safe_status(service.get_case(case_id))
                    elif call.name == "search_company_knowledge":
                        query = arguments.get("query", "") if isinstance(arguments, dict) else ""
                        result = search_company_knowledge(str(query))
                    else:
                        result = safe_status(service.get_case(case_id))
                    service.audit(case_id, "foundry.tool", {"tool": call.name, "call_id": call.call_id})
                except (ValueError, json.JSONDecodeError):
                    service.audit(case_id, "foundry.tool_rejected", {"reason": "invalid-tool-or-arguments"})
                    result = {"error": "Tool or arguments not permitted"}
                inputs.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result)}
                )
        raise RuntimeError("Foundry tool iteration limit exceeded")


router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("/api/cases/{case_id}/chat")
def chat(case_id: str, body: ChatRequest, db=Depends(get_db), principal=Depends(get_principal)):
    OnboardingService(db, principal).get_case(case_id)
    # Return the authorization read connection before taking the dedicated lock
    # connection. This avoids pool starvation with many queued requests.
    db.rollback()
    try:
        with conversation_lock(
            db.get_bind(), principal.tenant_id, case_id, development=settings.environment == "development"
        ):
            db.expire_all()
            with telemetry_span("onboarding.chat"):
                return _chat(case_id, body, db, principal)
    except ChatBusyError as exc:
        raise HTTPException(409, str(exc), headers={"Retry-After": "2"}) from exc


def _chat(case_id, body, db, principal):
    service = OnboardingService(db, principal)
    case = service.get_case(case_id)
    service.require_consent(case)

    # Deterministic domain guardrail for obvious off-topic general knowledge queries
    user_msg = body.message.strip()
    off_topic_pattern = r"(?i)\b(where is|who is|what is the capital|weather in|tell me a joke|write a poem|who was|who won|population of|distance between)\b"
    if not is_in_domain_query(user_msg) and re.search(off_topic_pattern, user_msg):
        refusal_msg = (
            "I am an onboarding assistant specifically dedicated to company policies and employee onboarding. "
            "I can only answer questions related to your onboarding process, required documents, and company guidelines. "
            "How can I assist you with your onboarding or company information today?"
        )
        service.audit(case_id, "foundry.off_topic_refusal", {"query": user_msg[:100]})
        db.commit()
        return {
            "message": refusal_msg,
            "escalation_enforced": False,
            "conversation_id": case.conversation_id or "",
            "response_id": "guardrail-refusal",
        }

    try:
        gateway = FoundryGateway(
            os.getenv("FOUNDRY_PROJECT_ENDPOINT", ""),
            os.getenv("FOUNDRY_AGENT_NAME", ""),
            os.getenv("FOUNDRY_AGENT_VERSION", ""),
        )
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc

    def save_conversation(conversation_id):
        case.conversation_id = conversation_id
        db.commit()

    try:
        result = gateway.chat(case.conversation_id, body.message, service, case_id, save_conversation)
        db.commit()
        return result
    except HTTPException:
        raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Foundry chat failed for case %s: %s", case_id, exc)
        service.audit(case_id, "foundry.failed", {"error_type": type(exc).__name__, "message": str(exc)[:300]})
        db.commit()
        raise HTTPException(502, "Foundry agent unavailable; please retry") from exc
    finally:
        gateway.close()
