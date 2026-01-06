# Tasks

### DANUBE-E1: Core Infrastructure

---

#### DANUBE-1: Initialize Rust Workspace

**Status:** 🟡 In Progress
**Type:** Task
**Priority:** Highest
**Estimate:** 2h

**Description:**
Set up the Rust workspace with Cargo, establish project structure, and configure basic tooling.

**Acceptance Criteria:**

- [ ] Cargo workspace initialized with `danube-master`, `danube-proto` crates
- [ ] `rust-toolchain.toml` pinning stable version
- [ ] `.cargo/config.toml` with release profile optimizations
- [ ] `rustfmt.toml` and `clippy.toml` configured
- [ ] `README.md` with build instructions
- [ ] **Tests:** Verify `cargo check` passes on clean workspace
- [ ] **Documentation:** README includes architecture overview and build steps

**Files to Create:**

```
danube/
├── Cargo.toml (workspace)
├── rust-toolchain.toml
├── .cargo/config.toml
├── crates/
│   ├── danube-master/
│   │   ├── Cargo.toml
│   │   └── src/main.rs
│   └── danube-proto/
│       ├── Cargo.toml
│       ├── src/lib.rs
│       └── proto/
└── docs/
    └── getting-started.md
```

**Note:** No CI workflows for external services. Danube will eventually build itself.

---

#### DANUBE-2: Set Up Structured Logging

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 3h
**Depends On:** DANUBE-1

**Description:**
Configure application-wide structured logging with tracing early in the project lifecycle.

**Acceptance Criteria:**

- [ ] Use `tracing` crate with `tracing-subscriber`
- [ ] JSON format in production (`DANUBE_LOG_FORMAT=json`), pretty format in development
- [ ] Log levels configurable via env var (`DANUBE_LOG=info,danube_master=debug`)
- [ ] Request ID propagation across async tasks (tower-http RequestId)
- [ ] Sensitive data redacted automatically (secrets, tokens, email)
- [ ] Log to stdout (container-friendly)
- [ ] **Tests:** Verify log output format, verify secret redaction
- [ ] **Documentation:** Document log level configuration and formatting options

**Example Log Entry (JSON):**

```json
{
  "timestamp": "2026-01-06T12:34:56.789Z",
  "level": "INFO",
  "target": "danube_master::jobs",
  "request_id": "abc123",
  "message": "Job started",
  "job_id": "job-456",
  "pipeline_id": "pipeline-789"
}
```

---

#### DANUBE-3: Set Up OpenTelemetry & Metrics

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 4h
**Depends On:** DANUBE-2

**Description:**
Initialize OpenTelemetry SDK for metrics and tracing early in development.

**Acceptance Criteria:**

- [ ] Configure `opentelemetry` and `opentelemetry-otlp` crates
- [ ] Prometheus metrics exporter at `/metrics` endpoint
- [ ] OTLP trace exporter to configured collector (optional)
- [ ] Metrics: `danube_build_info`, `danube_uptime_seconds`
- [ ] Tracing: Instrument all async functions with `#[instrument]`
- [ ] Configuration in CaC repo `config.yaml` under `observability`
- [ ] Graceful degradation if OTLP collector unavailable
- [ ] **Tests:** Verify metrics endpoint returns valid Prometheus format
- [ ] **Documentation:** Document metrics available and OTLP configuration

---

#### DANUBE-4: Implement Minimal Configuration Loading

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 3h
**Depends On:** DANUBE-2

**Description:**
Implement minimal TOML configuration file loading - only CaC repository location.

**Acceptance Criteria:**

- [ ] `Config` struct with `config_repo` section only
- [ ] Configuration loaded from `DANUBE_CONFIG` env var or `/etc/danube/danube.toml`
- [ ] Validation errors provide clear messages
- [ ] Default configuration generated via `danube init-config`
- [ ] **Tests:** Unit tests for valid and invalid configurations
- [ ] **Documentation:** Document minimal configuration in `docs/configuration.md`

**Example danube.toml:**

```toml
[config_repo]
url = "git@github.com:myorg/danube-config.git"
branch = "main"
sync_interval = "60s"
```

---

#### DANUBE-5: Set Up SQLite Database Layer

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 6h
**Depends On:** DANUBE-4

**Description:**
Implement SQLite connection pool and migration system using sqlx. WAL mode is hardcoded.

**Acceptance Criteria:**

- [ ] SQLite database created at `/var/lib/danube/danube.db` **with WAL mode hardcoded to ON**
- [ ] `PRAGMA foreign_keys = ON` enforced
- [ ] Embedded migrations via `sqlx::migrate!`
- [ ] All tables from data model created (users, teams, team_members, pipelines, pipeline_permissions, jobs, steps, secrets, artifacts)
- [ ] Indexes created
- [ ] `danube migrate` subcommand for manual migration
- [ ] Connection pool configured (max 10 connections)
- [ ] **Tests:** Integration test: migrate fresh database, verify schema, verify WAL mode enabled
- [ ] **Documentation:** Document database schema and migration process

**Code to enforce WAL mode:**

```rust
// In database initialization
sqlx::query("PRAGMA journal_mode = WAL")
    .execute(&pool)
    .await?;

// Verify WAL mode is active
let mode: String = sqlx::query_scalar("PRAGMA journal_mode")
    .fetch_one(&pool)
    .await?;
assert_eq!(mode.to_uppercase(), "WAL", "WAL mode must be enabled");
```

---

#### DANUBE-6: Implement Data Access Layer

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 10h
**Depends On:** DANUBE-5

**Description:**
Create repository structs for CRUD operations on all entities.

**Acceptance Criteria:**

- [ ] `TeamRepo`: create, get_by_id, list, update, delete
- [ ] `UserRepo`: create, get_by_oidc_subject, get_by_id, list_by_team
- [ ] `PipelineRepo`: create, get_by_id, list, list_by_team, update, delete
- [ ] `JobRepo`: create, get_by_id, list_by_pipeline, update_status, get_pending
- [ ] `StepRepo`: create, update_status, list_by_job
- [ ] `SecretRepo`: create, get_by_pipeline, delete (used by SecretService)
- [ ] `ArtifactRepo`: create, get_by_job, delete
- [ ] All methods use `sqlx::query_as!` for compile-time verification
- [ ] All queries instrumented with tracing spans
- [ ] **Tests:** Unit tests with in-memory SQLite for all CRUD operations
- [ ] **Documentation:** Document repository API in code comments

---

#### DANUBE-7: Implement Secrets Encryption

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-6

**Description:**
Encrypt secrets at rest using AES-256-GCM. Foundation for SecretService.

**Acceptance Criteria:**

- [ ] Encryption key loaded from `/var/lib/danube/keys/encryption.key`
- [ ] Key auto-generated on first startup if not present (32 random bytes)
- [ ] `encrypt_secret(plaintext: &str) -> Vec<u8>` function
- [ ] `decrypt_secret(ciphertext: &[u8]) -> Result<String>` function
- [ ] Nonce prepended to ciphertext (12 bytes)
- [ ] SecretRepo stores encrypted values, decrypts on read
- [ ] **Tests:** Unit tests: encrypt → decrypt round-trip, test with multiple secrets
- [ ] **Documentation:** Document secret encryption scheme and key management

**Library:** `aes-gcm` crate

---

#### DANUBE-8: Implement SecretService

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 5h
**Depends On:** DANUBE-7

**Description:**
In-memory cache of decrypted secrets for active jobs, served via gRPC.

