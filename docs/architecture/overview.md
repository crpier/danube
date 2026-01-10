# Architecture Overview

## Executive Summary

Danube is a self-hosted CI/CD platform designed for teams requiring full infrastructure control. Built in Python, it provides a lightweight alternative to heavyweight monoliths while maintaining enterprise-grade security and compliance features.

## Key Differentiators

- **Python-Native Pipelines**: Define pipelines in pure Python (`danubefile.py`) with IDE support
- **Configuration as Code**: GitOps workflow with declarative YAML
- **SLSA Level 3 Compliance**: Hermetic builds, ephemeral environments, provenance generation
- **Embedded Services**: Bundled K3s, SQLite, Dex OIDC, container registry
- **Minimal Dependencies**: UV-based installation, no external databases

## Target Users

- Platform engineering teams
- DevOps teams at small-to-medium organizations
- Organizations with compliance requirements prohibiting SaaS CI/CD
- Teams requiring on-premise or private cloud deployment

## Goals

| ID | Goal |
|----|------|
| G1 | Provide a fully functional CI/CD system deployable via simple installation |
| G2 | Enable pipeline definitions using Python with full IDE support |
| G3 | Achieve SLSA Level 3 compliance for supply chain security |
| G4 | Support real-time log streaming with minimal latency |
| G5 | Operate reliably on 4 vCPU / 8GB RAM (scalable to hundreds of pipelines) |
| G6 | Integrate with Git forges via webhooks |
| G7 | Manage all configuration via Git repository (GitOps) |
| G8 | Support cached Docker image builds via internal registry |

## Non-Goals

| ID | Non-Goal |
|----|----------|
| NG1 | Multi-node clustering (single master per cluster; deploy multiple instances for scale) |
| NG2 | Plugin/extension system for third-party integrations |
| NG3 | Non-containerized build environments |
| NG4 | Windows-native builds (Linux containers only) |
| NG5 | Serverless/ephemeral runner provisioning |
| NG6 | UI-based pipeline creation (config must be in Git) |

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Host Machine                       │
│  ┌───────────────────────────────────────────────────┐  │
│  │              Danube Master (Python)               │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │  │
│  │  │ FastAPI  │ │Scheduler │ │ Reaper & CaC     │  │  │
│  │  │HTTP+gRPC │ │Cron/Hook │ │ Syncer           │  │  │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────────────┘  │  │
│  │       └────────────┴─────────────┘                │  │
│  │                    ▼                               │  │
│  │       ┌─────────────────────────────┐             │  │
│  │       │      Master Core            │             │  │
│  │       │  - Job Orchestrator         │             │  │
│  │       │  - K8s Client (kubernetes)  │             │  │
│  │       │  - Log Streamer             │             │  │
│  │       │  - SecretService            │             │  │
│  │       └────────────┬────────────────┘             │  │
│  └────────────────────┼──────────────────────────────┘  │
│                       ▼                                 │
│  ┌─────────────────────────────────────────────────┐   │
│  │          K3s Cluster (Cilium CNI)               │   │
│  │  ┌──────────────────────────────────────────┐   │   │
│  │  │         Pipeline Pod                     │   │   │
│  │  │  ┌──────────────┐  ┌──────────────────┐  │   │   │
│  │  │  │ Coordinator  │  │ Worker           │  │   │   │
│  │  │  │ (Python SDK) │  │ (node:18, etc.)  │  │   │   │
│  │  │  │              │  │ or Kaniko        │  │   │   │
│  │  │  └──────────────┘  └──────────────────┘  │   │   │
│  │  └──────────────────────────────────────────┘   │   │
│  │  ┌──────────────────────────────────────────┐   │   │
│  │  │  Dex OIDC + Registry Pods                │   │   │
│  │  └──────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────┘   │
│  ┌────────────────────────────────────────────────┐    │
│  │ SQLite: /var/lib/danube/danube.db (WAL mode)  │    │
│  │ Logs: /var/lib/danube/logs/                   │    │
│  │ Artifacts: /var/lib/danube/artifacts/         │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 vCPU | 8+ vCPU |
| RAM | 8 GB | 16+ GB |
| Disk | 50 GB | 200+ GB |
| OS | Linux (x86_64, arm64) | Ubuntu 22.04 / Debian 12 |
| Kernel | 4.9+ (eBPF support) | 5.10+ |

## Design Principles

### Hub-and-Spoke Networking

All communication flows through the Master process:
```
Coordinator ──gRPC──▶ Master ──K8s Exec──▶ Worker
```

This eliminates service discovery complexity and centralizes authentication and log aggregation.

### Ephemeral Execution Environments

Each pipeline runs in an isolated Kubernetes Pod that is deleted immediately after completion. This prevents state leakage and supports SLSA compliance.

### GitOps for Configuration

All configuration (pipelines, teams, users, global settings) lives in a Git repository. The Master periodically syncs this repository, ensuring version control and auditability.

### Stateless Shell Execution

Each `step.run()` command spawns a fresh shell process. Directory changes and exports do not persist between commands. Users must chain commands explicitly.

## Related Documentation

- [Components](./components.md) - Detailed component descriptions
- [Execution Model](./execution-model.md) - Pipeline execution flow
- [Security](./security.md) - Threat model and security architecture
