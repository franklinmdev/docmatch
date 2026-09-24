# Langfuse local tracing (issue #148)

Question: can Langfuse trace the loop with every trace staying on this machine, and at what cost?

Date read for every source below: 2026-09-23. Primary sources only. Anything not verified in a primary source is marked "unverified".

## Sources

| Source | URL | Version at read time |
|---|---|---|
| Self-hosting overview and architecture | https://langfuse.com/self-hosting | docs label "Version: v4" |
| Docker Compose deployment guide | https://langfuse.com/self-hosting/deployment/docker-compose | docs label "Version: v4" |
| docker-compose.yml | https://github.com/langfuse/langfuse/blob/main/docker-compose.yml | main, images `langfuse:4`, `langfuse-worker:4`, `clickhouse-server:25.12`, `redis:7`, `postgres:17` default, chainguard `minio` |
| Redis/Valkey configuration | https://langfuse.com/self-hosting/deployment/infrastructure/cache | docs label "Version: v4" |
| Blob storage configuration | https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage | docs label "Version: v4" |
| Networking (self-hosted) | https://langfuse.com/self-hosting/security/networking | docs label "Version: v4" |
| Telemetry (self-hosted) | https://langfuse.com/self-hosting/security/telemetry | docs label "Version: v4" |
| langfuse/langfuse README | https://github.com/langfuse/langfuse/blob/main/README.md | main |
| langfuse/langfuse latest release | https://github.com/langfuse/langfuse/releases | v4.43.0, published 2026-09-23 |
| langfuse on PyPI | https://pypi.org/project/langfuse/ | 4.15.4, uploaded 2026-09-16 (GitHub release v4.15.4 of langfuse-python, 2026-09-16) |
| SDK overview | https://langfuse.com/docs/observability/sdk/overview | Python SDK v4 |
| SDK instrumentation | https://langfuse.com/docs/observability/sdk/instrumentation | Python SDK v4 |
| SDK advanced features | https://langfuse.com/docs/observability/sdk/advanced-features | Python SDK v4 |
| Token and cost tracking | https://langfuse.com/docs/observability/features/token-and-cost-tracking | current docs |
| Langfuse OpenTelemetry endpoint | https://langfuse.com/integrations/native/opentelemetry | current docs |
| Export from UI | https://langfuse.com/docs/api-and-data-platform/features/export-from-ui | current docs |
| Export to blob storage | https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage | current docs |
| OpenTelemetry Python exporters | https://opentelemetry.io/docs/languages/python/exporters/ | site shows semantic conventions 1.44.0 |
| OpenTelemetry Collector exporters | https://opentelemetry.io/docs/collector/components/exporter/ | current docs |
| OpenTelemetry GenAI semconv (moved page) | https://opentelemetry.io/docs/specs/semconv/gen-ai/ | moved page only |
| opentelemetry-sdk on PyPI | https://pypi.org/project/opentelemetry-sdk/ | 1.44.0, uploaded 2026-07-16 |

Note: the Langfuse docs pages were read as Markdown by appending `.md` to the page URL.

## 1. What self-hosted Langfuse requires

Yes to all of it. The current architecture (v4) has two application containers plus four storage services:

- Langfuse Web (`langfuse/langfuse`, UI and API) and Langfuse Worker (`langfuse/langfuse-worker`, async event processing).
- Postgres (transactional data).
- ClickHouse (traces, observations, scores).
- Redis or Valkey (cache and queue).
- S3-compatible blob storage (raw events, multimodal inputs, exports).

Source: https://langfuse.com/self-hosting (architecture section and diagram).

The docker-compose.yml service list matches: `langfuse-worker`, `langfuse-web`, `clickhouse`, `minio`, `redis`, `postgres`. Six containers.