**Acceptance Criteria:**

- [ ] `SecretService` struct with `DashMap<JobId, HashMap<String, String>>`
- [ ] `load_secrets(job_id: &str, pipeline_id: &str) -> Result<()>` — Load pipeline secrets from DB into cache
- [ ] `get_secret(job_id: &str, key: &str) -> Result<String>` — Retrieve secret for job
- [ ] `clear_secrets(job_id: &str)` — Remove job's secrets from cache
- [ ] Secrets automatically cleared when job completes/fails
- [ ] Metric: `danube_secret_cache_size` (gauge of cached secrets)
- [ ] **Tests:** Unit tests for load/get/clear, test cache isolation between jobs
- [ ] **Documentation:** Document SecretService API and lifecycle

---

#### DANUBE-9: Set Up Axum HTTP Server Skeleton

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-4

**Description:**
Initialize Axum server with basic middleware and routing structure.

**Acceptance Criteria:**

- [ ] Server binds to configured address (from CaC config.yaml, default 0.0.0.0:8080)
- [ ] Request logging middleware (tower-http `TraceLayer`)
- [ ] CORS middleware configured for frontend origin
- [ ] Route groups: `/api/v1/*`, `/health`, `/metrics`, `/` (frontend)
- [ ] Graceful shutdown on SIGTERM/SIGINT
- [ ] Global error handler returning JSON errors
- [ ] `AppState` struct shared via Axum state extractor (contains DB pool, config, SecretService)
- [ ] **Tests:** Integration test: verify server starts and responds to `/health`
- [ ] **Documentation:** Document API structure and middleware chain

---

### DANUBE-E2: Kubernetes Integration

---

#### DANUBE-10: Initialize kube-rs Client

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 4h
**Depends On:** DANUBE-9

**Description:**
Set up Kubernetes client for interacting with K3s.

**Acceptance Criteria:**

- [ ] Client initialized from kubeconfig path `/etc/rancher/k3s/k3s.yaml`
- [ ] Falls back to in-cluster config if kubeconfig missing
- [ ] Namespace `danube-jobs` created if not exists
- [ ] Namespace `danube-system` created for registry/Dex
- [ ] Client wrapped in `Arc` for sharing across tasks
- [ ] Health check verifies K8s API connectivity
- [ ] Metric: `danube_k8s_api_calls_total{operation, status}`
- [ ] **Tests:** Integration test against local K3s cluster
- [ ] **Documentation:** Document K3s setup requirements and permissions

---

#### DANUBE-11: Implement Pod Manifest Builder

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-10

**Description:**
Create a builder for generating Pipeline Pod manifests with Cilium NetworkPolicy.

**Acceptance Criteria:**

- [ ] `PodBuilder` struct with fluent API
- [ ] Sets job_id, pipeline_id as labels
- [ ] Configures Coordinator container (image, resources, env)
- [ ] Configures Worker container (user-specified image, resources, env)
- [ ] Shared `emptyDir` volume for workspace (`/workspace`) with configurable size limit
- [ ] Configures `restartPolicy: Never`
- [ ] **No secrets in env vars** — secrets accessed via SecretService
- [ ] Generates Cilium NetworkPolicy with egress allowlist (from CaC config.yaml)
- [ ] Resource limits configurable per pipeline (from CaC)
- [ ] **Tests:** Unit test: builder produces valid Pod YAML, verify no secrets in env, verify NetworkPolicy
- [ ] **Documentation:** Document Pod structure and security constraints

**Example NetworkPolicy:**

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: job-abc123-egress
spec:
  endpointSelector:
    matchLabels:
      danube.dev/job-id: abc123
  egress:
    - toEndpoints:
        - matchLabels:
            app: danube-master  # Allow gRPC to master
    - toFQDNs:
        - matchName: "registry.npmjs.org"
        - matchName: "pypi.org"
        - matchName: "registry.danube-system"
```

---

#### DANUBE-12: Implement Pod Lifecycle Manager

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-11

**Description:**
Manage Pod creation, monitoring, and deletion.

**Acceptance Criteria:**

- [ ] `create_pod(manifest: Pod) -> Result<()>`
- [ ] `create_network_policy(policy: CiliumNetworkPolicy) -> Result<()>`
- [ ] `watch_pod(job_id: &str) -> impl Stream<Item = PodPhase>`
- [ ] `delete_pod(job_id: &str) -> Result<()>`
- [ ] `delete_network_policy(job_id: &str) -> Result<()>`
- [ ] Pod status changes update Job record in SQLite
- [ ] Timeout handling: kill pods exceeding max duration (from pipeline config)
- [ ] Cleanup: delete pods and policies in terminal state older than 5 minutes
- [ ] Metric: `danube_pod_lifecycle_events_total{event}`
- [ ] **Tests:** Integration test: create pod+policy, wait for Running, delete
- [ ] **Documentation:** Document Pod lifecycle states and timeout behavior

---

#### DANUBE-13: Implement K8s Exec Client

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 8h
**Depends On:** DANUBE-10

**Description:**
Execute shell commands in Worker container via K8s Exec API.

**Acceptance Criteria:**

- [ ] `exec_command(pod: &str, container: &str, command: &[&str]) -> ExecStream`
- [ ] `ExecStream` yields `stdout`, `stderr` as separate byte streams
- [ ] Exit code captured when command completes
- [ ] Timeout support (kill exec after N seconds)
- [ ] Handle SPDY/WebSocket protocol upgrade transparently
- [ ] Backpressure: if consumer is slow, buffer up to 1MB then drop oldest
- [ ] Metric: `danube_exec_commands_total{status}`
- [ ] **Tests:** Integration test: exec `echo hello` in busybox pod, verify output
- [ ] **Documentation:** Document exec API usage and limitations

**Reference:** kube-rs `Api::exec` method with `AttachedProcess`.

---

#### DANUBE-14: Set Up Internal Container Registry

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-10

**Description:**
Deploy Docker Registry v2 to K3s for caching built images.

**Acceptance Criteria:**

- [ ] Registry Deployment manifest with `registry:2` image
- [ ] HostPath volume: `/var/lib/danube/registry`
- [ ] Service: `registry.danube-system:5000`
- [ ] Registry accessible from `danube-jobs` namespace
- [ ] Health check: verify registry responds to `/v2/` endpoint
- [ ] Auto-deploy registry on Danube startup if not present
- [ ] Metric: `danube_registry_health{status}`
- [ ] **Tests:** Integration test: push image, pull image, verify storage
- [ ] **Documentation:** Document registry architecture and access

**Deployment Example:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: registry
  namespace: danube-system
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: registry
          image: registry:2
          volumeMounts:
            - name: storage
              mountPath: /var/lib/registry
      volumes:
        - name: storage
          hostPath:
            path: /var/lib/danube/registry
```

---

#### DANUBE-17: Implement GitHub App Authentication

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 5h
**Depends On:** DANUBE-8

**Description:**
Implement GitHub App authentication with JWT signing and token caching for repository access.

**Acceptance Criteria:**
- [ ] Parse GitHub App private key from SecretService (PEM format)
- [ ] Generate JWT signed with RS256:
  - Algorithm: RS256
  - Payload: `{"iat": <now>, "exp": <now+10min>, "iss": <app_id>}`
- [ ] Call GitHub API: `POST /app/installations/{installation_id}/access_tokens`
  - Headers: `Authorization: Bearer <JWT>`, `Accept: application/vnd.github+json`
  - Response contains access token (expires in 1 hour)
