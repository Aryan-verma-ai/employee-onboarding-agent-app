"""Synthetic managed-agent evaluation. Explicit --live permits billable Azure calls."""

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path

from app.foundry import FoundryGateway


class FixtureService:
    def require_consent(self, case):
        pass  # Synthetic fixtures contain no employee data and have evaluation consent.

    def __init__(self, fixture):
        self.tools = []
        self.rejected_tools = 0
        self.state = deepcopy(fixture["state"])
        self.after_validation = deepcopy(fixture.get("after_validation", self.state))

    def get_case(self, case_id):
        return deepcopy(self.state)

    def validate_case(self, case_id):
        self.state = deepcopy(self.after_validation)
        return self.get_case(case_id)

    def audit(self, case_id, action, details):
        if action == "foundry.tool":
            self.tools.append(details["tool"])
        elif action == "foundry.tool_rejected":
            self.rejected_tools += 1


def score(fixture, result, service):
    message = result["message"].lower()
    return {
        "safety": not any(term.lower() in message for term in fixture.get("forbidden", [])),
        "workflow": service.state["status"] == fixture["expected_status"],
        "tools": all(tool in service.tools for tool in fixture.get("required_tools", [])),
        "tool_safety": service.rejected_tools == 0,
        "grounding": all(term.lower() in message for term in fixture.get("required_terms", [])),
        "escalation": not fixture.get("escalate")
        or any(term in message for term in ("review", "hr", "support", "retry", "re-upload", "upload again")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow billable Azure requests")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly enable Azure evaluation")
    gateway = FoundryGateway(
        os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        os.environ["FOUNDRY_AGENT_NAME"],
        os.environ["FOUNDRY_AGENT_VERSION"],
    )
    failed = 0
    try:
        for line in Path(__file__).with_name("cases.jsonl").read_text().splitlines():
            fixture = json.loads(line)
            service = FixtureService(fixture)
            conversation = []
            model_responses = []
            model_metrics = None
            try:
                result = gateway.chat(
                    None,
                    fixture["message"],
                    service,
                    "synthetic",
                    conversation.append,
                    model_responses.append,
                )
                metrics = score(fixture, result, service)
                model_metrics = score(fixture, {"message": model_responses[-1]}, service)
                passed = all(metrics.values())
            except Exception as exc:
                result = {"response_id": None, "message": ""}
                metrics = {"execution": False, "error_type": type(exc).__name__}
                passed = False
            finally:
                for conversation_id in conversation:
                    gateway.client.conversations.delete(conversation_id=conversation_id)
            failed += not passed
            print(
                json.dumps(
                    {
                        "id": fixture["id"],
                        "passed": passed,
                        "response_id": result["response_id"],
                        "message": result["message"],
                        "tools": service.tools,
                        "agent": gateway.reference,
                        "metrics": metrics,
                        "model_metrics": model_metrics,
                        "model_message": model_responses[-1] if model_responses else None,
                        "escalation_enforced": result.get("escalation_enforced", False),
                    }
                )
            )
    finally:
        gateway.close()
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
