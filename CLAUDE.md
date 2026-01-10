# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Danube is a self-hosted CI/CD platform designed for teams requiring full infrastructure control. It targets small-to-medium engineering teams running on-premise or private cloud infrastructure with support for scaling to hundreds of concurrent pipelines.

**Key Features:**
- UV-based Python installation (fast, modern package management)
- Python-native pipelines defined in `danubefile.py`
- Configuration as Code (GitOps) via YAML
- SLSA Level 3 compliance with hermetic builds
- Embedded OIDC authentication via Dex
- Auto-scaling support for cloud environments

## Project Structure

This is a Python project using UV:

```
danube/
├── pyproject.toml          # Project configuration and dependencies
├── uv.lock                 # Locked dependencies
├── danube/                 # Main Python package
│   ├── master.py           # Master process entry point
│   ├── api/                # FastAPI HTTP + gRPC servers
│   ├── orchestrator/       # Job orchestration
│   ├── k8s/                # Kubernetes client wrapper
│   ├── db/                 # SQLAlchemy models and repositories
│   ├── cac/                # Configuration as Code sync
│   ├── security/           # Secrets and authentication
│   └── sdk/                # Coordinator Python SDK
├── proto/                  # gRPC protocol definitions
├── frontend/               # SolidJS SPA (served by FastAPI)
├── tests/                  # Unit, integration, and E2E tests
└── docs/                   # Documentation (split by topic)
    ├── architecture/
    ├── configuration/
    ├── deployment/
    ├── development/
    └── tickets/
```

## Development Commands

### Python Project

```bash
# Install UV if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Run Master process
uv run python -m danube.master

# Run tests
uv run snektest

# Run specific test
uv run snektest tests/unit/test_orchestrator.py

# Run with coverage
uv run snektest --cov=danube --cov-report=html

# Type checking
uv run pyright danube/

# Format code
uv run ruff format danube/

# Lint code
uv run ruff check danube/

# Generate gRPC code from proto
python -m grpc_tools.protoc -I./proto \
  --python_out=./danube/api \
  --grpc_python_out=./danube/api \
  ./proto/danube.proto

# Database migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "Add new table"
```

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run dev server
npm run dev

# Build for production
npm run build

# Type check
npm run typecheck
```

## Architecture

### High-Level Components

1. **Danube Master (Python)**
   - **FastAPI HTTP API**: REST endpoints, webhooks, health checks
   - **gRPC Server**: Internal RPC for Coordinator ↔ Master communication
   - **Scheduler**: Cron and webhook-based pipeline triggers
   - **Reaper**: Garbage collection for logs, artifacts, images
   - **CaC Syncer**: Polls Git repository for configuration updates
   - **Master Core**: Pipeline orchestration, Pod lifecycle, log streaming
   - **K8s Client**: Pod management via kubernetes Python client
   - **SecretService**: In-memory encrypted secret cache

2. **K3s (Bundled)**
   - Lightweight Kubernetes with Cilium CNI
   - Runs pipeline pods in isolation
   - DNS-based egress filtering for SLSA L3 compliance

3. **Pipeline Pods** (Ephemeral)
   - **Coordinator Container**: Python runtime executing `danubefile.py`
   - **Worker Container**: User-specified image (e.g., `node:18`) or Kaniko for builds

4. **Persistent Storage**
   - SQLite database (`/var/lib/danube/danube.db`) with WAL mode
   - Logs in `/var/lib/danube/logs/`
   - Artifacts in `/var/lib/danube/artifacts/`
   - Container registry storage in `/var/lib/danube/registry/`

### Execution Flow

1. CaC Syncer detects configuration changes in Git repository
2. Webhook arrives or cron triggers pipeline
3. Master creates Pod manifest with Coordinator + Worker containers
4. Coordinator imports and executes `danubefile.py`
5. Each `step.run()` call sends gRPC request to Master
6. Master uses K8s Exec API to run command in Worker container
7. Master streams stdout/stderr to disk and SSE (for UI)
8. On completion, Master updates job status and deletes Pod

### Network Architecture

All communication flows through the Master (hub-and-spoke pattern):

```
Coordinator ──gRPC──▶ Master ──K8s Exec──▶ Worker
```

**Key Points:**
- Coordinator has no direct network access to Worker
- Cilium NetworkPolicy enforces DNS-based egress allowlist
- Only whitelisted domains accessible from pipeline pods
- Internal registry accessible at `registry.danube-system:5000`

## Configuration

### Server Configuration (`danube.toml`)

Only minimal configuration required on the host:

```toml
[config_repo]
url = "git@github.com:myorg/danube-config.git"
branch = "main"
sync_interval = "60s"
```

### Configuration as Code Repository

All other configuration lives in Git:

```
danube-config/
├── config.yaml           # Global settings, retention, egress allowlist
├── users.yaml            # User definitions
├── teams.yaml            # Team definitions
└── pipelines/
    ├── frontend-build.yaml
    └── backend-build.yaml