- [ ] Cache tokens in memory with expiry tracking
- [ ] Automatic token refresh when expired
- [ ] Support multiple GitHub Apps (different orgs)
- [ ] Handle API errors gracefully:
  - Rate limiting (exponential backoff)
  - Invalid credentials (clear error message)
  - Network failures (retry with timeout)
- [ ] Metric: `danube_github_app_tokens_generated_total{status}`
- [ ] **Tests:** Unit test JWT generation, integration test with GitHub API (mock)
- [ ] **Documentation:** Document GitHub App setup process in `docs/github-app-setup.md`

**Library:** `jsonwebtoken` crate for JWT signing, `reqwest` for API calls

**Example GitHub App Config (in CaC):**
```yaml
# config.yaml
git_authentication:
  - type: github_app
    name: myorg-app
    app_id: "123456"
    installation_id: "78910"
    private_key_secret: "github-app-myorg-key"
    match_patterns:
      - "github.com/myorg/*"
```

---

### DANUBE-E3: Execution Engine

---

#### DANUBE-20: Define gRPC Protocol

**Status:** 🔴 Todo
**Type:** Task
**Priority:** Highest
**Estimate:** 2h
**Depends On:** DANUBE-1

**Description:**
Define Protobuf schema for Coordinator ↔ Master communication, including SecretService and artifacts.

**Acceptance Criteria:**

- [ ] `danube.proto` in `danube-proto` crate
- [ ] `CoordinatorService` with methods:
  - `RunStep(RunStepRequest) returns (stream StepOutput)`
  - `ReportStatus(StatusReport) returns (Empty)`
  - `GetSecret(GetSecretRequest) returns (GetSecretResponse)`
  - `UploadArtifact(stream ArtifactChunk) returns (UploadArtifactResponse)`
  - `DownloadArtifact(DownloadArtifactRequest) returns (stream ArtifactChunk)`
- [ ] Generated Rust code via `tonic-build`
- [ ] **Tests:** Verify proto compiles and generates valid Rust code
- [ ] **Documentation:** Document gRPC protocol in `docs/grpc-api.md`

**Proto Definition:**

```protobuf
syntax = "proto3";
package danube.v1;

service CoordinatorService {
  rpc RunStep(RunStepRequest) returns (stream StepOutput);
  rpc ReportStatus(StatusReport) returns (Empty);
  rpc GetSecret(GetSecretRequest) returns (GetSecretResponse);
  rpc UploadArtifact(stream ArtifactChunk) returns (UploadArtifactResponse);
  rpc DownloadArtifact(DownloadArtifactRequest) returns (stream ArtifactChunk);
}

message RunStepRequest {
  string job_id = 1;
  string step_name = 2;
  string command = 3;
  map<string, string> env = 4;
  optional uint32 timeout_seconds = 5;
}

message StepOutput {
  oneof output {
    bytes stdout = 1;
    bytes stderr = 2;
    int32 exit_code = 3;
  }
}

message StatusReport {
  string job_id = 1;
  string status = 2;
  optional string error_message = 3;
}

message GetSecretRequest {
  string job_id = 1;
  string key = 2;
}

message GetSecretResponse {
  string value = 1;
}

message ArtifactChunk {
  bytes data = 1;
}

message UploadArtifactResponse {
  string artifact_id = 1;
  uint64 size_bytes = 2;
}

message DownloadArtifactRequest {
  string job_id = 1;
  string artifact_name = 2;
}

message Empty {}
```

---

#### DANUBE-21: Implement gRPC Server

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 8h
**Depends On:** DANUBE-20, DANUBE-13, DANUBE-8

**Description:**
Implement the Master-side gRPC server handling Coordinator requests.

**Acceptance Criteria:**

- [ ] gRPC server on configured port (default 9090)
- [ ] `RunStep` handler:
  - Validates job_id exists and is running
  - Looks up Pod name from job record
  - Calls K8s exec with command
  - Streams stdout/stderr back to caller
  - Sends exit_code as final message
- [ ] `ReportStatus` handler updates job status in SQLite
- [ ] `GetSecret` handler:
  - Validates job_id is active
  - Calls `SecretService.get_secret(job_id, key)`
  - Returns secret value
  - Logs secret access for audit
- [ ] `UploadArtifact` handler:
  - Streams tarball to `/var/lib/danube/artifacts/{job_id}/{name}.tar.gz`
  - Records artifact in database
- [ ] `DownloadArtifact` handler:
  - Streams artifact file to caller
- [ ] Authentication: extract job_id from request, validate against active jobs
- [ ] Concurrent step limit per job (default 1, configurable in CaC)
- [ ] Metrics: `danube_grpc_requests_total{method, status}`
- [ ] **Tests:** Unit tests with mock K8s client and SecretService
- [ ] **Documentation:** Document gRPC server authentication and rate limiting

---

#### DANUBE-22: Build Python Coordinator SDK

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 10h
**Depends On:** DANUBE-20

**Description:**
Create the Python SDK used inside the Coordinator container. Includes secrets and artifacts modules.

**Acceptance Criteria:**

- [ ] Python package: `danube-sdk`
- [ ] gRPC client generated from proto via `grpcio-tools`
- [ ] `step.run(command, name=None, env=None, timeout=None, image=None) -> StepResult`
- [ ] `StepResult` contains `exit_code`, `stdout`, `stderr`
- [ ] `ctx` object with `job_id`, `pipeline_id`, `branch`, `commit_sha`, `trigger_type`
- [ ] `secrets` module with `get(key: str) -> str` — calls gRPC GetSecret
- [ ] `artifacts` module:
  - `upload(path: str, name: str) -> None` — tar directory, upload via gRPC
  - `download(name: str, dest: str) -> None` — download artifact, extract to dest
- [ ] Type stubs (`.pyi`) for IDE autocomplete
- [ ] Bundled in Coordinator image
- [ ] Error handling: clear exceptions if secret not found or gRPC fails
- [ ] **Tests:** Unit tests with mocked gRPC server, test secret access, test artifact upload/download
- [ ] **Documentation:** Full API reference in `docs/python-sdk.md`

**Example Usage:**

```python
from danube import step, ctx, secrets, artifacts

# Run tests
result = step.run("npm test", name="Run Tests")
if result.exit_code != 0:
    raise SystemExit(1)

# Upload coverage report
artifacts.upload("coverage/", name="coverage-report")

# Build image with Kaniko (if on main branch)
if ctx.branch == "main":
    step.run(
        "/kaniko/executor --context=/workspace --dockerfile=Dockerfile "
        "--destination=registry.danube-system:5000/my-app:latest",
        name="Build Image",
        image="gcr.io/kaniko-project/executor:latest"
    )
```

---

#### DANUBE-23: Build Coordinator Container Image

**Status:** 🔴 Todo
**Type:** Task
**Priority:** High
**Estimate:** 2h
**Depends On:** DANUBE-22

**Description:**
Create Dockerfile for Coordinator image.

**Acceptance Criteria:**

- [ ] Base image: `python:3.11-slim`
- [ ] Install `danube-sdk` and `grpcio`
- [ ] Entrypoint: `python /workspace/danubefile.py`
- [ ] Non-root user
- [ ] Image size < 150MB
- [ ] Published to internal registry or external registry
- [ ] **Tests:** Build image, verify entrypoint works with sample `danubefile.py`
- [ ] **Documentation:** Document Coordinator image build and publish process

