# System Components

## Master Process Components

### FastAPI HTTP Server

**Responsibility**: REST API endpoints, webhook ingestion, static file serving, log streaming.

**Technology**: FastAPI, Uvicorn, Pydantic v2.

**Endpoints**:

- `/api/v1/pipelines` - Pipeline management
- `POST /api/v1/pipelines/{id}/run` - Manually trigger a run; creates a `pending` job, runs it in the background via the `JobManager`, returns the job (unknown pipeline → 404)
- `/api/v1/jobs` - Job listing and control
- `POST /api/v1/jobs/{id}/cancel` - Request cancellation of a `running` job (unknown → 404; not running → 409)
- `GET /api/v1/jobs/{id}/logs/stream` - Server-Sent Events log stream: replays the existing log file, then tails new lines until the job is terminal, closing with an `event: end` frame
- `/api/v1/artifacts` - Artifact download
- `POST /webhooks/github`, `POST /webhooks/gitlab` - Git webhook ingestion: verify the provider signature/token, parse push/PR events, resolve matching pipeline(s), and enqueue runs through the shared `JobManager` trigger path with `TriggerType.WEBHOOK` and a `branch/sha` `trigger_ref`. A failed signature/token is rejected `401` and enqueues nothing; a payload malformed for its event type is `400`; a valid event matching no pipeline (or an event type Danube ignores, e.g. a ping or tag push) is a clean `200` no-op. Webhook triggers dedup against concurrent triggers via the same path.
- `/health`, `/health/ready` - Health checks
- `/metrics` - Prometheus metrics
- `/` - Frontend SPA

The control-plane routes (`run`, `cancel`, `logs/stream`) are mounted only when the app is constructed with a `JobManager`; the read-only app omits them. The webhook routes are mounted only when the app additionally receives a `WebhookConfig` carrying the per-provider secrets (GitHub HMAC secret, GitLab token); a provider whose secret is unset rejects every request.

### Internal RPC Control Plane

**Responsibility**: Coordinator ↔ Master communication.

**Protocol**: HTTP/JSON, with a stable schema that can later be carried over another transport if needed.

**Endpoints**:

- `POST /rpc/run-step` - Execute command in Worker container
- `POST /rpc/get-secret` - Retrieve decrypted secret
- `POST /rpc/upload-artifact` - Upload build artifact
- `POST /rpc/report-status` - Update job or step status

### Scheduler

**Responsibility**: Trigger pipelines from cron expressions, webhooks, and manual requests.

**Technology**: asyncio tasks, croniter.

**Behavior**:

- The single trigger/enqueue path for every source (cron, manual, webhook); each
  trigger records its `TriggerType` on the created job.
- Cron schedules evaluated every 60 seconds against the current minute
  (`run_cron(now)` takes the clock, so evaluation is deterministic under test). A
  matching pipeline enqueues exactly one run per minute.
- Webhook events queued and processed immediately
- Deduplication for concurrent triggers on the same pipeline/ref: at most one
  active job (`pending`/`scheduling`/`running`) exists per pipeline/ref, so a
  concurrent trigger collapses onto the in-flight job.
- Enforces a conservative global concurrency cap before handing a job to the
  orchestrator; triggers beyond the cap stay `pending` and are dispatched as
  running jobs finish (`execution-model.md`, Concurrency).

### Reaper

**Responsibility**: Garbage collection for logs, artifacts, workspaces, cached images, and stale runner state.

**Technology**: asyncio interval task.

**Policies**:

- Delete logs older than `retention.logs_days`
- Delete artifacts older than `retention.artifacts_days`
- Delete cached images older than `retention.registry_images_days`
- Remove abandoned job workspaces and containers
- Runs daily at 03:00 UTC by default

### Blueprint Syncer

**Responsibility**: Poll Blueprint Git repository for configuration changes and sync accepted changes into SQLite.

**Technology**: GitPython or equivalent Git integration, asyncio interval task, JSON Schema validation.

**Behavior**:

- Polls every `config_repo.sync_interval` unless manually triggered
- Parses JSON files in the Blueprint repo
- Validates schema and cross-references
- Applies changes transactionally
- Failed syncs leave the current active configuration untouched
- Logs diffs of applied changes

### Master Core

**Responsibility**: Orchestrate pipeline execution and own job lifecycle state.

**State Machine**:

```text
pending → scheduling → running → [success | failure | timeout | cancelled]
```

**Operations**:

- Create job records
- Ask runner to create job containers and workspace
- Start the Coordinator and await its exit under the job timeout (the Coordinator drives the pipeline over RPC; the Master does not run steps itself)
- Validate Coordinator RPC requests
- Execute Worker commands through the runner
- Stream logs to disk and clients
- Track step and job status
- Store artifacts and provenance
- Stop and clean up jobs on completion, timeout, cancellation, or crash

### Job Manager

**Responsibility**: Supervise background job execution for the control plane.

**Technology**: anyio task group (as an async context manager).

**Behavior**:

