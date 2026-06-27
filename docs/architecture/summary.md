# Danube Architecture and Technical Approach

## Overview

Danube is a self-hosted, single-host CI/CD appliance. A Python Master process runs on one machine and orchestrates ephemeral local containers for each pipeline run. All configuration is managed through a GitOps Blueprint repository.

## Core Architecture

- **Single-host appliance**: Danube targets one machine with local disk, rootless Podman, and one Master process.
- **Master-centric control plane**: The Master owns scheduling, job state, command execution, logs, secrets, artifacts, Blueprint sync, and cleanup.
- **Local runner abstraction**: The Master talks to a rootless Podman-backed runner layer that starts job pods, execs commands, streams logs, and tears jobs down.
- **Two-container job pattern**: Coordinator runs `danubefile.py`; Worker runs build commands in the user-selected image.
- **Hub-and-spoke execution**: Coordinator calls Master; Master execs commands in Worker through the Podman API. Coordinator and Worker do not talk directly.
- **Ephemeral execution**: Every job gets isolated containers and a per-job workspace, then everything is deleted after completion.

## Execution Model

- **Coordinator container**: Python SDK runtime that imports and executes the pipeline definition.
- **Worker container**: User-defined build environment for shell commands and image builds.
- **Stateless commands**: Every `step.run()` starts a fresh shell process in the Worker.
- **Shared workspace**: Coordinator and Worker mount the same per-job workspace directory.
- **Centralized logs/artifacts**: Master captures logs, writes them to disk, streams them to clients, and stores artifacts under the data directory.

## Configuration and Data

- **Blueprint GitOps repo**: Declarative JSON config for global settings, users, teams, pipelines, permissions, and retention.
- **SQLite persistence**: SQLite stores users, teams, pipelines, jobs, steps, secrets, artifacts, and runner state.
- **Filesystem layout**: Logs, artifacts, registry/cache data, workspaces, and keys live under `/var/lib/danube`.

## Security Posture

- **Container isolation**: Danube relies on standard OCI runtime isolation, Linux namespaces, cgroups, seccomp/AppArmor, and non-privileged execution.
- **Default-deny egress**: Job containers should not have direct internet access. Allowed outbound traffic goes through Danube-managed firewall rules and/or an egress proxy.
- **Secrets over RPC**: Secrets are encrypted at rest and fetched from the Master on demand, never injected into container manifests or default environments.
- **SLSA-oriented design**: Ephemeral environments, controlled egress, build logs, artifact records, and signed provenance support supply-chain guarantees.

## Technical Stack

- **Language/runtime**: Python 3.14+
- **API framework**: FastAPI + Uvicorn
- **Async runtime**: asyncio
- **Container runtime layer**: rootless Podman through the Podman API
- **Storage**: SQLite plus host filesystem
- **Validation**: Pydantic v2 and JSON Schema
- **Security**: cryptography, OIDC/JWT auth, signed provenance
- **Observability**: structured logs, Prometheus metrics, OpenTelemetry traces
- **Tooling**: UV, snektest, pyright
