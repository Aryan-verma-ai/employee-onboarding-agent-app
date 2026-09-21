"""API-key model smoke test; this does not verify a managed Foundry agent."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from openai import OpenAI


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--report")
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    key = next(
        (
            values.get(name)
            for name in ("AZURE_OPENAI_API_KEY", "AZURE_AI_API_KEY", "FOUNDRY_API_KEY", "AZURE_API_KEY")
            if values.get(name)
        ),
        None,
    )
    if not key:
        raise SystemExit("API key is missing from the selected .env file. No request was sent.")
    base = values.get("AZURE_OPENAI_BASE_URL")
    parsed = urlsplit(base or "")
    if (
        parsed.scheme != "https"
        or parsed.hostname != "employee-onboarding-kc-resource.services.ai.azure.com"
        or parsed.path != "/openai/v1/"
    ):
        raise SystemExit("Expected the configured Korea Central resource endpoint; no secret was sent.")
    report = {
        "test": "azure_model_only",
        "managed_agent_verified": False,
        "deployment": values.get("FOUNDRY_MODEL_DEPLOYMENT_NAME"),
        "verified": False,
    }
    try:
        with OpenAI(api_key=key, base_url=base, timeout=45, max_retries=0) as client:
            result = client.responses.create(
                model=report["deployment"],
                input="Reply with CONNECTION_OK only. This is a synthetic connection test.",
                max_output_tokens=32,
                store=False,
            )
        report.update(
            verified=bool(result.output_text.strip()), response_id=result.id, response=result.output_text
        )
    except Exception as error:
        # Never print exception bodies, request headers, or environment values.
        report.update(error_type=type(error).__name__, http_status=getattr(error, "status_code", None))
    if args.report:
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["verified"] else 1)


if __name__ == "__main__":
    main()
