# Foundry observability and conversation coordination

## Conversation locking

PostgreSQL deployments coordinate chat by a deterministic signed 64-bit hash of tenant and case.
A dedicated SQLAlchemy connection holds a session-level advisory lock throughout the response/tool
loop. It is independent of the ORM connection: validation commits and conversation-ID persistence
cannot release the lock. The dedicated connection uses AUTOCOMMIT, so it does not retain a long-running
transaction. The authorization read is released before acquisition; access is checked again inside.

The lock uses bounded polling (10 seconds). Busy cases return HTTP 409 with Retry-After: 2. Locks are
explicitly released on all exits. Unlock failures invalidate the physical connection rather than
returning a potentially locked connection to the pool. PostgreSQL disconnect cleanup releases session
locks. No lease table or migration is needed. Hash collisions can serialize unrelated cases but cannot
expose data. SQLite is explicitly development-only and uses per-case in-process locks.

Provision database pool capacity for one dedicated connection plus one ORM connection per active chat,
plus non-chat traffic. PostgreSQL must be reached directly or through a session-mode pooler: transaction
pooling (including PgBouncer transaction mode) cannot preserve session advisory locks. Set connection
and pool timeouts at deployment. Requests that lose the lock connection fail; they must not be replayed
blindly because a Foundry response may already have been accepted. Inspect response IDs before retrying
ambiguous failures. Advisory locking coordinates chat workers only, not separate form/document edits.

## Optional OpenTelemetry

By default application tracing is off. Set ONBOARDING_OTEL_ENABLED=true to emit manual spans through
opentelemetry-api. Configure an SDK TracerProvider and an OTLP exporter in your deployment bootstrap
before starting workers (install opentelemetry-sdk and opentelemetry-exporter-otlp). Exporter URL/auth
and sampling are deployment configuration; the app never hardcodes an exporter or sends telemetry to
an unsolicited endpoint. An enabled API without a provider is a no-op. For Azure server-side traces,
connect Application Insights to the existing Foundry project.

Manual spans are onboarding.chat and foundry.response. They contain only the pinned agent version,
Foundry response ID and a boolean failure marker. Raw prompts, assistant text, tool arguments, document
text, identity values, exception messages and tenant/case identifiers are excluded. Exception capture
is disabled for these spans. SDK/HTTP auto-instrumentation is separate and can capture content: do not
enable content capture; review its privacy settings before deployment. Restrict trace access and
retention. Database audits provide the authorized correlation between cases and Foundry response IDs.

## Evaluation

Run python -m evaluations.run --live only when explicitly opting into Azure model charges. This reuses
the configured agent/version; it never creates or updates the agent. Eight synthetic scenarios cover
incomplete input, validation success, created state, conflicting evidence, extraction failure, forged
approval, identity privacy and cross-case access requests. Each has its own tool state; validation can
change that state. Results expose response IDs, actual tools, agent reference and separate safety,
workflow, tool-use, grounding and escalation metrics. Conversations are deleted after each scenario,
including failures after conversation creation. Output contains synthetic fixture messages only.

These are deterministic smoke metrics, not an LLM quality score or proof of production safety. Keyword
metrics can miss paraphrased false claims and should be paired with human review/Foundry evaluators.
The existing provisioning verification is not repeated by this change. Offline tests cover lock cleanup,
timeouts, tenant-scoped keys, fixture transitions and metric failures. Live PostgreSQL coordination and
new evaluation scenarios require environment-specific acceptance evidence.

## Explicit resource-key transport

Entra remains the default (FOUNDRY_AUTH_MODE=entra). Where the configured Foundry resource accepts
resource-key authentication, FOUNDRY_AUTH_MODE=api_key uses AZURE_OPENAI_API_KEY through an api-key
header. The adapter accepts only a standard HTTPS *.services.ai.azure.com/api/projects/PROJECT
endpoint and restricts every outbound request to that exact host and project OpenAI path; redirects
are disabled. It still sends the persisted agent name/version reference, creates Foundry conversations,
and runs the same validated tool loop. It never falls back to model-only inference or sends a key to
another configured model/provider. Manage and rotate this resource-wide credential in your secret
store; managed identity is preferred for deployed least-privilege access. Header/path behavior has
mock transport tests. Live compatibility must be recorded for the actual resource separately.

## Escalation integrity and current acceptance

Tool results distinguish conflicting_fields and failed_extractions from missing_fields and include
escalation_required plus next_action. Conflict and extraction failures require HR review, not merely
re-entry of a supposedly missing identity field. Final server formatting enforces this guidance from
authoritative case state if a model omits it, and records foundry.escalation_enforced. It never exposes
raw evidence values. Evaluation reports retain model_message/model_metrics separately from guarded
application message/metrics so server protections do not disguise model quality failures.

The subsequent live eight-scenario run passed all application metrics and all raw model metrics using
employee-onboarding version 1 and gpt-4.1-mini. No agent version was recreated. Exact-version metadata
was also read successfully with the scoped API-key path. Evidence is in the task's
outputs/foundry-evaluation.jsonl; results are a synthetic smoke baseline rather than a production SLA.
Real database lock tests are opt-in with TEST_POSTGRES_URL and exercise independent PostgreSQL sessions,
commit/rollback survival, tenant scoping and release after exceptions.