```

## Technology Stack

### Python Dependencies
- **FastAPI** + **Uvicorn**: HTTP server and ASGI runtime
- **grpcio** + **grpcio-tools**: gRPC server and client
- **kubernetes**: Official Kubernetes Python client
- **SQLAlchemy** (async): Database ORM and migrations
- **asyncio** + **uvloop**: Async runtime with performance boost
- **Pydantic v2**: Data validation and settings
- **cryptography**: AES-256-GCM secret encryption
- **GitPython**: Git repository operations
- **OpenTelemetry**: Metrics and traces
- **pyright**: Strict type checking

### Coordinator SDK (Python)
- **grpcio**: gRPC client
- **Python 3.11+**: Runtime

### Frontend
- **SolidJS**: UI framework
- **Vite**: Build tool

### Infrastructure
- **K3s**: Lightweight Kubernetes
- **Cilium**: CNI with DNS-based egress filtering
- **Dex**: OIDC provider
- **Docker Registry v2**: Internal container registry
- **SQLite**: Embedded database (WAL mode)

## Key Design Principles

### Shell Execution Model: Stateless

Each `step.run()` spawns a fresh `/bin/sh -c` process. Directory changes and variable exports do NOT persist:

```python
# WRONG: cd does not persist
step.run("cd /app")
step.run("npm install")  # Runs in /, not /app

# CORRECT: Chain commands
step.run("cd /app && npm install")
```

### State Management

| State Type | Location | Scope |
|------------|----------|-------|
| Python variables | Coordinator memory | Pipeline execution lifetime |
| Shell variables | Worker process | Single command lifetime |
| Environment variables | Passed via `env={}` | Per-step |
| Captured output | gRPC response | Per-step |

### Security

- Secrets stored encrypted at rest (AES-256-GCM)
- Secrets served via gRPC to Coordinator (never in env vars)
- Pipeline pods ephemeral (deleted after execution)
- Hermetic builds via DNS egress filtering
- SLSA Level 3 provenance generation

## Database Schema

SQLite database with core tables:
- `users`: User accounts with OIDC mapping
- `teams`: Team definitions with RBAC
- `pipelines`: Pipeline metadata
- `jobs`: Job execution records
- `steps`: Individual step records
- `secrets`: Encrypted secrets
- `artifacts`: Artifact metadata

WAL mode enabled for concurrent reads during writes.

## Testing Strategy

- **Unit tests**: snektest for all modules
- **Integration tests**: `tests/integration/` with K3s cluster
- **E2E tests**: `tests/e2e/` for full system workflows
- **Frontend tests**: Vitest for UI components

Run tests:
```bash
# All tests
uv run snektest

# With coverage
uv run snektest --cov=danube --cov-report=html
```

## Documentation

Documentation is split into focused, LLM-friendly files:

- **`docs/architecture/`**: System architecture, components, data model, security
- **`docs/configuration/`**: Server and CaC configuration reference
- **`docs/deployment/`**: Installation and upgrade guides
- **`docs/development/`**: Development setup and testing
- **`docs/tickets/`**: Implementation tickets organized by epic

See `docs/tickets/README.md` for ticket workflow.

## Implementation Status

This project is in active development. Implementation tickets are tracked in `docs/tickets/`.

**8 Epics, 31 Tickets, ~186 hours estimated**

### Ticket Tracking System

Each ticket has a **Status:** field with these values:

- 🔴 **Todo** - Not started
- 🟡 **In Progress** - Currently being worked on
- 🟢 **Done** - Completed and tested
- ⚫ **Blocked** - Waiting on dependency

### Querying Ticket Status

To see all in-progress tickets:
```bash
grep -r "🟡 In Progress" docs/tickets/*.md
```

To see all completed tickets:
```bash
grep -r "🟢 Done" docs/tickets/*.md
```

### Guidelines for Completing Tickets

1. **Check all acceptance criteria** - Each ticket has checkboxes that must all be checked
2. **Run tests** - Most tickets require tests to pass
3. **Update documentation** - Many tickets require documentation updates
4. **Update status** - Change 🟡 In Progress → 🟢 Done only when ALL criteria met

## Common Patterns

### Adding New gRPC Methods

1. Update `proto/danube.proto`
2. Rebuild proto: `python -m grpc_tools.protoc ...`
3. Implement handler in `danube/api/grpc.py`
4. Add Python client method in `danube/sdk/coordinator.py`

### Adding Database Tables

1. Update models in `danube/db/models.py`
2. Create migration: `uv run alembic revision --autogenerate -m "Add table"`
3. Review and edit migration file
4. Apply migration: `uv run alembic upgrade head`
5. Add repository methods in `danube/db/repos.py`

### Adding Configuration Options

1. Update CaC schema in `danube/cac/models.py`
2. Update parser in `danube/cac/parser.py`
3. Update validation logic
4. Document in `docs/configuration/cac-reference.md`
