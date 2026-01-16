# Phase 2: Platform Capabilities

### DAN-11: JWT auth with Dex OIDC
- **Status**: todo
- **Summary**: Integrate Dex authentication for UI and API.
- **Description**: Validate JWT tokens, handle redirects, and map users to SQLite identities.
- **Acceptance Criteria**:
  - JWT validation checks signature, issuer, audience, and expiry.
  - Unauthorized requests are rejected with 401.
- **Testing**:
  - Add unit tests for JWT validation and auth middleware.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/security.md` authentication section.

### DAN-12: Team-based RBAC authorization
- **Status**: todo
- **Summary**: Enforce permissions for pipelines and resources.
- **Description**: Implement read/write/admin checks using team memberships and pipeline permissions.
- **Acceptance Criteria**:
  - Permission checks applied to pipeline, job, artifact, and secret endpoints.
  - Global admins bypass specific checks.
- **Testing**:
  - Add unit tests for permission resolution.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/security.md` authorization section.

### DAN-13: Artifact upload and storage
- **Status**: todo
- **Summary**: Accept and store build artifacts.
- **Description**: Implement artifact upload RPC, store artifacts on disk, and persist metadata in SQLite.
- **Acceptance Criteria**:
  - Artifacts stored under `/var/lib/danube/artifacts/<job_id>/`.
  - Artifact metadata persisted and retrievable via API.
- **Testing**:
  - Add integration tests for upload and download flows.
  - Run `uv run snektest tests/integration/`.
- **Documentation**:
  - Update `docs/architecture/data-model.md` artifacts section.

### DAN-14: SLSA provenance generation
- **Status**: todo
- **Summary**: Generate signed provenance after job completion.
- **Description**: Create in-toto provenance document and sign with Ed25519.
- **Acceptance Criteria**:
  - Provenance includes build definition, inputs, outputs, environment.
  - Signature stored alongside provenance file.
- **Testing**:
  - Add unit tests for provenance content and signature verification.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/security.md` SLSA section.

### DAN-15: Observability metrics endpoint
- **Status**: todo
- **Summary**: Expose Prometheus metrics.
- **Description**: Add `/metrics` endpoint with job, db, k8s, and log metrics.
- **Acceptance Criteria**:
  - Metrics include job counts, durations, db query stats.
  - Endpoint available when `metrics_enabled` is true.
- **Testing**:
  - Add unit tests for metrics registry output.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/observability.md` metrics section.

### DAN-16: OpenTelemetry tracing
- **Status**: todo
- **Summary**: Emit traces for API and job execution.
- **Description**: Add tracing spans for HTTP requests and orchestration steps with OTLP export.
- **Acceptance Criteria**:
  - Traces exported when enabled in config.
  - Trace context propagated across RPC boundaries.
- **Testing**:
  - Add unit tests for trace span creation.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/observability.md` tracing section.

### DAN-17: Egress allowlist enforcement
- **Status**: todo
- **Summary**: Apply network policies for hermetic builds.
- **Description**: Implement Cilium NetworkPolicy generation based on blueprint config.
- **Acceptance Criteria**:
  - Policy allows only configured domains and registry.
  - Policy updates on blueprint sync.
- **Testing**:
  - Add integration tests for policy generation.
  - Run `uv run snektest tests/integration/`.
- **Documentation**:
  - Update `docs/architecture/networking.md` egress allowlist section.

### DAN-18: Database schema + queries
- **Status**: todo
- **Summary**: Implement SQLite schema and query layer.
- **Description**: Add schema definitions, query helpers for users, teams, pipelines, jobs, steps, secrets, artifacts.
- **Acceptance Criteria**:
  - Tables and indexes match data model specification.
  - Queries cover CRUD for all core entities.
- **Testing**:
  - Add unit tests using in-memory SQLite.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/data-model.md` with schema status and notes.