**Redis/Valkey is not optional.** The compose file makes `langfuse-worker` depend on `redis`, and the docs describe the ingestion path this way: the web container writes each event batch to S3 and puts only a reference in Redis; the worker reads the queue from Redis and ingests into ClickHouse (https://langfuse.com/self-hosting, "Queued trace ingestion"). The cache page says Langfuse "uses Redis/Valkey as a caching layer and queue", needs Redis 7 or newer (Valkey 8 or newer is also official) and `maxmemory-policy=noeviction` (https://langfuse.com/self-hosting/deployment/infrastructure/cache). I found no documented setting that turns the Redis dependency off. "Unverified" that none exists in code, but the docs and compose file treat it as required.

This directly conflicts with the repo's stack decision (CLAUDE.md "Cut on purpose": Celery and Redis). Valkey is a Redis fork, so swapping it in does not remove the extra service; it renames it.

Also relevant to the repo's Postgres decision: Langfuse's own Postgres is a separate database from docmatch's, and ClickHouse holds the trace data, not Postgres.

## 2. Lighter self-hosting mode

The docs list three deployment options: Langfuse Cloud, Docker Compose ("Local use and testing", "Single VM without high availability, scaling, or backups"), and Kubernetes/cloud templates for production (https://langfuse.com/self-hosting). I found no single-container or reduced mode (no SQLite mode, no "without ClickHouse" profile, no in-process queue). The Docker Compose guide is the smallest documented option and it runs the full six-container stack. So the full stack is mandatory even for one user, as far as the documentation shows.

Sizing: the Docker Compose guide's VM tab recommends at least 4 cores and 16 GiB of memory. That is for the VM path. A minimum for the local path is unverified.

Blob storage: the compose file ships MinIO, so no cloud bucket is needed. The docs also list Amazon S3, GCS, Azure Blob, and Cloudflare R2 as official options, none of which are needed for a local-only setup (https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage).

Local-only network posture: the compose file's header comment says all components except the web container are bound to `127.0.0.1`. Two exceptions I saw in the file: `langfuse-web` publishes `3000:3000` (all interfaces) and MinIO publishes `9090:9000` (all interfaces), while its console is on `127.0.0.1:9091`. Bind those to localhost if traces must not be reachable from the LAN.

## 3. Python SDK: version and recording primitives

Version: `langfuse` 4.15.4, uploaded to PyPI 2026-09-16 (also GitHub release v4.15.4 of langfuse-python, 2026-09-16). Self-hosted server minimum for SDK v3 and v4 is 3.63.0; the current server release is v4.43.0 (2026-09-23) (https://langfuse.com/docs/observability/sdk/overview).

Primitives (https://langfuse.com/docs/observability/sdk/instrumentation):

- `@observe()` decorator, with `name=` and `as_type="generation"` etc. Captures input, output, timings and errors automatically.
- `langfuse.start_as_current_observation(as_type=..., name=...)` context manager. Nested blocks become child observations through OpenTelemetry context.
- `langfuse.start_observation(...)` for manual observations that do not change the active context.
- `langfuse.update_current_span(...)` and `langfuse.update_current_generation(...)`, and `.update(...)` on an observation object.
- Observation types via `as_type`, including `span`, `generation`, and `embedding` (cost is captured on `generation` and `embedding`).

What they record:

- **Latency**: every observation carries start and end timestamps; the SDK doc states "accurate latency tracking via synchronous timestamps".
- **Cost and usage**: `usage_details={"input": ..., "output": ..., "cache_read_input_tokens": ...}` and `cost_details={"input": ..., "output": ...}` in USD, per usage type, on generations and embeddings (https://langfuse.com/docs/observability/features/token-and-cost-tracking). Ingested values take priority. If not ingested, Langfuse infers usage with a tokenizer and cost from a model definition matched on the `model` parameter; custom model definitions with our own prices can be added in Project Settings. Each token must be counted in exactly one usage key or cost is double counted.
- **Custom per-stage metadata**: `metadata={...}` on any observation (example in the docs: `metadata={"confidence": 0.9}`). Trace-level attributes such as environment, tags, user and session also exist on the same page.

Fit for docmatch: stage as span, model call as generation with our own computed cost (we already compute list price times vendor units) passed in `cost_details`, which avoids relying on Langfuse's price table for Azure Document Intelligence, which is billed per page and is not a token model. That last point is my inference: the docs describe usage per "unit" and custom usage types, but I did not verify a per-page pricing example. Mark "unverified" until tried.

## 4. Export, and local sinks

Traces already stay on this machine if the SDK points at a local server. The SDK works against Cloud and self-hosted "with the same code; only the credentials and base URL differ" (https://langfuse.com/docs/observability/sdk/overview). Set `LANGFUSE_BASE_URL=http://localhost:3000`.

Self-hosted egress (this is the rule 6 question):

- The docs state "Langfuse does not require internet access" and that it only pings a cached GitHub API copy to check for updates, failing gracefully offline (https://langfuse.com/self-hosting/security/networking).
- Langfuse self-hosted sends usage telemetry to PostHog Cloud by default. The docs say it does not include raw traces, prompts, observations, scores, or dataset contents; it sends aggregates (version, project counts, trace and score counts, up to 30 user email domains with counts). Disable with `TELEMETRY_ENABLED=false` on all application containers (https://langfuse.com/self-hosting/security/telemetry). The compose file defaults it to `true`, so set it to false. For rule 6 purposes, set it false regardless.
- LLM Playground and LLM-evals call an external LLM API only if configured; not needed for tracing.
- So Langfuse Cloud is disqualified by rule 6, but self-hosting is not, provided telemetry is turned off and the ports are local.

Data out of Langfuse: UI batch export to CSV or JSON (https://langfuse.com/docs/api-and-data-platform/features/export-from-ui), scheduled blob storage export (listed as available for Self Hosted), and the public API and SDK for programmatic reads (https://langfuse.com/docs/api-and-data-platform/features/public-api, referenced not read in depth).

Local sink without a Langfuse server: the SDK exposes `tracer_provider=` (isolated TracerProvider), `should_export_span=`, `mask_otel_spans=` and a `LANGFUSE_TRACING_ENABLED`-style switch is in the docs' configuration (exact name unverified) (https://langfuse.com/docs/observability/sdk/advanced-features). I found no documented file exporter or "write locally first" mode in the Langfuse SDK. Whether a second span processor (for example a console or file exporter) can be attached to the isolated `TracerProvider` alongside Langfuse's is plausible from the OTel design but unverified in Langfuse's docs. `mask_otel_spans` runs at export and could redact extracted text before it reaches even the local server.

## 5. OpenTelemetry underneath, and plain OpenTelemetry as fallback

Langfuse SDK v4 is built on OpenTelemetry: the SDK overview lists "Based on OpenTelemetry, so you can use any OTEL-based instrumentation library". `start_as_current_observation` uses the active OTel context. Langfuse also accepts OTLP directly at `/api/public/otel` (HTTP/JSON and HTTP/protobuf, no gRPC), local example `http://localhost:3000/api/public/otel` for deployments at or above v3.22.0, with an `Authorization: Basic` header and `x-langfuse-ingestion-version=4` (https://langfuse.com/integrations/native/opentelemetry). The docs also state the older `POST /api/public/ingestion` is deprecated and on Cloud stops accepting everything except scores on 2026-11-16.

Plain OpenTelemetry (SDK 1.44.0, 2026-07-16):

- Python SDK `opentelemetry-sdk` includes `ConsoleSpanExporter` and `ConsoleMetricExporter` (https://opentelemetry.io/docs/languages/python/exporters/). OTLP exporters send to a collector.
- The Collector lists a File Exporter and a Debug Exporter, both marked alpha in the table I read (https://opentelemetry.io/docs/collector/components/exporter/). Stability columns per signal: I read "alpha alpha alpha" for File Exporter; treat as alpha.
- Latency: native. Every span has start and end times. Nesting and per-stage spans work.
- Custom metadata: span attributes, arbitrary key-values. Native.
- Cost: no built-in cost concept that I verified. You would record cost as our own span attributes (for example `docmatch.cost_usd`). GenAI semantic conventions define token usage attributes, but the OpenTelemetry site page is a "moved" stub pointing to the `open-telemetry/semantic-conventions-genai` repository, which I did not read. Stability of the GenAI conventions is unverified. Whether they include any cost attribute is unverified.
- No UI, no aggregation, no cost roll-ups, no model price tables. You would need a backend. The docs I read for plain OTel name Jaeger and the Collector; picking and running a backend (or querying a JSONL file yourself) is the cost.

## Bottom line

Can Langfuse trace the loop with every trace staying on this machine? Yes, self-hosted. The Python SDK 4.15.4 targets a local server by base URL only, the docs say Langfuse needs no internet access, and the only outbound call besides an update check is usage telemetry that carries no trace content and can be disabled with `TELEMETRY_ENABLED=false`. Langfuse Cloud stays out under rule 6, and the self-hosted route does not share that problem.

Cost:

- **Infrastructure**: the smallest documented deployment is six containers (web, worker, Postgres, ClickHouse, Redis, MinIO). No lighter mode is documented. Redis is a hard requirement (queue and cache), Valkey 8 or newer is the only official alternative, so the stack decision to cut Redis cannot be honoured while running Langfuse. This is the finding to flag: adopting Langfuse means adding Redis and ClickHouse and object storage to a stack that deliberately has only Postgres. The docs suggest 4 cores and 16 GiB for a VM; whether a dev machine with less memory can run it is unverified.
- **Isolation option**: keep it fully outside the engine's runtime. Run the compose stack only on the dev machine and CI does not need it (the SDK is designed not to break the app and can be disabled), but that is my inference, not something I verified in the docs.
- **Operational**: no backups or HA in the compose path, per the docs.

What plain OpenTelemetry gives up by comparison: no trace UI, no cost and usage model, no price inference, no dashboards, no Metrics API, no export UI. It keeps per-stage latency and arbitrary metadata for free and adds no new services if written to console or a file, at the price of building any cost and p95 rollups ourselves, which for this repo (cost and latency per stage, from code that already computes both) may be enough. Recommended next step for the decision ticket: state which is wanted, a browsable UI (accept Redis, ClickHouse, MinIO on the dev machine only) or numbers in the report (plain OTel to a file, zero new services).