**Dockerfile:**

```dockerfile
FROM python:3.11-slim

RUN useradd -m danube
WORKDIR /workspace

COPY danube-sdk/ /tmp/danube-sdk
RUN pip install --no-cache-dir /tmp/danube-sdk && rm -rf /tmp/danube-sdk

USER danube
ENTRYPOINT ["python", "danubefile.py"]
```

---

#### DANUBE-24: Implement Git Clone with Multi-Auth Support

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 7h
**Depends On:** DANUBE-8, DANUBE-17

**Description:**
Clone repository into workspace at job start with support for multiple authentication methods (GitHub App, GitLab tokens, SSH keys).

**Acceptance Criteria:**

- [ ] Load `git_authentication` config from CaC (synced to database)
- [ ] Match repository URL against `match_patterns` to select auth method
- [ ] **GitHub App authentication:**
  - Use DANUBE-17 to get installation access token
  - Clone via HTTPS: `https://x-access-token:TOKEN@github.com/org/repo.git`
  - Handle token expiry and refresh
- [ ] **GitLab token authentication:**
  - Load token from SecretService
  - Clone via HTTPS: `https://oauth2:TOKEN@gitlab.com/org/repo.git`
- [ ] **SSH key authentication (fallback):**
  - Generate SSH key pair on first startup: `/var/lib/danube/keys/git_fallback_key`
  - Print public key to logs
  - Clone via SSH: `git@github.com:org/repo.git`
  - Inject SSH key into Coordinator container
- [ ] Shallow clone by default (`--depth=1`)
- [ ] Checkout specific commit SHA
- [ ] Submodule support (optional flag in pipeline config: `clone_submodules: true`)
- [ ] Clear error messages:
  - "No matching git_authentication for repo X"
  - "GitHub App token generation failed: ..."
  - "SSH key not found at ..."
- [ ] Metric: `danube_git_clone_total{auth_method, status}`
- [ ] **Tests:** Test all auth methods with mock Git server, verify credentials not leaked
- [ ] **Documentation:**
  - Document GitHub App setup in `docs/github-app-setup.md`
  - Document GitLab token setup in `docs/gitlab-auth.md`
  - Document SSH key fallback in `docs/git-authentication.md`

**Example CaC Config:**
```yaml
# config.yaml
git_authentication:
  # Primary: GitHub App for main org
  - type: github_app
    name: myorg-app
    app_id: "123456"
    installation_id: "78910"
    private_key_secret: "github-app-myorg-key"
    match_patterns:
      - "github.com/myorg/*"

  # GitLab self-hosted
  - type: gitlab_token
    name: gitlab-internal
    url: "https://gitlab.internal.com"
    token_secret: "gitlab-token"
    match_patterns:
      - "gitlab.internal.com/*"

  # Fallback: SSH key for everything else
  - type: ssh_key
    name: fallback
    private_key_path: "/var/lib/danube/keys/git_fallback_key"
    match_patterns:
      - "*"  # Match all
```

---

#### DANUBE-25: Implement Job Orchestrator

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 10h
**Depends On:** DANUBE-12, DANUBE-21, DANUBE-24

**Description:**
Central component that manages job lifecycle from trigger to completion.

**Acceptance Criteria:**

- [ ] `JobOrchestrator::start_job(pipeline_id, trigger) -> job_id`
- [ ] Loads pipeline config from database (synced from CaC)
- [ ] Clones repository to workspace volume
- [ ] Loads secrets into SecretService for this job
- [ ] Creates Pod + NetworkPolicy via PodBuilder
- [ ] Updates job status to "running"
- [ ] Monitors Pod status via watcher
- [ ] On Pod completion:
  - Updates job status based on Coordinator exit code
  - Deletes Pod and NetworkPolicy
  - Clears secrets from SecretService
  - Sends notification (if configured)
- [ ] On timeout:
  - Logs warning (V1: soft timeout, no kill)
  - Metric: `danube_job_timeouts_total{pipeline}`
- [ ] Concurrent job limit enforced (configurable in CaC)
- [ ] Queue pending jobs if at limit
- [ ] Metrics: `danube_jobs_total{status}`, `danube_job_duration_seconds`
- [ ] **Tests:** Integration test with full job lifecycle
- [ ] **Documentation:** Document job orchestration flow

---

#### DANUBE-26: Implement Kaniko Image Build Support

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-14, DANUBE-22

**Description:**
Enable Kaniko executor in Worker container for building Docker images.

**Acceptance Criteria:**

- [ ] Coordinator SDK supports `image="gcr.io/kaniko-project/executor:latest"` parameter
- [ ] Master auto-injects Docker config for internal registry into Kaniko pods
- [ ] Support `--cache=true --cache-repo=registry.danube-system:5000/{pipeline}/cache`
- [ ] External registry credentials from SecretService (for Docker Hub, etc.)
- [ ] Example pipeline in documentation using Kaniko
- [ ] **Tests:** Integration test: build image with Kaniko, verify pushed to registry
- [ ] **Documentation:** Document Kaniko usage and caching strategy in `docs/kaniko-builds.md`

---

### DANUBE-E4: Configuration as Code

---

#### DANUBE-15: Implement CaC Repository Watcher

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 8h
**Depends On:** DANUBE-4, DANUBE-6

**Description:**
Poll Git repository for configuration changes and sync to database.

**Acceptance Criteria:**

