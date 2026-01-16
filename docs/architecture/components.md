# System Components

## Master Process Components

### FastAPI HTTP Server

**Responsibility**: REST API endpoints, webhook ingestion, static file serving

**Technology**: FastAPI, Uvicorn, Pydantic v2

**Endpoints**:
- `/api/v1/pipelines` - Pipeline management
- `/api/v1/jobs` - Job listing and control
- `/api/v1/artifacts` - Artifact download
- `/webhooks/github`, `/webhooks/gitlab` - Git webhook ingestion
- `/health`, `/health/ready` - Health checks
- `/metrics` - Prometheus metrics
- `/` - Frontend SPA

### HTTP/2 Control Plane

**Responsibility**: Internal Coordinator ↔ Master communication

**Technology**: HTTP/2 with JSON payloads

**Endpoints**:
- `POST /rpc/run-step` - Execute command in Worker container
- `POST /rpc/get-secret` - Retrieve decrypted secret
- `POST /rpc/upload-artifact` - Upload build artifact (streamed)
- `POST /rpc/report-status` - Update job status

### Scheduler

**Responsibility**: Trigger pipelines based on cron expressions or webhook events

**Technology**: asyncio tasks, croniter

**Behavior**:
- Cron jobs evaluated every 60 seconds
- Webhook events queued and processed immediately
- Deduplication for concurrent triggers on same ref

### Reaper

**Responsibility**: Garbage collection for logs, artifacts, and container images

**Technology**: asyncio interval task

**Policies**:
- Delete logs older than `retention.logs_days`
- Delete artifacts older than `retention.artifacts_days`
- Delete registry images older than `retention.registry_images_days`
- Runs daily at 03:00 UTC

### Blueprint Syncer

**Responsibility**: Poll Git repository for configuration changes, sync to database

**Technology**: GitPython, asyncio interval task

**Behavior**:
- Polls every `config_repo.sync_interval` (default 60s)
- Parses JSON files in Blueprint repo
- Validates schema with JSON Schema
- Updates SQLite via `aiosqlite` queries
- Logs diff of changes applied

### Master Core

**Responsibility**: Orchestrate pipeline execution, manage Pod lifecycle

**Technology**: kubernetes Python client, asyncio

**State Machine**:
```
pending → scheduling → running → [success | failure | timeout | cancelled]
```

**Operations**:
- Create Pod manifest with Coordinator + Worker containers
- Submit to K8s API
- Wait for Pod ready
- Monitor execution via HTTP/2 JSON calls from Coordinator
- Stream logs to disk and SSE clients
- Update job status in database
- Delete Pod on completion

### K8s Client Wrapper

**Responsibility**: Interact with Kubernetes API

**Technology**: kubernetes (official Python client)

**Operations**:
- Pod creation and deletion
- Exec API for running commands in Worker container
- Log streaming from containers
- Service and deployment management (for Dex, Registry)

### Log Writer

**Responsibility**: Stream stdout/stderr to disk files and SSE clients

**Technology**: aiofiles, asyncio

**Format**:
- Plain text, one line per output
- File path: `/var/lib/danube/logs/<job_id>.log`
- Append-only, no rotation (handled by Reaper)

### SecretService

**Responsibility**: In-memory cache of decrypted secrets, served via HTTP/2 JSON

**Technology**: Python dict, cryptography library (AES-256-GCM)

**Lifecycle**:
1. Job starts → Master loads pipeline secrets from SQLite
2. Decrypt secrets using encryption key from `/var/lib/danube/keys/encryption.key`
3. Cache in memory keyed by `(job_id, secret_key)`
4. Coordinator requests via HTTP/2 JSON
5. Master validates job_id is active and has access
6. Return secret value
7. Clear cache when job completes

## External Components

### K3s

**Responsibility**: Lightweight Kubernetes distribution

**Configuration**: Cilium CNI enabled, Flannel disabled

**Namespaces**:
- `danube-system` - Dex, Registry, Master communication
- `danube-jobs` - Ephemeral pipeline Pods

### Dex

**Responsibility**: OIDC provider for user authentication

**Configuration**: Users defined in Blueprint repository `users.json`

**Flow**:
1. User visits Danube UI
2. Redirect to Dex login page
3. Dex validates credentials against Blueprint user list
4. Issue JWT token with `sub` claim
5. Danube validates JWT and extracts user identity

### Container Registry

**Responsibility**: Store Docker images built by pipelines

**Technology**: Docker Registry v2

**Endpoint**: `registry.danube-system:5000` (cluster-internal)

**Storage**: `/var/lib/danube/registry` (hostPath volume)

### SQLite

**Responsibility**: Persistent storage for metadata

**Configuration**: WAL mode enabled

**Schema**: See [Data Model](./data-model.md)

**Access**: `aiosqlite` with direct SQL queries

## Pipeline Pod Containers

### Coordinator Container

**Image**: `danube-coordinator:latest` (Python 3.14 slim)

**Contains**:
- Danube Python SDK
- HTTP/2 JSON client
- User's `danubefile.py` (mounted from workspace)

**Does NOT contain**: Build tools, compilers, package managers

**Lifecycle**:
1. Container starts
2. SDK imports `danubefile.py`
3. Executes pipeline function
4. Each `step.run()` → HTTP/2 JSON call to Master
5. Exits when pipeline completes

### Worker Container

**Image**: User-defined (e.g., `node:18`, `python:3.14`, `gcr.io/kaniko-project/executor`)

**Purpose**: Execute shell commands or build Docker images

**Access**: No network access except allowlisted domains (Cilium NetworkPolicy)

**Lifecycle**:
- Container starts and idles
- Master uses K8s Exec API to run commands
- Commands execute in `/workspace` (shared volume with Coordinator)
- Container deleted when job completes

## Technology Stack Summary

| Layer | Technology |
|-------|------------|
| HTTP Framework | FastAPI + Uvicorn |
| HTTP/2 Control Plane | JSON over HTTP/2 |
| Async Runtime | asyncio + uvloop |
| K8s Client | kubernetes (official) |
| Database | aiosqlite (raw SQL) |
| Git Client | GitPython |
| Validation | Pydantic v2 |
| Encryption | cryptography |
| Type Checking | pyright (strict mode) |
| Testing | snektest |
| Package Manager | UV |
