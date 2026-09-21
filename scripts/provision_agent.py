"""Create a real, versioned prompt agent in an existing Foundry project."""

import json
import os

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition

from app.azure_auth import azure_credential
from app.foundry import INSTRUCTIONS, tool_definitions


def main():
    with azure_credential() as credential:
        with AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=credential
        ) as project:
            agent = project.agents.create_version(
                agent_name=os.environ.get("FOUNDRY_AGENT_NAME", "employee-onboarding"),
                definition=PromptAgentDefinition(
                    model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
                    instructions=INSTRUCTIONS,
                    tools=tool_definitions(),
                ),
            )
            print(
                json.dumps(
                    {
                        "FOUNDRY_AGENT_NAME": agent.name,
                        "FOUNDRY_AGENT_VERSION": agent.version,
                        "agent_id": agent.id,
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