- The control plane's façade over the `Scheduler`: triggering a run delegates to the scheduler's shared enqueue path, so a manual HTTP trigger gets the same deduplication and global concurrency cap as cron- and webhook-sourced runs.
- The scheduler owns the task group that runs triggered jobs in the background (and the cron loop) so a trigger HTTP request returns immediately while the job runs to completion.
- The task group is owned by the scheduler (not in FastAPI's lifespan) so the control plane behaves identically under `httpx.ASGITransport` (which skips lifespan) and uvicorn.
- Delegates cancel and job/log-path lookups straight to the `JobOrchestrator`.

### Runner Interface

**Responsibility**: Abstract container execution from Master logic.

**Initial implementation**: `LocalContainerRunner`.

**Future-compatible implementations** may include remote runner agents or other execution backends, but the initial product targets one host.

**Core operations**:

- Create per-job workspace
- Pull or validate required images
- Create one Podman pod per job
- Start Coordinator and Worker containers inside that pod
- Await the Coordinator's exit and report its exit code to the Master
- Exec commands in Worker
- Stream stdout/stderr
- Stop containers
- Clean up containers, networks, temporary volumes, and workspace state
- Report runtime status and failures

### Local Container Runner

**Responsibility**: Run jobs on the appliance host using rootless Podman.

**Runtime**: Podman, rootless mode, integrated through the Podman API. The initial implementation creates one Podman pod per Danube job.

**Security responsibilities**:

- Run containers without privileged mode
- Avoid host network, host PID, and host IPC
- Mount only the per-job workspace and required read-only assets
- Apply CPU, memory, and process limits
- Drop unnecessary Linux capabilities
- Apply seccomp/AppArmor where supported
- Attach job containers to a controlled per-job network
- Enforce cleanup even after failure paths

### Log Writer

**Responsibility**: Stream stdout/stderr to disk and live clients.

**Technology**: aiofiles, asyncio.

**Format**:

- Plain text, one line per output record
- File path: `/var/lib/danube/logs/<job_id>.log`
- Append-only during job execution
- Retention handled by Reaper

### SecretService

**Responsibility**: In-memory cache of decrypted secrets, served to Coordinator through RPC.

**Technology**: Python dict/cache, cryptography library using AES-256-GCM.

**Lifecycle**:

1. Job starts → Master loads authorized secrets from SQLite
2. Master decrypts using `/var/lib/danube/keys/encryption.key`
3. Secrets cached in memory by `(job_id, secret_key)`
4. Coordinator requests secret via RPC
5. Master validates job is active and authorized
6. Master returns secret value
7. Cache cleared when job ends

## External / Local Services

### Rootless Podman

**Responsibility**: Pull images, create pods/containers, apply kernel isolation, run exec commands, and expose logs/events to Danube through the Podman API.

Danube should not implement low-level container isolation itself. It drives rootless Podman through the local runner adapter.

### Identity Provider / Auth

**Responsibility**: Authenticate UI/API users.

Danube may run an embedded identity provider or integrate with an existing OIDC provider. Authenticated users are mapped to Blueprint-defined users and teams.

### Local Registry / Image Cache

**Responsibility**: Store built container images and cache layers for repeat builds.

**Storage**: `/var/lib/danube/registry` or runtime-specific image cache.

### SQLite

**Responsibility**: Persistent metadata store.

**Configuration**: WAL mode enabled.

**Schema**: See [Data Model](./data-model.md).

**Access**: `snekql` (async typed query builder/runtime over the `aiosqlite` backend extra). Tables are declared as `snekql` `Model` classes; no raw-SQL data access or ORM layer.

## Job Containers

### Coordinator Container

**Image**: Danube-provided Python image.

**Contains**:

- Danube Python SDK
- RPC client
- User's `danubefile.py`

**Does not contain** build tools by default.

**Lifecycle**:

1. Container starts
2. SDK imports `danubefile.py`
3. Pipeline function executes
4. Each `step.run()` calls Master RPC
5. Coordinator exits when pipeline completes or fails

### Worker Container

**Image**: User-defined build image, such as `node:20`, `python:3.14`, or a build-tool image.

**Purpose**: Execute shell commands and produce outputs.

**Access**:

- Shared `/workspace` volume
- Danube-controlled network path
- No direct Coordinator control path

**Lifecycle**:

- Container starts and idles
- Master execs step commands through the local runner
- Commands run in `/workspace`
- Container deleted when job ends

## Technology Stack Summary

| Layer | Technology |
|-------|------------|
| Language | Python 3.14+ |
| HTTP Framework | FastAPI + Uvicorn |
| Async Runtime | asyncio |
| Container Runner | Rootless Podman via Podman API |
| Database | SQLite via snekql (aiosqlite backend) |
| Git Client | GitPython or equivalent |
| Validation | Pydantic v2 + JSON Schema |
| Encryption | cryptography |
| Auth | OIDC/JWT + team RBAC |
| Metrics | Prometheus endpoint |
| Tracing | OpenTelemetry |
| Testing | snektest |
| Package Manager | UV |
