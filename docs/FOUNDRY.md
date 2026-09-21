# Azure AI Foundry implementation

The conversational agent is a persisted, versioned **Foundry Agent Service prompt agent**.
Python is the authenticated application and tool execution boundary. It does not substitute a local
chatbot for Foundry. The OpenAI-compatible Responses transport invokes the registered agent by
name and version through the Foundry project endpoint. No model is selected at runtime.

## Provision and run

1. Create an Azure AI Foundry resource/project and deploy a model supporting function tools.
2. Give the provisioning identity permission to create agents and the runtime managed identity
   the Foundry User (previously Azure AI User) role scoped to the project. Use `az login` locally;
   deployed code uses `DefaultAzureCredential` and managed identity. Do not put credentials in git.
3. Set `FOUNDRY_PROJECT_ENDPOINT` to `https://RESOURCE.services.ai.azure.com/api/projects/PROJECT`,
   `FOUNDRY_MODEL_DEPLOYMENT_NAME` to your actual deployment name, and
   `FOUNDRY_AGENT_NAME=employee-onboarding`.
4. From the repository root run `python -m scripts.provision_agent`. This creates an agent version
   in Azure and prints its name, version and ID. Every invocation creates a new version.
5. Set `FOUNDRY_AGENT_VERSION` to the returned version, then start the API. Retain the previous
   version to support rollback; update the runtime setting deliberately when promoting a version.
6. Open the registered agent in Foundry. Exercise chat through the application so the application
   can service local function calls. Confirm the response ID and agent version in the audit trail.

Use `azure-ai-projects>=2.3.0,<3` plus `azure-identity` and its OpenAI transport dependency.
Offline verification used Azure AI Projects 2.7.0 and OpenAI 3.16.2; prompt/tool serialization
and SDK method signatures were inspected against these installed versions.
Projects 1.x/classic threads/runs examples are incompatible with this integration.

## Trust boundary and persistence

`POST /api/cases/{case_id}/chat` authenticates first and checks case access before any Azure call.
The database stores the Foundry conversation ID, including before the first model response to
preserve a newly created conversation if an upstream call fails. Clients cannot supply conversation
IDs. An in-process lock serializes chat requests in the supported single-worker deployment to
prevent parallel conversation creation or overlapping tool turns. A distributed lock is required
before scaling multiple workers.

The two zero-argument tools read status and request deterministic validation. They receive the
authorized case from the server, never the model. Tool output contains only status, missing field
names, and whether creation has occurred. Employee creation is deliberately absent from tools:
the HR user must use the explicit authenticated finalization endpoint after reviewing valid data.
Chat text cannot forge that approval. PAN/Aadhaar-like patterns are redacted before submission and
in assistant output; this is defense in depth, not comprehensive personal-data classification.

The loop permits at most eight response rounds. Unknown tools and extra arguments are rejected.
Audit records contain response/conversation IDs, pinned agent reference and tool names, not raw
prompts, OCR or identity numbers. Upstream errors are surfaced as failures; there is no simulated
production response. Retention/deletion must cover both the application DB and Foundry conversations.

## Tracing and evaluation

Connect Application Insights in Foundry under Agents > Traces > Connect to enable server-side
tracing. Restrict telemetry access and configure retention; agent conversations can contain personal
data even when tool results are minimal. Correlate application audit response IDs with Foundry traces.

`pytest tests/test_foundry.py` runs **mocked offline contract tests**, not cloud acceptance tests.
`python -m evaluations.run` runs the synthetic fixture suite against the configured real Azure agent,
consumes model tokens and deletes its test conversations. It uses synthetic tool state and basic
forbidden-phrase checks. Review responses manually and add Foundry rubric evaluation for task
adherence, correct tool use, privacy, missing-information handling and refusal to bypass HR review.
Neither unit tests nor this small suite establish production model quality or OCR accuracy.

Cloud acceptance: create a version, run a real application conversation, inspect its tool call and
response trace, restart the app and continue the same case, validate uploaded sample documents,
and finalize through HR approval. Record Azure resource, model deployment, agent version, response
IDs and observed results. Until executed, deployment and live agent behavior remain unverified.

## Official references (reviewed 2026-09-20)

- [Managed prompt agents](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent)
- [Function tools](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/function-calling)
- [Tracing](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-setup)
- [Agent evaluation](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/evaluate-agent)


## Korea Central project configuration

The existing app now targets `employee-onboarding-agent-kc` in Korea Central:

- Project: `https://employee-onboarding-kc-resource.services.ai.azure.com/api/projects/employee-onboarding-agent-kc`
- Model deployment: `gpt-4.1-mini`
- Model API: `https://employee-onboarding-kc-resource.services.ai.azure.com/openai/v1/`

The repository `.env` is ignored by Git. At configuration time no key-bearing `.env` was found in the checkout or Codex workspace; the generated local `.env` contains non-secret configuration and an empty key placeholder. The key was subsequently populated locally and the API-key model smoke test passed; no secret was displayed or committed.

`python -m scripts.verify_model --env-file PATH --report REPORT.json` performs a small synthetic API-key model test. It prints no keys or raw provider error bodies and explicitly does not certify the managed agent. Azure AI Projects 2.x supports Entra authentication only. Use `scripts.connect_foundry` / `scripts.verify_foundry` with an accessible authorized Entra session to provision and verify the actual managed agent. A model response alone must never mark issue #6 complete.

A no-credential request reached the supplied project endpoint and received HTTP 401. This confirms transport reachability only; the subsequent authenticated model smoke test passed. Managed-agent verification still needs Entra access.
