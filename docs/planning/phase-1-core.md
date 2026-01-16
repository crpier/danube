# Phase 1: Core Foundation

### DAN-1: Implement FastAPI HTTP API skeleton
- **Status**: todo
- **Summary**: Stand up the HTTP REST API surface for core resources.
- **Description**: Create FastAPI routes and request/response models for pipelines, jobs, artifacts, webhooks, health, and metrics.
- **Acceptance Criteria**:
  - Routes exist for `/api/v1/pipelines`, `/api/v1/jobs`, `/api/v1/artifacts`, `/webhooks/github`, `/webhooks/gitlab`, `/health`, `/health/ready`, `/metrics`.
  - Basic request validation and stubbed responses are implemented.
- **Testing**:
  - Add unit tests covering route registration and basic responses.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/components.md` with API surface summary.

### DAN-2: Implement HTTP/2 RPC control plane
- **Status**: todo
- **Summary**: Add the internal RPC endpoints for Coordinator ↔ Master calls.
- **Description**: Implement `/rpc/run-step`, `/rpc/get-secret`, `/rpc/upload-artifact`, `/rpc/report-status` with JSON schemas and job validation.
- **Acceptance Criteria**:
  - RPC endpoints accept JSON payloads and return structured responses.
  - Job ID validation rejects unknown jobs.
- **Testing**:
  - Add unit tests for RPC handler validation.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/components.md` with RPC endpoint details.

### DAN-3: Job orchestrator state machine
- **Status**: todo
- **Summary**: Implement job lifecycle transitions and persistence.
- **Description**: Build the orchestrator to manage state changes and persist them to SQLite.
- **Acceptance Criteria**:
  - State transitions follow `pending → scheduling → running → success|failure|timeout|cancelled`.
  - Status is persisted and queryable via DB layer.
- **Testing**:
  - Add unit tests for state transitions.
  - Run `uv run snektest tests/unit/test_orchestrator.py`.
- **Documentation**:
  - Update `docs/architecture/execution-model.md` with state transition behavior.

### DAN-4: Scheduler for cron + webhook triggers
- **Status**: todo
- **Summary**: Schedule jobs from cron and webhook events.
- **Description**: Implement cron polling, webhook ingestion, and deduplication by ref.
- **Acceptance Criteria**:
  - Cron schedule evaluated every 60s.
  - Webhook triggers enqueue jobs immediately.
  - Duplicate triggers for same ref are deduped.
- **Testing**:
  - Add unit tests for cron parsing and webhook dedupe.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/components.md` scheduler section.

### DAN-5: K8s client wrapper
- **Status**: todo
- **Summary**: Add Kubernetes client operations for pods and exec.
- **Description**: Implement pod create/delete, exec command streaming, and log streaming wrappers.
- **Acceptance Criteria**:
  - Wrapper exposes create/delete pod and exec command APIs.
  - Log streaming supports stdout/stderr multiplexing.
- **Testing**:
  - Add unit tests with mocked K8s client calls.
  - Run `uv run snektest tests/unit/test_k8s_client.py`.
- **Documentation**:
  - Update `docs/architecture/components.md` K8s client section.

### DAN-6: Pod builder for coordinator + worker
- **Status**: todo
- **Summary**: Build the dual-container pod manifest.
- **Description**: Generate pod spec with coordinator image, worker image, shared `/workspace` volume, and resource limits.
- **Acceptance Criteria**:
  - Pod manifest includes both containers and shared volume.
  - Resource requests/limits are set from pipeline config.
- **Testing**:
  - Add unit tests for manifest generation.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/execution-model.md` pod pattern section.

### DAN-7: Log streaming pipeline to disk + SSE
- **Status**: todo
- **Summary**: Persist job logs and stream to SSE clients.
- **Description**: Write logs to `/var/lib/danube/logs/<job_id>.log` and stream updates to SSE endpoints.
- **Acceptance Criteria**:
  - Log writer appends stdout/stderr to file.
  - SSE clients receive live log updates with backpressure handling.
- **Testing**:
  - Add unit tests for log writer and SSE stream handler.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/observability.md` logging section.

### DAN-8: Blueprint repo syncer
- **Status**: todo
- **Summary**: Implement GitOps sync for configuration.
- **Description**: Poll the blueprint repo, parse JSON config files, and update SQLite accordingly.
- **Acceptance Criteria**:
  - Sync runs at configured interval and logs diffs.
  - Failed syncs do not modify active configuration.
- **Testing**:
  - Add integration tests using a temp Git repo.
  - Run `uv run snektest tests/integration/test_blueprint_sync.py`.
- **Documentation**:
  - Update `docs/configuration/blueprint-reference.md` sync behavior section.

### DAN-9: Blueprint validation (schema + references)
- **Status**: todo
- **Summary**: Validate blueprint files before applying changes.
- **Description**: Add JSON Schema validation and cross-reference checks for teams/pipelines.
- **Acceptance Criteria**:
  - Invalid schema blocks sync and logs errors.
  - Missing team references are rejected.
- **Testing**:
  - Add unit tests for validation failure cases.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/configuration/blueprint-reference.md` validation section.

### DAN-10: SecretService cache + AES-256-GCM
- **Status**: todo
- **Summary**: Provide encrypted secrets retrieval for jobs.
- **Description**: Decrypt secrets into an in-memory cache scoped by job_id and expose via RPC.
- **Acceptance Criteria**:
  - Secrets stored encrypted in SQLite and decrypted on job start.
  - Secrets accessible only through RPC and never via env vars.
- **Testing**:
  - Add unit tests for encrypt/decrypt and cache lifecycle.
  - Run `uv run snektest tests/unit/test_secrets.py`.
- **Documentation**:
  - Update `docs/architecture/security.md` secrets section.
