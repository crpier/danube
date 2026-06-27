# Architecture Overview

## Executive Summary

Danube is a self-hosted CI/CD appliance for teams that want full control over their build infrastructure. It runs as a Python Master process on a single machine and orchestrates ephemeral local containers for each pipeline run.

Pipelines are defined in Python (`danubefile.py`). System configuration is managed through a GitOps Blueprint repository containing declarative JSON.

## Key Differentiators

- **Single-host appliance**: Designed to run on one Linux machine with local disk and rootless Podman.
- **Python-native pipelines**: Pipeline definitions are normal Python files with editor support.
- **GitOps Blueprint**: Pipelines, teams, users, permissions, retention, and global settings live in Git.
- **Ephemeral local execution**: Every job creates isolated containers and deletes them after completion.
- **Controlled egress**: Jobs run with default-deny networking and use Danube-managed egress controls for approved destinations.
- **Minimal operating surface**: The default deployment is one appliance host with Danube-managed rootless Podman.

## Target Users

- Platform engineering teams
- DevOps teams at small-to-medium organizations
- Teams that want self-hosted CI/CD with a small operational footprint
- Organizations with compliance requirements prohibiting SaaS CI/CD
- Teams that want auditable, Git-managed CI/CD configuration

## Goals

| ID | Goal |
|----|------|
| G1 | Provide a fully functional CI/CD appliance deployable on one Linux host |
| G2 | Enable pipeline definitions in Python with strong editor support |
| G3 | Keep configuration source-controlled through a Blueprint Git repository |
| G4 | Run builds in ephemeral, isolated local containers |
| G5 | Support real-time log streaming with minimal latency |
| G6 | Provide controlled network egress for hermetic or near-hermetic builds |
| G7 | Store logs, artifacts, job metadata, secrets, and provenance locally |
| G8 | Leave a clean seam for future remote runner agents without requiring them now |

## Non-Goals

| ID | Non-Goal |
|----|----------|
| NG1 | Cluster orchestration as a required deployment substrate |
| NG2 | Multi-node clustering in the initial product |
| NG3 | Reimplementing a container runtime |
| NG4 | Plugin/extension system for arbitrary third-party integrations |
| NG5 | Non-containerized build environments |
| NG6 | Windows-native builds |
| NG7 | UI-based pipeline creation; configuration remains Git-managed |

## High-Level Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                         Host Machine                         │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                  Danube Master (Python)                │  │
│  │                                                        │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐   │  │
│  │  │ FastAPI  │ │Scheduler │ │ Blueprint Syncer     │   │  │
│  │  │ HTTP/API │ │Cron/Hook │ │ Reaper               │   │  │
│  │  └────┬─────┘ └────┬─────┘ └──────────┬───────────┘   │  │
│  │       └────────────┴──────────────────┘               │  │
│  │                    ▼                                  │  │
│  │             ┌───────────────┐                         │  │
│  │             │ Master Core   │                         │  │
│  │             │ - Orchestrator│                         │  │
│  │             │ - Log Writer  │                         │  │
│  │             │ - Secrets     │                         │  │
│  │             │ - Artifacts   │                         │  │
│  │             └───────┬───────┘                         │  │
│  │                     ▼                                 │  │
│  │             ┌───────────────┐                         │  │
│  │             │ Local Runner  │                         │  │
│  │             │ OCI adapter   │                         │  │
│  │             └───────┬───────┘                         │  │
│  └─────────────────────┼──────────────────────────────────┘  │
│                        ▼                                     │
│              ┌───────────────────────┐                       │
│              │ Local Container Runtime│                      │
│              │ Rootless Podman         │                      │
│              └───────────┬───────────┘                       │
│                          ▼                                   │
│              ┌───────────────────────────────┐               │
│              │ Ephemeral Job Environment      │               │
│              │ ┌────────────┐ ┌────────────┐ │               │
│              │ │Coordinator │ │ Worker     │ │               │
│              │ │Python SDK  │ │Build image │ │               │
│              │ └────────────┘ └────────────┘ │               │
│              │ Shared /workspace volume       │               │
│              └───────────────────────────────┘               │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ /var/lib/danube/                                      │  │
│  │ SQLite DB, logs, artifacts, workspaces, registry/cache,│  │
│  │ keys, provenance                                      │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 vCPU | 8+ vCPU |
| RAM | 8 GB | 16+ GB |
| Disk | 50 GB SSD | 200+ GB SSD |
| OS | Linux x86_64 or arm64 | Ubuntu 22.04 / Debian 12 |
| Kernel | Modern kernel with namespaces/cgroups | 5.10+ |
| Runtime | Rootless Podman | Podman managed by Danube installer |

## Design Principles

### Master-Mediated Execution

All job control flows through the Master:

```text
Coordinator ──HTTP/JSON──▶ Master ──runtime exec──▶ Worker
```

The Coordinator decides which steps to run. The Master validates requests, executes commands in the Worker, captures logs, updates state, and enforces lifecycle rules.

### Local Runner, Not Local Runtime

Danube owns orchestration policy, not low-level container isolation. The default local runner uses rootless Podman through the Podman API for pulling images, creating pods/containers, applying cgroups/namespaces, and executing commands.

### Ephemeral Execution Environments

Each job receives fresh containers and a per-job workspace. The runner deletes containers and temporary workspace state after completion or timeout.

### GitOps Configuration

The Blueprint repository is the source of truth. The Master periodically syncs it, validates JSON/schema/reference integrity, and applies accepted changes to SQLite.

### Stateless Shell Execution

Each `step.run()` starts a fresh shell process in the Worker. Directory changes and exported variables do not persist between commands.

## Related Documentation

- [Components](./components.md)
- [Execution Model](./execution-model.md)
- [Local Runner](./local-runner.md)
- [Networking](./networking.md)
- [Security](./security.md)
- [Data Model](./data-model.md)