- [ ] SSH key pair generated on startup: `/var/lib/danube/keys/git_deploy_key{,.pub}`
- [ ] Public key printed to logs on first startup
- [ ] Clone CaC repository on startup
- [ ] Poll repository at configured interval (default 60s)
- [ ] On change detected (new commit):
  - Pull latest changes
  - Parse all YAML files (config.yaml, users.yaml, teams.yaml, pipelines/*.yaml)
  - Validate YAML schema
  - Update database with changes (upsert users, teams, pipelines)
  - Update Dex user list (DANUBE-89)
- [ ] Manual sync command: `danube sync-config`
- [ ] Metric: `danube_cac_sync_total{status}`, `danube_cac_sync_last_success`
- [ ] **Tests:** Unit test with mock Git repo, verify sync updates DB
- [ ] **Documentation:** Document CaC repository structure and sync process

---

#### DANUBE-16: Implement CaC YAML Parser

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-15

**Description:**
Parse and validate CaC YAML files.

**Acceptance Criteria:**

- [ ] Deserialize YAML with `serde_yaml`
- [ ] Structs for each resource kind:
  - `CacConfig` (config.yaml)
  - `CacUser` (users.yaml)
  - `CacTeam` (teams.yaml)
  - `CacPipeline` (pipelines/*.yaml)
- [ ] Schema validation with descriptive error messages
- [ ] Support for multiple resources in one file (YAML `---` separator)
- [ ] Detect and report duplicate names
- [ ] Validate references (e.g., team exists for pipeline)
- [ ] **Tests:** Unit tests with valid and invalid YAML samples
- [ ] **Documentation:** Document YAML schema for each resource type in `docs/cac-schema.md`

---

#### DANUBE-30: Implement Pipeline Metadata Extractor

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-22

**Description:**
Extract trigger metadata from danubefile.py without executing build logic.

**Acceptance Criteria:**

- [ ] Spawn sandboxed Python process with timeout (5s)
- [ ] Mock SDK captures decorator arguments
- [ ] Returns JSON: `{name, triggers: [{type, config}]}`
- [ ] Handles syntax errors gracefully (return error, don't crash Master)
- [ ] Handles missing decorators (default: manual trigger only)
- [ ] Resource limits: 128MB memory, 1 CPU
- [ ] **Tests:** Integration test: parse sample pipelines, verify extracted metadata
- [ ] **Documentation:** Document metadata extraction process and limitations

**Flow:**

```
Master → spawn python3 -c "..." → stdout JSON → parse
```

---

#### DANUBE-31: Implement Cron Scheduler

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-30, DANUBE-25

**Description:**
Schedule pipeline executions based on cron expressions from CaC.

**Acceptance Criteria:**

- [ ] Parse cron expressions using `cron` crate
- [ ] Load cron schedules from pipelines in database (from CaC)
- [ ] Background Tokio task checks every minute
- [ ] On match: call `JobOrchestrator::start_job`
- [ ] Persist last run time in SQLite to survive restarts
- [ ] Handle timezone (all cron in UTC)
- [ ] Skip execution if previous run still in progress (configurable)
- [ ] Metric: `danube_cron_triggers_total{pipeline}`
- [ ] **Tests:** Unit test cron scheduling logic
- [ ] **Documentation:** Document cron syntax and timezone handling

---

#### DANUBE-32: Implement Webhook Router

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-30, DANUBE-25

**Description:**
Route incoming Git webhooks to appropriate pipelines.

**Acceptance Criteria:**

- [ ] `POST /api/v1/webhooks/github` endpoint
- [ ] `POST /api/v1/webhooks/gitlab` endpoint
- [ ] `POST /api/v1/webhooks/gitea` endpoint
- [ ] Verify webhook signature (HMAC-SHA256)
- [ ] Parse payload: extract repo URL, branch, commit SHA
- [ ] Match against pipelines by repo URL and branch filter (from CaC)
- [ ] Trigger matching pipelines via `JobOrchestrator::start_job`
- [ ] Return 200 immediately (async processing)
- [ ] Log unmatched webhooks for debugging
- [ ] Metric: `danube_webhook_requests_total{forge, matched}`
- [ ] **Tests:** Unit tests for each webhook type with sample payloads
- [ ] **Documentation:** Document webhook setup for each Git forge

---

#### DANUBE-33: Implement Webhook Signature Verification

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 3h
**Depends On:** DANUBE-32

**Description:**
Verify webhook payloads are authentic.

**Acceptance Criteria:**

- [ ] GitHub: HMAC-SHA256 via `X-Hub-Signature-256`
- [ ] GitLab: Token via `X-Gitlab-Token`
- [ ] Gitea: HMAC-SHA256 via `X-Gitea-Signature`
- [ ] Webhook secret stored per pipeline (in SecretService)
- [ ] Reject requests with invalid/missing signatures
- [ ] Timing-safe comparison for signatures
- [ ] **Tests:** Unit tests with valid/invalid signatures
- [ ] **Documentation:** Document webhook secret configuration in CaC

---

### DANUBE-E5: API & Authentication

---

#### DANUBE-87: Deploy Dex OIDC Provider

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 6h
**Depends On:** DANUBE-10

**Description:**
Deploy Dex as K3s pod for OIDC authentication.

**Acceptance Criteria:**

- [ ] Dex Deployment manifest with `ghcr.io/dexidp/dex:latest` image
- [ ] ConfigMap for Dex configuration (issuer, storage, staticClients)
- [ ] SQLite storage: `/var/lib/danube/dex.db`
- [ ] Service: `dex.danube-system:5556`
- [ ] Static client configured: `danube` (client ID and secret)
- [ ] Enable password database for static users
- [ ] Auto-deploy Dex on Danube startup if not present
- [ ] Health check: verify Dex responds to `/.well-known/openid-configuration`
- [ ] **Tests:** Integration test: verify Dex deployment, query OIDC config
- [ ] **Documentation:** Document Dex architecture and configuration

**Dex ConfigMap:**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dex-config
  namespace: danube-system
data:
  config.yaml: |
    issuer: http://dex.danube-system:5556/dex
    storage:
      type: sqlite3
      config:
        file: /var/lib/dex/dex.db
    web:
      http: 0.0.0.0:5556
    staticClients:
      - id: danube
        redirectURIs:
          - 'http://localhost:8080/auth/callback'
        name: 'Danube CI/CD'
        secret: <generated-on-install>
    enablePasswordDB: true
```

---

#### DANUBE-88: Integrate Danube with Dex OIDC

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 6h
**Depends On:** DANUBE-87, DANUBE-9

**Description:**
Implement OIDC login flow with Dex.

**Acceptance Criteria:**

- [ ] `GET /auth/login` redirects to Dex authorization endpoint
- [ ] `GET /auth/callback` handles OIDC code exchange
- [ ] Validate ID token signature and claims
- [ ] Extract `sub` (subject) and `email` from token
- [ ] Create/update user in SQLite from OIDC subject
- [ ] Issue session cookie (HTTP-only, Secure, SameSite=Strict)
- [ ] Session stored in SQLite with expiry (24 hours default)
- [ ] `GET /auth/logout` clears session and redirects
- [ ] Middleware extracts user from session cookie
- [ ] Protected routes return 401 if no valid session
- [ ] Metric: `danube_auth_events_total{event}`
- [ ] **Tests:** Integration test with mock OIDC provider
- [ ] **Documentation:** Document OIDC login flow and session management

**Library:** `openidconnect` crate

---

#### DANUBE-89: Sync CaC Users to Dex

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-88, DANUBE-15

**Description:**
Sync users from CaC repository to Dex static password list.

**Acceptance Criteria:**

- [ ] When CaC sync detects users.yaml changes:
  - Parse user definitions (email, password_hash)
  - Update Dex static password config
  - Trigger Dex config reload (via API or pod restart)
- [ ] Support bcrypt password hashes
- [ ] Validate password hashes before writing to Dex
- [ ] Log user additions/removals
- [ ] **Tests:** Unit test user sync logic
- [ ] **Documentation:** Document user management via CaC in `docs/user-management.md`

---

#### DANUBE-44: Implement Team-Based RBAC Data Model

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-6, DANUBE-89

**Description:**
Implement team and permission checking logic.

**Acceptance Criteria:**

- [ ] `has_permission(user_id, pipeline_id, permission) -> bool` function
- [ ] Permission levels: `read`, `write`, `admin`
- [ ] Permission inheritance:
  - `admin` implies `write` and `read`
  - `write` implies `read`
- [ ] Global admin teams (from CaC) have access to all pipelines
- [ ] Efficient permission caching (reload on CaC sync)
- [ ] **Tests:** Unit tests for permission checks with various scenarios
- [ ] **Documentation:** Document RBAC model in `docs/rbac.md`

**Permission Hierarchy:**

```
read: View pipeline config, view jobs, view logs
write: read + trigger jobs, cancel jobs, download artifacts
admin: write + edit pipeline (via CaC), manage secrets
```

---

#### DANUBE-45: Implement Permission Checking Middleware

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-44

**Description:**
Axum middleware to enforce RBAC on API endpoints.

**Acceptance Criteria:**

- [ ] Middleware extracts user from session
- [ ] Extracts pipeline_id from request path (if applicable)
- [ ] Calls `has_permission(user, pipeline, required_permission)`
- [ ] Returns 403 Forbidden if permission denied
- [ ] Logs permission denials for audit
- [ ] Applied to all protected API routes
- [ ] **Tests:** Integration tests for protected endpoints with different permission levels
- [ ] **Documentation:** Document protected endpoints and required permissions

---

#### DANUBE-41: Implement REST API - Pipelines (Read-Only)

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 3h
**Depends On:** DANUBE-6, DANUBE-45

**Description:**
Read-only endpoints for pipeline management (CaC is source of truth).

**Acceptance Criteria:**

- [ ] `GET /api/v1/pipelines` — List all pipelines (filtered by user permissions)
- [ ] `GET /api/v1/pipelines/:id` — Get pipeline details (requires `read` permission)
- [ ] `POST /api/v1/pipelines/:id/trigger` — Manual trigger (requires `write` permission)
- [ ] All endpoints require authentication
- [ ] Pagination for list endpoint
- [ ] **Tests:** Integration tests for all endpoints with RBAC scenarios
- [ ] **Documentation:** OpenAPI spec for pipeline endpoints

**Note:** No POST/PUT/DELETE for pipelines - editing happens via CaC repository.

---

#### DANUBE-42: Implement REST API - Jobs

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-6, DANUBE-45

**Description:**
Endpoints for job history and management.

**Acceptance Criteria:**

- [ ] `GET /api/v1/jobs` — List jobs (filterable by pipeline, status, requires `read` on pipeline)
- [ ] `GET /api/v1/jobs/:id` — Get job details with steps (requires `read`)
- [ ] `POST /api/v1/jobs/:id/cancel` — Cancel running job (requires `write`)
- [ ] `GET /api/v1/jobs/:id/logs` — Get full log file content (requires `read`)
- [ ] `GET /api/v1/jobs/:id/logs/stream` — SSE stream for live logs (requires `read`)
- [ ] `GET /api/v1/jobs/:id/artifacts` — List artifacts (requires `read`)
- [ ] `GET /api/v1/jobs/:id/artifacts/:name` — Download artifact (requires `write` - per requirements)
- [ ] Pagination and date range filtering
- [ ] **Tests:** Integration tests for all endpoints
- [ ] **Documentation:** OpenAPI spec for job endpoints

---

#### DANUBE-43: Implement REST API - Secrets (Read-Only Keys)

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 3h
**Depends On:** DANUBE-7, DANUBE-45

**Description:**
Endpoints for secret management (CaC is source of truth for values).

**Acceptance Criteria:**

- [ ] `GET /api/v1/secrets` — List secret keys (not values, requires `admin` on any pipeline)
- [ ] `GET /api/v1/pipelines/:id/secrets` — List secrets for pipeline (requires `admin`)
- [ ] Values never returned in API responses
- [ ] Audit log entry on secret access
- [ ] **Tests:** Integration tests for all endpoints
- [ ] **Documentation:** OpenAPI spec for secret endpoints

**Note:** Secret creation/deletion happens via CaC repository, not API.

---

### DANUBE-E6: Log Management

---

#### DANUBE-50: Implement Log Writer

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 4h
**Depends On:** DANUBE-21

**Description:**
Stream logs from K8s exec to disk files.

**Acceptance Criteria:**

- [ ] Each job has a single log file: `/var/lib/danube/logs/<job_id>.log`
- [ ] Stdout and stderr interleaved with timestamps
- [ ] Format: `[2024-01-06T12:00:00Z] [step-name] [stdout] line content`
- [ ] Flush after each line (no buffering delays)
- [ ] Track byte offsets for each step (store in `steps` table)
- [ ] Handle concurrent writes from multiple steps (mutex or actor pattern)
- [ ] File rotation: none (reaper handles deletion)
- [ ] **Tests:** Unit test log formatting and offset tracking
- [ ] **Documentation:** Document log format and storage

---

#### DANUBE-51: Implement Log Streaming API

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-50

**Description:**
Real-time log streaming to UI via Server-Sent Events.

**Acceptance Criteria:**

- [ ] `GET /api/v1/jobs/:id/logs/stream` returns `text/event-stream`
- [ ] Requires `read` permission on pipeline
- [ ] On connection: send existing log content
- [ ] Tail file for new content, push to client
- [ ] Handle job completion: send final event and close stream
- [ ] Heartbeat every 15s to keep connection alive
- [ ] Support multiple concurrent clients per job
- [ ] Backpressure: if client is slow, skip intermediate chunks
- [ ] **Tests:** Integration test: connect to stream, verify events received
- [ ] **Documentation:** Document SSE log streaming protocol

---

#### DANUBE-52: Implement Reaper (Garbage Collector)

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-6, DANUBE-50

**Description:**
Background task to delete expired logs and artifacts.

**Acceptance Criteria:**

- [ ] Runs every hour (configurable in CaC config.yaml)
- [ ] Deletes jobs older than `retention.logs_days`
- [ ] Deletes associated log files
- [ ] Deletes associated artifact directories
- [ ] Deletes orphaned step records (cascade)
- [ ] Logs deletion summary (X jobs, Y MB freed)
- [ ] Dry-run mode for testing: `danube reaper --dry-run`
- [ ] Metric: `danube_reaper_deleted_bytes_total`
- [ ] **Tests:** Unit test reaper logic with mock filesystem
- [ ] **Documentation:** Document retention policy configuration

---

#### DANUBE-53: Reaper - Registry Image Garbage Collection

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Medium
**Estimate:** 3h
**Depends On:** DANUBE-14, DANUBE-52

**Description:**
Extend Reaper to clean up old container images from internal registry.

**Acceptance Criteria:**

- [ ] Delete images older than `retention.registry_images_days` (from CaC)
- [ ] Delete unreferenced layers (dangling)
- [ ] Call registry API: `DELETE /v2/<name>/manifests/<reference>`
- [ ] Run registry garbage collection: `registry garbage-collect /etc/docker/registry/config.yml`
- [ ] Logs space freed
- [ ] Metric: `danube_registry_images_deleted_total`
- [ ] **Tests:** Integration test with mock registry
- [ ] **Documentation:** Document registry cleanup process

---

#### DANUBE-90: Generate Signing Keys

**Status:** 🔴 Todo
**Type:** Task
**Priority:** High
**Estimate:** 2h
**Depends On:** DANUBE-1

**Description:**
Generate Ed25519 key pair for signing artifacts and provenance documents.

**Acceptance Criteria:**

- [ ] Generate Ed25519 key pair on first startup if not exists
- [ ] Store private key: `/var/lib/danube/keys/signing.key` (permissions 0600)
- [ ] Store public key: `/var/lib/danube/keys/signing.key.pub` (permissions 0644)
- [ ] Print public key to logs on first startup for user verification
- [ ] Verify key exists and is readable on subsequent startups
- [ ] Error if key is corrupted or has wrong permissions
- [ ] **Tests:** Unit test key generation, verify format and permissions
- [ ] **Documentation:** Document signing key management and rotation procedure in `docs/security.md`

**Library:** `ed25519-dalek` crate

---

#### DANUBE-91: Generate SLSA Provenance

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-90, DANUBE-25

**Description:**
Generate signed SLSA provenance documents for completed jobs.

**Acceptance Criteria:**

- [ ] After job completes successfully, generate in-toto provenance JSON
- [ ] Follow SLSA provenance v0.2 schema
- [ ] Include builder metadata:
  - Builder ID (Danube instance URL)
  - Danube version
  - Job ID and pipeline ID
- [ ] Include materials (sources):
  - Git repository URL
  - Commit SHA
  - Branch name
- [ ] Include recipe:
  - danubefile.py content hash (SHA256)
  - Entry point path
- [ ] Include subject (outputs):
  - Artifact names and SHA256 checksums
- [ ] Sign provenance with Ed25519 private key
- [ ] Store provenance: `/var/lib/danube/artifacts/<job_id>/provenance.json`
- [ ] Metric: `danube_provenance_generated_total{status}`
- [ ] **Tests:** Unit test provenance generation, verify JSON schema, verify signature
- [ ] **Documentation:** Document provenance format and SLSA compliance in `docs/slsa-compliance.md`

**Example Provenance:**

```json
{
  "_type": "https://in-toto.io/Statement/v0.1",
  "subject": [
    {"name": "dist.tar.gz", "digest": {"sha256": "abc123..."}}
  ],
  "predicateType": "https://slsa.dev/provenance/v0.2",
  "predicate": {
    "builder": {"id": "https://danube.example.com"},
    "buildType": "https://danube.dev/PipelineBuild/v1",
    "materials": [
      {"uri": "git+https://github.com/myorg/app@main",
       "digest": {"sha1": "def456..."}}
    ],
    "recipe": {
      "type": "https://danube.dev/DanubeFile/v1",
      "definedInMaterial": 0,
      "entryPoint": "danubefile.py"
    }
  }
}
```

---

#### DANUBE-92: Attach Signatures to Artifacts

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 3h
**Depends On:** DANUBE-90, DANUBE-27

**Description:**
Sign artifacts with Ed25519 key when uploaded.

**Acceptance Criteria:**

- [ ] When artifact uploaded via `UploadArtifact` gRPC:
  - Calculate SHA256 checksum of artifact
  - Sign checksum with Ed25519 private key
  - Store signature: `/var/lib/danube/artifacts/<job_id>/<name>.sig`
- [ ] Signature format: raw Ed25519 signature (64 bytes)
- [ ] Include artifact checksums and signatures in provenance document
- [ ] `GET /api/v1/jobs/:id/artifacts/:name.sig` endpoint to download signature
- [ ] **Tests:** Unit test artifact signing, verify signature validity
- [ ] **Documentation:** Document artifact signature format

---

#### DANUBE-93: Artifact Verification Tool

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Medium
**Estimate:** 4h
**Depends On:** DANUBE-91, DANUBE-92

**Description:**
CLI tool to verify artifact signatures and provenance.

**Acceptance Criteria:**

- [ ] `danube verify-artifact <path>` command
- [ ] Download artifact, signature, and provenance from Danube API
- [ ] Verify Ed25519 signature against public key
- [ ] Verify SHA256 checksum matches provenance
- [ ] Display verification results:
  - ✓ Signature valid
  - ✓ Checksum matches
  - Builder info (Danube URL, version)
  - Source info (repo, commit, branch)
- [ ] Exit code: 0 (valid) or 1 (invalid/error)
- [ ] Support offline verification with local provenance file
- [ ] `--public-key <path>` option to use custom public key
- [ ] **Tests:** Integration test with real artifacts, test failure cases
- [ ] **Documentation:** Document verification workflow in `docs/artifact-verification.md`

**Example Output:**

```
$ danube verify-artifact dist.tar.gz
✓ Signature valid (signed by danube.example.com)
✓ Checksum matches provenance (sha256: abc123...)
✓ Built from github.com/myorg/app @ commit def456
✓ Branch: main
✓ Builder: Danube v1.0.0
✓ SLSA Provenance Level 3
```

---

### DANUBE-E7: Frontend

---

#### DANUBE-60: Initialize SolidJS Project

**Status:** 🔴 Todo
**Type:** Task
**Priority:** High
**Estimate:** 2h
**Depends On:** None

**Description:**
Set up SolidJS SPA with Vite.

**Acceptance Criteria:**

- [ ] Vite + SolidJS + TypeScript configured
- [ ] TailwindCSS for styling
- [ ] ESLint + Prettier configured
- [ ] Folder structure: `components/`, `pages/`, `api/`, `stores/`
- [ ] Build outputs to `dist/`
- [ ] Dev proxy to Rust backend (vite.config.ts)
- [ ] **Tests:** Verify build succeeds
- [ ] **Documentation:** Document frontend dev setup in `docs/frontend-dev.md`

---

#### DANUBE-61: Implement App Shell & Routing

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 4h
**Depends On:** DANUBE-60

**Description:**
Create base layout with navigation and client-side routing.

**Acceptance Criteria:**

- [ ] Sidebar with navigation: Pipelines, Jobs
- [ ] Header with user info and logout
- [ ] Router: `@solidjs/router`
- [ ] Routes: `/`, `/pipelines`, `/pipelines/:id`, `/jobs/:id`
- [ ] 404 page
- [ ] Skeleton loaders for all data-dependent views
- [ ] **Tests:** Component tests for navigation
- [ ] **Documentation:** Document routing structure

---

#### DANUBE-62: Implement Pipeline List & Detail Pages (Read-Only)

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 5h
**Depends On:** DANUBE-61, DANUBE-41

**Description:**
UI for viewing pipelines (read-only, editing via CaC).

**Acceptance Criteria:**

- [ ] Pipeline list with status indicators (last job status)
- [ ] Search/filter by name and team
- [ ] Pipeline detail page with:
  - Configuration summary (from CaC)
  - Recent jobs list
  - Manual trigger button (if user has `write` permission)
  - Link to CaC repository for editing
- [ ] No create/edit/delete buttons (CaC only)
- [ ] **Tests:** Component tests for pipeline list and detail
- [ ] **Documentation:** Document pipeline UI components

---

#### DANUBE-63: Implement Job Detail & Log Viewer

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Highest
**Estimate:** 8h
**Depends On:** DANUBE-61, DANUBE-42, DANUBE-51

**Description:**
Real-time job execution view with log streaming.

**Acceptance Criteria:**

- [ ] Job header: status, duration, trigger info
- [ ] Step list with status indicators and duration
- [ ] Log viewer:
  - Monospace font, dark background
  - ANSI color code rendering
  - Auto-scroll (toggleable)
  - Search within logs
  - Line numbers
- [ ] Live updates via SSE (`EventSource`)
- [ ] Cancel button for running jobs (if user has `write`)
- [ ] Artifact download links (if user has `write`)
- [ ] Performance: handle 100k+ log lines without lag (virtualized list)
- [ ] **Tests:** Component tests for log viewer
- [ ] **Documentation:** Document log viewer features

**Library:** Consider `@tanstack/solid-virtual` for virtualization.

---

#### DANUBE-65: Embed Frontend in Rust Binary

**Status:** 🔴 Todo
**Type:** Task
**Priority:** High
**Estimate:** 2h
**Depends On:** DANUBE-9, DANUBE-61

**Description:**
Compile frontend into Rust binary using rust-embed.

**Acceptance Criteria:**

- [ ] `rust-embed` crate configured with `folder = "../frontend/dist"`
- [ ] Axum fallback handler serves embedded files
- [ ] SPA routing: all non-API routes return `index.html`
- [ ] Correct MIME types for all assets
- [ ] Gzip compression for responses (tower-http)
- [ ] Cache headers: immutable for hashed assets, no-cache for index.html
- [ ] Build script: `npm run build` before `cargo build --release`
- [ ] **Tests:** Integration test: verify frontend assets served correctly
- [ ] **Documentation:** Document frontend build and embedding process

---

### DANUBE-E8: Integrations

---

#### DANUBE-70: Implement Notification Sender

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Medium
**Estimate:** 4h
**Depends On:** DANUBE-25

**Description:**
Send notifications on job completion (configured in CaC).

**Acceptance Criteria:**

- [ ] HTTP webhook: POST JSON payload to configured URL (from CaC)
- [ ] Slack/Discord: formatted message with job status, link
- [ ] SMTP email: configurable recipients, HTML template
- [ ] Notification triggers: on_failure, on_success, always (from pipeline CaC config)
- [ ] Retry with backoff on transient failures (3 attempts)
- [ ] Timeout: 10s per request
- [ ] **Tests:** Unit tests with mock HTTP server
- [ ] **Documentation:** Document notification configuration in CaC

---

### DANUBE-E9: Installer & Packaging

---

#### DANUBE-80: Implement Health Check Endpoints

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 3h
**Depends On:** DANUBE-9, DANUBE-10, DANUBE-87, DANUBE-14

**Description:**
Endpoints for liveness and readiness probes.

**Acceptance Criteria:**

- [ ] `GET /health` — Returns 200 if HTTP server is responsive
- [ ] `GET /health/ready` — Returns 200 if all subsystems healthy:
  - SQLite connection works
  - K8s API is reachable
  - Dex pod running
  - Registry pod running
  - CaC sync successful within last 5 minutes
- [ ] Returns 503 with JSON error details on failure
- [ ] No authentication required
- [ ] **Tests:** Integration tests for health endpoints
- [ ] **Documentation:** Document health check endpoints

---

#### DANUBE-86: Implement Periodic Health Checks + Self-Healing

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 6h
**Depends On:** DANUBE-80

**Description:**
Background health checks with automatic remediation.

**Acceptance Criteria:**

- [ ] Run health checks every 5 minutes
- [ ] Check all subsystems:
  - Database integrity (`PRAGMA integrity_check`)
  - K8s API connectivity
  - Dex pod status
  - Registry pod status
  - CaC sync staleness
  - Disk space (warn if <10% free)
- [ ] Self-healing actions:
  - Restart failed Dex pod
  - Restart failed Registry pod
  - Retry failed CaC sync
  - Clear database locks
- [ ] Log all health check results and remediation actions
- [ ] Metrics: `danube_health_check_total{subsystem, status}`, `danube_self_healing_actions_total{action}`
- [ ] **Tests:** Unit tests for health checks and self-healing logic
- [ ] **Documentation:** Document health monitoring and self-healing

---

#### DANUBE-85: Implement Job Draining for Upgrades

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 5h
**Depends On:** DANUBE-25

**Description:**
Gracefully drain running jobs before upgrade.

**Acceptance Criteria:**

- [ ] `danube drain` command enters draining mode
- [ ] Draining mode:
  - Reject new webhook triggers (return 503)
  - Skip cron triggers
  - Wait for running jobs to complete
  - Timeout after configured duration (default 3600s = 1 hour)
- [ ] If jobs still running at timeout:
  - Prompt user: [force-kill and continue] or [abort drain]
  - If force-kill: terminate pods, mark jobs as `cancelled`
- [ ] `danube drain --status` shows draining progress
- [ ] `danube drain --abort` cancels draining mode
- [ ] **Tests:** Integration test: start jobs, drain, verify no new jobs accepted
- [ ] **Documentation:** Document upgrade procedure with draining in `docs/upgrade-guide.md`

---

#### DANUBE-81: Create Release Binary Build (Self-Hosted)

**Status:** 🔴 Todo
**Type:** Task
**Priority:** High
**Estimate:** 6h
**Depends On:** All implementation tickets

**Description:**
Create self-hosted pipeline to build Danube release binaries.

**Acceptance Criteria:**

- [ ] **Self-hosted pipeline:** Danube builds itself using its own CI/CD system
- [ ] Pipeline defined in CaC repository: `pipelines/release.yaml`
- [ ] Targets: `x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`
- [ ] Static linking with musl for portability
- [ ] Strip symbols, LTO enabled
- [ ] Binary size < 50MB
- [ ] SHA256 checksums published
- [ ] Binaries uploaded to GitHub Releases
- [ ] **Tests:** Smoke test release binary on clean system
- [ ] **Documentation:** Document release build process in `docs/release-process.md`

**Cargo Profile:**

```toml
[profile.release]
lto = true
codegen-units = 1
strip = true
panic = "abort"
```

---

#### DANUBE-82: Create Installer Script with Cilium

**Status:** 🔴 Todo
**Type:** Story
**Priority:** High
**Estimate:** 10h
**Depends On:** DANUBE-81

**Description:**
One-line installer for fresh servers with K3s + Cilium.

**Acceptance Criteria:**

- [ ] `curl -fsSL https://get.danube.dev | sudo sh`
- [ ] Detect OS and architecture
- [ ] Download correct binary
- [ ] Install K3s with Flannel disabled:

  ```bash
  curl -sfL https://get.k3s.io | sh -s - \
    --flannel-backend=none \
    --disable-network-policy
  ```

- [ ] Install Cilium CNI:

  ```bash
  cilium install
  ```

- [ ] Verify Cilium installation
- [ ] Create `/var/lib/danube` directory structure
- [ ] Generate encryption key
- [ ] Generate SSH deploy key for CaC repo, print public key
- [ ] Create minimal `/etc/danube/danube.toml`
- [ ] Create systemd service file
- [ ] Enable and start service
- [ ] Print success message with:
  - Access URL
  - SSH public key (to add to CaC repo)
  - Next steps
- [ ] Idempotent: safe to run multiple times
- [ ] **Tests:** Test installer in Docker containers for each supported OS
- [ ] **Documentation:** Document installation process and requirements in `docs/installation.md`

---

#### DANUBE-83: Create Docker Image

**Status:** 🔴 Todo
**Type:** Task
**Priority:** Medium
**Estimate:** 3h
**Depends On:** DANUBE-81

**Description:**
Container image for Docker/Kubernetes deployment.

**Acceptance Criteria:**

- [ ] Multi-stage Dockerfile (build + runtime)
- [ ] Runtime: `gcr.io/distroless/static` or `alpine`
- [ ] Exposes ports 8080 (HTTP), 9090 (gRPC)
- [ ] Volume mount point: `/var/lib/danube`
- [ ] Entrypoint: `/danube serve`
- [ ] Published to Docker Hub and GitHub Container Registry
- [ ] Tags: `latest`, `vX.Y.Z`, `vX.Y`, `vX`
- [ ] **Tests:** Run container, verify health checks pass
- [ ] **Documentation:** Document Docker deployment in `docs/docker-deployment.md`

---

#### DANUBE-84: Set Up Prometheus for Danube Deployment

**Status:** 🔴 Todo
**Type:** Story
**Priority:** Medium
**Estimate:** 4h
**Depends On:** DANUBE-3, DANUBE-81

**Description:**
Configure Prometheus to scrape metrics from Danube when deployed in Kubernetes.

**Acceptance Criteria:**

- [ ] Prometheus ServiceMonitor CRD for Danube
- [ ] Scrape `/metrics` endpoint
- [ ] Dashboard: Grafana dashboard JSON for Danube metrics
- [ ] Alerts: Basic alerts (disk full, pod failures, high error rate, CaC sync failures)
- [ ] **Tests:** Deploy Danube + Prometheus stack, verify metrics collected
- [ ] **Documentation:** Document Prometheus setup and dashboard import in `docs/observability.md`

---
