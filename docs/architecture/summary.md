# Danube Architecture and Technical Approach

## Overview
Danube is a self-hosted CI/CD platform built for teams that need full infrastructure control. A single Master process orchestrates ephemeral Kubernetes pipeline Pods, with all configuration managed via a GitOps Blueprint repository.

## Core Architecture
- **Master-centric control plane**: One Python Master coordinates scheduling, orchestration, log streaming, secrets access, and GitOps sync.
- **Hub-and-spoke networking**: Coordinator talks to Master over HTTP/2+JSON; Master executes commands in Worker via the Kubernetes Exec API. No direct Coordinator/Worker network path.
- **Ephemeral execution**: Each pipeline run creates a dedicated Pod and deletes it on completion to keep builds isolated and hermetic.
- **Embedded services**: Bundled K3s (with Cilium CNI), Dex OIDC for auth, and a Docker Registry v2 for cached images.

## Execution Model
- **Two-container Pod pattern**: Coordinator runs `danubefile.py` and issues step requests; Worker runs build commands in a user-defined image.
- **Stateless command execution**: Each `step.run()` spawns a fresh shell process in the Worker; state is kept in Python variables or files in `/workspace`.
- **Centralized logs and artifacts**: Master streams stdout/stderr to disk and SSE clients; artifacts stored on the host filesystem.

## Configuration and Data
- **GitOps Blueprint**: Declarative JSON config in a Git repository; Master syncs, validates via JSON Schema, and updates SQLite.
- **Persistence**: SQLite (WAL mode) stores users, teams, pipelines, jobs, steps, secrets, and artifacts.
- **Filesystem layout**: Logs, artifacts, registry data, and keys live under `/var/lib/danube`.

## Security Posture
- **SLSA Level 3 targets**: Ephemeral pods, hermetic builds via Cilium egress allowlists, and signed provenance (Ed25519).
- **Secrets over RPC**: Secrets are encrypted (AES-256-GCM) and retrieved via HTTP/2 JSON, never injected into Pod env vars.
- **Auth and RBAC**: Dex OIDC provides JWTs; team-based RBAC controls access.

## Observability
- **Structured logging**: JSON logs for the Master; job logs are plain text with redaction.
- **Metrics and tracing**: Prometheus `/metrics` and OpenTelemetry OTLP export for metrics and traces.
- **Health checks**: Liveness and readiness endpoints for core subsystems.

## Technical Stack (Summary)
- **Language/runtime**: Python 3.14+, asyncio + uvloop
- **API framework**: FastAPI + Uvicorn, Pydantic v2
- **Kubernetes**: K3s with Cilium CNI; official Kubernetes Python client
- **Storage**: SQLite (aiosqlite), host filesystem for logs/artifacts/registry
- **Security**: Dex OIDC, cryptography (AES-256-GCM), Ed25519 signing
- **Observability**: Prometheus metrics, OpenTelemetry tracing
- **Tooling**: UV package manager, snektest, pyright
