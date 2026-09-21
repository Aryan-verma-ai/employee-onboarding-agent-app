"""Opt-in live Foundry smoke evaluation with synthetic, read-only tool fixtures.

Run: python -m evaluations.run. Calls Azure and consumes model tokens.
These deterministic checks supplement, rather than replace, human quality review.
"""

import json
import os
from pathlib import Path

from app.foundry import FoundryGateway


class FixtureService:
    def __init__(self):
        self.tools = []

    def get_case(self, case_id):
        return {"status": "needs-information", "missing_fields": ["email", "full_name"]}

    def validate_case(self, case_id):
        return self.get_case(case_id)

    def audit(self, case_id, action, details):
        if action == "foundry.tool":
            self.tools.append(details["tool"])


def main():
    gateway = FoundryGateway(
        os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        os.environ["FOUNDRY_AGENT_NAME"],
        os.environ["FOUNDRY_AGENT_VERSION"],
    )
    failed = 0
    try:
        for line in Path(__file__).with_name("cases.jsonl").read_text().splitlines():
            fixture = json.loads(line)
            service = FixtureService()
            result = gateway.chat(None, fixture["message"], service, "synthetic")
            passed = not any(term.lower() in result["message"].lower() for term in fixture["forbidden"])
            passed = passed and service.get_case("synthetic")["status"] == fixture["expected_status"]
            if fixture["id"] == "missing-fields":
                passed = (
                    passed
                    and "get_onboarding_status" in service.tools
                    and "email" in result["message"].lower()
                )
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
                    }
                )
            )
            gateway.client.conversations.delete(conversation_id=result["conversation_id"])
    finally:
        gateway.close()
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
