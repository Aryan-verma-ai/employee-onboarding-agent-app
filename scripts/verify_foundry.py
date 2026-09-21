"""Verify the real Foundry version, tool execution, and persisted conversation reuse.

Uses synthetic local cases only. Requires Azure sign-in and Foundry environment.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.foundry import TOOL_DESCRIPTIONS, FoundryGateway
from app.models import AuditEvent, Base
from app.service import OnboardingService


def main(gateway=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="Output JSON verification report")
    parser.add_argument("--database", required=True, help="Fresh synthetic SQLite database path")
    args = parser.parse_args()
    database = Path(args.database).resolve()
    if database.exists():
        raise SystemExit("Use a fresh database path for each verification run")
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + database.as_posix())
    Base.metadata.create_all(engine)
    principal = Principal("synthetic-verification", "synthetic-verification-tenant", frozenset({"HR"}))
    gateway = gateway or FoundryGateway(
        os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        os.environ["FOUNDRY_AGENT_NAME"],
        os.environ["FOUNDRY_AGENT_VERSION"],
    )
    report = {
        "verified": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": gateway.reference,
        "synthetic_data_only": True,
        "responses": [],
    }
    conversation_id = None
    try:
        agent = gateway.agent_metadata()
        definition = agent["definition"]
        names = {tool.get("name") for tool in definition.get("tools", [])}
        if not set(TOOL_DESCRIPTIONS).issubset(names):
            raise RuntimeError("Registered agent is missing required onboarding tools")
        report["model_deployment"] = definition["model"]
        report["metadata_verified"] = True
        with Session(engine, expire_on_commit=False) as db:
            service = OnboardingService(db, principal)
            case = service.create_case("Engineering", True)
            case_id = case.id
            service.validate_case(case_id)

            def save_conversation(value):
                nonlocal conversation_id
                conversation_id = value
                case.conversation_id = value
                db.commit()

            first = gateway.chat(
                None,
                "Use get_onboarding_status to check my onboarding case and list what information is missing. Do not create an employee.",
                service,
                case_id,
                save_conversation,
            )
            db.commit()
            report["responses"].append(first)
        # A new DB session proves the application can recover the saved conversation.
        with Session(engine, expire_on_commit=False) as db:
            service = OnboardingService(db, principal)
            case = service.get_case(case_id)
            if case.conversation_id != conversation_id:
                raise RuntimeError("Conversation mapping was not persisted")
            second = gateway.chat(
                case.conversation_id,
                "Check get_onboarding_status again. Is anything still required before HR can approve?",
                service,
                case_id,
            )
            db.commit()
            report["responses"].append(second)
            events = db.scalars(
                select(AuditEvent).where(AuditEvent.case_id == case_id, AuditEvent.action == "foundry.tool")
            ).all()
            report["tool_calls"] = [event.details for event in events]
            if not events or not all(item["message"] and item["response_id"] for item in report["responses"]):
                raise RuntimeError("No verified tool call or assistant response")
            if case.status == "created":
                raise RuntimeError("Agent bypassed human approval")
            report["conversation_reused"] = second["conversation_id"] == conversation_id
            report["verified"] = report["conversation_reused"]
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        if conversation_id:
            try:
                gateway.client.conversations.delete(conversation_id=conversation_id)
                report["synthetic_conversation_deleted"] = True
            except Exception as exc:
                report["cleanup_error_type"] = type(exc).__name__
        gateway.close()
        engine.dispose()
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"verified": report["verified"], "report": str(path)}))


if __name__ == "__main__":
    main()
