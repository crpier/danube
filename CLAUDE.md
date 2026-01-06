# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Danube is a self-hosted, single-binary CI/CD platform designed for teams requiring full infrastructure control. It targets small-to-medium engineering teams running on-premise or private cloud infrastructure.

**Key Features:**
- Single binary deployment bundling K3s and SQLite
- Python-native pipelines defined in `danubefile.py`
- Configuration as Code (GitOps) via YAML
- SLSA Level 3 compliance with hermetic builds
- Embedded OIDC authentication

## Project Structure

This is a Rust workspace with multiple crates:

```
danube/
├── Cargo.toml (workspace)
├── crates/
│   ├── danube-master/      # Main binary: HTTP API, scheduler, orchestration
│   ├── danube-proto/       # gRPC protocol definitions
│   └── danube-coordinator/ # Python SDK for pipeline definitions
├── frontend/               # SolidJS SPA (embedded in binary)
└── docs/
```

## Development Commands

### Rust Project

```bash
# Check code compiles
cargo check

# Build the project
cargo build

# Build optimized release binary
cargo build --release

# Run tests
cargo test

# Run specific test
cargo test test_name

# Run integration tests
cargo test --test integration_test_name

# Format code
cargo fmt

# Run linter
cargo clippy

# Run the main binary
cargo run --bin danube-master
```

### Python Coordinator SDK

```bash
# Install SDK for development
cd crates/danube-coordinator/python
pip install -e .

# Run Python tests
pytest

# Generate gRPC code from proto
python -m grpc_tools.protoc -I../../danube-proto/proto \
  --python_out=. --grpc_python_out=. \
  ../../danube-proto/proto/danube.proto
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

1. **Danube Master Binary** (Rust)
   - **Axum HTTP API**: REST endpoints, webhooks, health checks
   - **gRPC Server**: Internal RPC for Coordinator ↔ Master communication
   - **Scheduler**: Cron and webhook-based pipeline triggers
   - **Reaper**: Garbage collection for logs, artifacts, images
   - **CaC Syncer**: Polls Git repository for configuration updates
   - **Master Core**: Pipeline orchestration, Pod lifecycle, log streaming
   - **K8s Client**: Pod management via kube-rs
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

### Rust Dependencies
- **axum** + **tower**: HTTP server and middleware
- **tonic**: gRPC server and client
- **kube-rs** + **k8s-openapi**: Kubernetes client
- **sqlx**: SQLite async database access
- **tokio**: Async runtime
- **tracing** + **tracing-subscriber**: Structured logging
- **opentelemetry**: Metrics and traces
- **serde** + **serde_yaml**: Configuration parsing
- **aes-gcm**: Secret encryption
- **git2**: Git repository operations
- **rust-embed**: Embed frontend in binary

### Python (Coordinator SDK)
- **grpcio**: gRPC client
- **Python 3.11**: Runtime

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

- **Unit tests**: Each crate with `#[cfg(test)]` modules
- **Integration tests**: `tests/` directory with K3s cluster
- **Python tests**: pytest for SDK
- **Frontend tests**: Vitest for UI components

Run integration tests against local K3s:
```bash
cargo test --test integration_k8s
```

## Implementation Status

This project is in active development. Implementation tickets are tracked in `docs/tasks.md`.

### Ticket Tracking System

Each ticket has a **Status:** field with these values:

- 🔴 **Todo** - Not started
- 🟡 **In Progress** - Currently being worked on
- 🟢 **Done** - Completed and tested

### How to Update Ticket Status

**Using the helper script (recommended):**
```bash
# Mark ticket as in progress
python3 scripts/update_ticket_status.py DANUBE-1 in-progress

# Mark ticket as done
python3 scripts/update_ticket_status.py DANUBE-1 done

# Mark ticket as todo (reset)
python3 scripts/update_ticket_status.py DANUBE-1 todo
```

**Manual update:**
Edit `docs/tasks.md` and change the `**Status:**` field. Don't forget to also check off acceptance criteria checkboxes when marking as Done.

### Querying Ticket Status

To see all in-progress tickets:
```bash
grep -A5 "🟡 In Progress" docs/tasks.md
```

To see all completed tickets:
```bash
grep -A5 "🟢 Done" docs/tasks.md
```

To see specific ticket status:
```bash
grep -A10 "#### DANUBE-1:" docs/tasks.md
```

### Guidelines for Completing Tickets

1. **Check all acceptance criteria** - Each ticket has checkboxes that must all be checked
2. **Run tests** - Most tickets require tests to pass
3. **Update documentation** - Many tickets require documentation updates
4. **Update status** - Change 🟡 In Progress → 🟢 Done only when ALL criteria met

## Common Patterns

### Adding New gRPC Methods

1. Update `crates/danube-proto/proto/danube.proto`
2. Rebuild proto: `cargo build -p danube-proto`
3. Implement handler in `crates/danube-master/src/grpc/`
4. Add Python client method in `crates/danube-coordinator/python/danube/`

### Adding Database Tables

1. Create migration in `crates/danube-master/src/db/migrations/`
2. Update schema in `crates/danube-master/src/db/schema.rs`
3. Add repository methods in `crates/danube-master/src/db/repos/`

### Adding Configuration Options

1. Update CaC schema in `danube-plan-final.md` (currently planning phase)
2. Add field to config struct in `crates/danube-master/src/config/`
3. Update validation logic
4. Document in configuration guide
