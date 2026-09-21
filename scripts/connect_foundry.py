"""Discover and verify Foundry using the current user's authenticated Azure CLI."""

import argparse
import json
import sys
from pathlib import Path
from time import time
from urllib.parse import urlparse

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import AzureCliCredential

from app.foundry import INSTRUCTIONS, FoundryGateway, tool_definitions
from scripts.verify_foundry import main as verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-endpoint")
    parser.add_argument("--model-deployment")
    parser.add_argument(
        "--agent-version", help="Verify an existing onboarding agent version instead of creating one"
    )
    parser.add_argument("--agent-name", default="employee-onboarding")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    credential = AzureCliCredential(subscription=args.subscription)
    inventory = []

    def arm(url):
        if not url.startswith("https://management.azure.com/"):
            raise ValueError("Unexpected management URL")
        response = requests.get(
            url,
            headers={
                "Authorization": "Bearer "
                + credential.get_token("https://management.azure.com/.default").token
            },
            timeout=45,
        )
        if response.status_code >= 400:
            error = response.json().get("error", {})
            raise RuntimeError(
                f"Azure {response.status_code}: {error.get('code', 'request failed')}: {error.get('message', '')}"
            )
        return response.json()

    def pages(url):
        rows = []
        while url:
            data = arm(url)
            rows.extend(data.get("value", []))
            url = data.get("nextLink")
        return rows

    try:
        if not args.project_endpoint:
            accounts = pages(
                f"https://management.azure.com/subscriptions/{args.subscription}/providers/Microsoft.CognitiveServices/accounts?api-version=2025-06-01"
            )
            choices = []
            for account in accounts:
                if account.get("kind") != "AIServices":
                    continue
                base = "https://management.azure.com" + account["id"]
                item = {
                    "account": account["name"],
                    "location": account.get("location"),
                    "projects": [],
                    "deployments": [],
                }
                for category in ("projects", "deployments"):
                    try:
                        item[category] = pages(base + "/" + category + "?api-version=2025-06-01")
                    except RuntimeError as exc:
                        item[category + "_error"] = str(exc)
                inventory.append(item)
                for project in item["projects"]:
                    endpoints = project.get("properties", {}).get("endpoints", {})
                    endpoint = next(
                        (
                            url
                            for url in endpoints.values()
                            if isinstance(url, str) and "/api/projects/" in url
                        ),
                        None,
                    )
                    if endpoint:
                        choices.append((endpoint, item["deployments"]))
            (output / "azure-inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
            if len(choices) != 1:
                raise RuntimeError(
                    f"Found {len(choices)} candidate projects. See azure-inventory.json and rerun with --project-endpoint and --model-deployment."
                )
            args.project_endpoint, deployments = choices[0]
            if not args.model_deployment and not args.agent_version:
                if len(deployments) != 1:
                    raise RuntimeError(
                        "Multiple or no model deployments found. See azure-inventory.json and rerun with --model-deployment."
                    )
                args.model_deployment = deployments[0]["name"]
        parsed = urlparse(args.project_endpoint)
        if (
            parsed.scheme != "https"
            or not (parsed.hostname or "").endswith(".services.ai.azure.com")
            or "/api/projects/" not in parsed.path
        ):
            raise ValueError("Expected a public Azure Foundry project endpoint")
        if not args.agent_version:
            if not args.model_deployment:
                raise ValueError("A model deployment is required to provision the agent")
            with AIProjectClient(endpoint=args.project_endpoint, credential=credential) as project:
                agent = project.agents.create_version(
                    agent_name=args.agent_name,
                    definition=PromptAgentDefinition(
                        model=args.model_deployment, instructions=INSTRUCTIONS, tools=tool_definitions()
                    ),
                )
                args.agent_version = str(agent.version)
                print(f"Created Foundry agent {agent.name}, version {agent.version}")
        connection = {
            "FOUNDRY_PROJECT_ENDPOINT": args.project_endpoint,
            "FOUNDRY_AGENT_NAME": args.agent_name,
            "FOUNDRY_AGENT_VERSION": args.agent_version,
        }
        (output / "foundry-connection.json").write_text(json.dumps(connection, indent=2), encoding="utf-8")
        gateway = FoundryGateway(
            args.project_endpoint, args.agent_name, args.agent_version, credential=credential
        )
        sys.argv = [
            "verify_foundry",
            "--database",
            str(output.parent / "work" / f"foundry-check-{int(time())}.db"),
            "--report",
            str(output / "foundry-verification.json"),
        ]
        verify(gateway)
        from dotenv import set_key

        env_path = Path(__file__).resolve().parents[1] / ".env"
        for name, value in connection.items():
            set_key(env_path, name, value)
        print("Live Foundry connection verified. Configuration and evidence saved to " + str(output))
    finally:
        credential.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Connection not verified: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
