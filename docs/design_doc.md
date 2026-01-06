# Danube - Architecture & Implementation Plan

## Part 1: Architecture Design Document

---

### Document Metadata

| Field | Value |
|-------|-------|
| **Project Name** | Danube |
| **Version** | 1.0 |
| **Status** | Draft |
| **Author** | [crpier42] |
| **Last Updated** | 2026-01-06 |

---

### 1. Executive Summary

Danube is a self-hosted, single-binary CI/CD platform designed for teams that require full infrastructure control without the operational burden of managing multiple services. It targets small-to-medium engineering teams running on-premise or private cloud infrastructure.

**Key Differentiators:**

- **Single Binary Deployment:** No external database servers, no container orchestrators to manage. Danube bundles K3s and SQLite internally.
- **Python-Native Pipelines:** Pipelines are defined in pure Python (`danubefile.py`), enabling conditionals, loops, and IDE support (autocomplete, type checking).
- **Configuration as Code (GitOps):** All configuration (pipelines, teams, users) managed via Git repository with declarative YAML.
- **SLSA Level 3 Compliance:** Hermetic builds with DNS-based egress filtering, ephemeral environments, and provenance generation out of the box.
- **Embedded OIDC:** Dex OIDC provider runs in-cluster for authentication, with future support for GitHub/Google SSO.

**Target Users:** Platform engineers, DevOps teams, and organizations with compliance requirements that prohibit SaaS CI/CD solutions.

---

### 2. Goals & Non-Goals

#### 2.1 Goals

| ID | Goal |
|----|------|
| G1 | Provide a fully functional CI/CD system deployable via a single binary or container image. |
| G2 | Enable pipeline definitions using Python with full IDE support (type stubs, autocomplete). |
| G3 | Achieve SLSA Level 3 compliance for software supply chain security. |
| G4 | Support real-time log streaming to the UI with minimal latency. |
| G5 | Operate reliably on a single node with 2 vCPU / 4GB RAM. |
| G6 | Integrate with Git forges (GitHub, GitLab, Gitea) via webhooks. |
| G7 | Manage all configuration via Git repository (GitOps workflow). |
| G8 | Support cached Docker image builds via internal registry. |

#### 2.2 Non-Goals

| ID | Non-Goal |
|----|----------|
| NG1 | Multi-node clustering or horizontal scaling (V1). |
| NG2 | Plugin/extension system for third-party integrations. |
| NG3 | Support for non-containerized build environments (bare metal agents). |
| NG4 | Windows-native builds (Linux containers only). |
| NG5 | Serverless/ephemeral runner provisioning (e.g., AWS Lambda). |
| NG6 | UI-based pipeline creation (all config must be in Git). |

---

### 3. Background & Context

#### 3.1 Problem Statement

Existing self-hosted CI/CD solutions fall into two categories:

1. **Heavy Monoliths (Jenkins, GitLab CI):** Require multiple services (PostgreSQL, Redis, Sidekiq), consume significant resources, and demand ongoing maintenance.
2. **Cloud-Native Systems (Tekton, Argo Workflows):** Assume an existing Kubernetes cluster and expert-level knowledge of CRDs, RBAC, and CNI networking.

Teams without dedicated platform engineers often resort to SaaS solutions (GitHub Actions, CircleCI), sacrificing control over their build infrastructure and exposing secrets to third parties.

#### 3.2 Proposed Solution

Danube occupies the middle ground: a **single-artifact deployment** that internally manages its own Kubernetes distribution (K3s), database (SQLite), container registry, and OIDC provider. Configuration is managed via Git (GitOps), ensuring version control, auditability, and rollback capabilities.

---

### 4. System Architecture

#### 4.1 High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Host Machine                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         Danube Binary                                 │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │   Axum      │  │  Scheduler  │  │   Reaper    │  │  CaC Syncer  │  │  │
│  │  │   HTTP API  │  │  (Cron/     │  │  (Garbage   │  │  (Git Poll)  │  │  │
│  │  │  + gRPC     │  │   Webhook)  │  │  Collector) │  │              │  │  │
│  │  └──────┬──────┘  └──────┬──────┘  └─────────────┘  └──────┬───────┘  │  │
│  │         │                │                                 │          │  │
│  │         └────────┬───────┴─────────────────────────────────┘          │  │
│  │                  ▼                                                    │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │                      Master Core                                │  │  │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │  │  │
│  │  │  │ Pipeline    │  │ K8s Client  │  │ Log Writer              │  │  │  │
│  │  │  │ State       │  │ (kube-rs)   │  │ (Disk)                  │  │  │  │
│  │  │  │ Machine     │  │             │  │                         │  │  │  │
│  │  │  └─────────────┘  └──────┬──────┘  └─────────────────────────┘  │  │  │
│  │  │                          │                                      │  │  │
│  │  │  ┌─────────────────────────────────────────────────────────────┐│  │  │
│  │  │  │ SecretService (in-memory cache + SQLite)                    ││  │  │
│  │  │  └─────────────────────────────────────────────────────────────┘│  │  │
│  │  └──────────────────────────┼──────────────────────────────────────┘  │  │
│  └─────────────────────────────┼─────────────────────────────────────────┘  │
│                                │                                            │
│                                ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                        K3s (Bundled, Cilium CNI)                        ││
│  │  ┌──────────────────────────────────────────────────────────────────┐   ││
│  │  │                      Pipeline Pod                                │   ││
│  │  │  ┌──────────────────────┐    ┌────────────────────────────────┐  │   ││
│  │  │  │  Coordinator         │    │  Worker                        │  │   ││
│  │  │  │  (Python Runtime)    │    │  (User Image: node:18, etc.)   │  │   ││
│  │  │  │                      │    │  OR                            │  │   ││
│  │  │  │  - Runs danubefile.py│    │  Kaniko Executor (for builds)  │  │   ││
│  │  │  │  - Calls Master gRPC │    │                                │  │   ││
│  │  │  └──────────────────────┘    └────────────────────────────────┘  │   ││
│  │  │              │                            ▲                      │   ││
│  │  │              │         gRPC               │  K8s Exec            │   ││
│  │  │              └────────────────────────────┘                      │   ││
│  │  └──────────────────────────────────────────────────────────────────┘   ││
│  │                                                                         ││
│  │  ┌──────────────────────────────────────────────────────────────────┐   ││
│  │  │  Dex OIDC Provider Pod                                           │   ││
│  │  │  - Issues JWT tokens                                             │   ││
│  │  │  - Login UI                                                      │   ││
│  │  │  - User validation (from CaC repo)                               │   ││
│  │  └──────────────────────────────────────────────────────────────────┘   ││
│  │                                                                         ││
│  │  ┌──────────────────────────────────────────────────────────────────┐   ││
│  │  │  Container Registry Pod (Docker Registry v2)                     │   ││
│  │  │  - Stores built images                                           │   ││
│  │  │  - registry.danube-system:5000                                   │   ││
│  │  └──────────────────────────────────────────────────────────────────┘   ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                             │
│  ┌────────────────────┐  ┌──────────────────────────────────────────────┐   │
│  │  /var/lib/danube   │  │  SQLite: danube.db (WAL mode: ON)            │   │
│  │  ├── logs/         │  │  - Users, Teams, Pipelines, Jobs, Secrets    │   │
│  │  ├── artifacts/    │  └──────────────────────────────────────────────┘   │
│  │  ├── registry/     │                                                     │
│  │  ├── danube.db     │  ┌──────────────────────────────────────────────┐   │
│  │  └── keys/         │  │  Git Repository (CaC)                        │   │
│  │      ├── encryption│  │  - users.yaml                                │   │
│  │      ├── signing   │  │  - teams.yaml                                │   │
│  │      └── git_deploy│  │  - pipelines/*.yaml                          │   │
│  └────────────────────┘  │  - config.yaml                               │   │
│                          └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 4.2 Component Descriptions

| Component | Responsibility | Technology |
|-----------|----------------|------------|
| **Axum HTTP API** | REST endpoints for UI, webhook ingestion, health checks. | Rust, Axum, Tower |
| **gRPC Server** | Internal RPC for Coordinator ↔ Master communication. | Rust, Tonic |
| **Scheduler** | Triggers pipelines based on cron expressions or webhook events. | Rust, Tokio tasks |
| **Reaper** | Background task for deleting expired logs/artifacts/images based on retention policy. | Rust, Tokio interval |
| **CaC Syncer** | Polls Git repository, syncs configuration to database. | Rust, git2 |
| **Master Core** | Orchestrates pipeline execution, manages Pod lifecycle, streams logs. | Rust, kube-rs |
| **K8s Client** | Submits Pod manifests, executes commands via K8s Exec API. | kube-rs, k8s-openapi |
| **Log Writer** | Streams stdout/stderr to disk files. | Rust, tokio::fs |
| **SecretService** | In-memory cache of decrypted secrets, served to pipelines via gRPC. | Rust, dashmap |
| **Embedded Frontend** | SolidJS SPA compiled and embedded via rust-embed. | SolidJS, Vite |
| **K3s** | Lightweight Kubernetes distribution with Cilium CNI. | K3s, containerd, Cilium |
| **SQLite** | Persistent storage for structured metadata (WAL mode hardcoded). | SQLite, sqlx |
| **Dex** | OIDC provider for user authentication. | Dex (Go binary) |
| **Container Registry** | Stores Docker images built by pipelines. | Docker Registry v2 |

---

### 5. Execution Model

#### 5.1 The Pod Pattern

Each pipeline execution spawns a single Kubernetes Pod containing two containers:

| Container | Image | Role |
|-----------|-------|------|
| **Coordinator** | `danube-coordinator:latest` (Python 3.11 slim) | Executes user's `danubefile.py`. Contains only the Danube Python SDK. No build tools. |
| **Worker** | User-defined (e.g., `node:18`, `gcr.io/kaniko-project/executor`) | Receives shell commands from Master via K8s Exec API, or builds Docker images with Kaniko. |

#### 5.2 Execution Flow

```
1. CaC Syncer detects new commit in config repo
         │
         ▼
2. Syncer parses pipelines/*.yaml, updates database
         │
         ▼
3. Webhook arrives (or cron triggers)
         │
         ▼
4. Master: Create Pod manifest
         │
         ▼
5. K3s: Schedule Pod, pull images
         │
         ▼
6. Coordinator starts, imports danubefile.py
         │
         ▼
7. Coordinator encounters step("npm install")
         │
         ▼
8. Coordinator → gRPC → Master: RunStep(command="npm install")
         │
         ▼
9. Master → K8s API: Exec into Worker container
         │
         ▼
10. Master streams stdout/stderr → Log file + SSE (UI)
         │
         ▼
11. Step completes, Master returns exit code to Coordinator
         │
         ▼
12. Coordinator continues to next step (or exits)
         │
         ▼
13. Master detects Pod exit, updates job status in SQLite
         │
         ▼
14. Master deletes Pod (ephemeral)
```

#### 5.3 Networking Strategy: Hub-and-Spoke + Egress Filtering

All network communication flows through the Master.

```
Coordinator ──gRPC──▶ Master ──K8s Exec──▶ Worker
     ▲                  │
     │                  │
     └──────────────────┘
          (Response)
```

**Egress Filtering (SLSA L3 Compliance):**

- Cilium NetworkPolicy enforces DNS-based egress allowlist
- Only whitelisted domains reachable from pipeline pods
- Example allowlist: `registry.npmjs.org`, `pypi.org`, `registry.danube-system`

**Rationale:**

- Eliminates service discovery complexity between ephemeral containers.
- Simplifies authentication (only Master needs K8s credentials).
- Enables log aggregation at a single point.
- Hermetic builds prevent supply chain attacks.

#### 5.4 State Management

| State Type | Location | Scope |
|------------|----------|-------|
| Python variables | Coordinator container memory | Pipeline execution lifetime |
| Shell variables | Worker container process | Single command lifetime |
| Environment variables | Passed explicitly via `env={}` | Per-step |
| Captured output | Returned to Coordinator via gRPC response | Per-step |

**Shell Execution Model: Stateless.**
Each `run()` command spawns a fresh `/bin/sh -c` process. Directory changes or variable exports do not persist.

```python
# WRONG: cd does not persist
step.run("cd /app")
step.run("npm install")  # Runs in /, not /app

# CORRECT: Chain commands
step.run("cd /app && npm install")
```

---

### 6. Data Model

#### 6.1 SQLite Schema

```sql
-- Core entities
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    oidc_subject TEXT UNIQUE,  -- Dex subject claim
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE teams (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE team_members (
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',  -- 'member', 'admin'
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(id),
    repo_url TEXT NOT NULL,
    branch_filter TEXT,  -- JSON array: ["main", "release/*"]
    cron_schedule TEXT,  -- Cron expression or NULL
    config_path TEXT NOT NULL DEFAULT 'danubefile.py',
    max_duration_seconds INTEGER DEFAULT 3600,  -- 1 hour default
    workspace_size_gb INTEGER DEFAULT 5,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE pipeline_permissions (
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    level TEXT NOT NULL,  -- 'read', 'write', 'admin'
    PRIMARY KEY (pipeline_id, team_id)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    trigger_type TEXT NOT NULL,  -- 'webhook', 'cron', 'manual'
    trigger_ref TEXT,  -- Git SHA or branch name
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'running', 'success', 'failure', 'cancelled', 'timeout'
    started_at TEXT,
    finished_at TEXT,
    log_path TEXT,  -- Relative path to log file
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    exit_code INTEGER,
    started_at TEXT,
    finished_at TEXT,
    log_offset_start INTEGER,  -- Byte offset in job log file
    log_offset_end INTEGER
);

CREATE TABLE secrets (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT REFERENCES pipelines(id),  -- NULL = global secret
    key TEXT NOT NULL,
    value_encrypted BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(pipeline_id, key)
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,  -- Filesystem path
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(job_id, name)
);

-- Indexes
CREATE INDEX idx_jobs_pipeline_id ON jobs(pipeline_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_steps_job_id ON steps(job_id);
CREATE INDEX idx_team_members_user_id ON team_members(user_id);
CREATE INDEX idx_pipeline_permissions_team_id ON pipeline_permissions(team_id);
```

#### 6.2 Filesystem Layout

```
/var/lib/danube/
├── danube.db              # SQLite database (WAL mode hardcoded)
├── logs/
│   └── <job_id>.log       # Append-only log file per job
├── artifacts/
│   └── <job_id>/
│       └── <artifact_name>.tar.gz
├── registry/              # Container registry storage
│   └── docker/
│       └── registry/
│           └── v2/
└── keys/
    ├── encryption.key     # Symmetric key for secrets (AES-256-GCM)
    ├── signing.key        # Ed25519 key for provenance signing
    └── git_deploy_key     # SSH private key for CaC repo
    └── git_deploy_key.pub # SSH public key (add to Git repo)
```

---

### 7. Configuration

#### 7.1 Server Configuration (danube.toml)

**Only configuration needed - everything else is in Git CaC repository.**

```toml
# /etc/danube/danube.toml

[config_repo]
url = "git@github.com:myorg/danube-config.git"
branch = "main"
sync_interval = "60s"  # How often to poll for changes
```

**That's it!** All other configuration (pipelines, teams, users, global settings) lives in the CaC repository.

#### 7.2 Configuration as Code Repository Structure

```
danube-config/
├── config.yaml           # Global settings
├── users.yaml            # User definitions
├── teams.yaml            # Team definitions
└── pipelines/
    ├── frontend-build.yaml
    ├── backend-build.yaml
    └── deploy-prod.yaml
```

#### 7.3 CaC: Global Configuration (config.yaml)

```yaml
apiVersion: danube.dev/v1
kind: Config
metadata:
  name: global

spec:
  server:
    bind_address: "0.0.0.0:8080"
    data_dir: "/var/lib/danube"

  kubernetes:
    namespace: "danube-jobs"
    coordinator_image: "danube-coordinator:latest"

  retention:
    logs_days: 30
    artifacts_days: 14
    registry_images_days: 30

  observability:
    otel_endpoint: "http://localhost:4317"
    metrics_enabled: true
    traces_enabled: true

  egress_allowlist:
    # DNS-based allowlist via Cilium NetworkPolicy
    - "registry.npmjs.org"
    - "pypi.org"
    - "github.com"
    - "registry.danube-system"  # Internal registry

  git_authentication:
    # GitHub App for primary organization
    - type: github_app
      name: myorg-app
      app_id: "123456"
      installation_id: "78910"
      private_key_secret: "github-app-myorg-key"
      match_patterns:
        - "github.com/myorg/*"
        - "github.com/myorg-frontend/*"

    # GitLab self-hosted instance
    - type: gitlab_token
      name: gitlab-internal
      url: "https://gitlab.internal.company.com"
      token_secret: "gitlab-token"
      match_patterns:
        - "gitlab.internal.company.com/*"

    # SSH key fallback for other Git servers
    - type: ssh_key
      name: fallback
      private_key_path: "/var/lib/danube/keys/git_fallback_key"
      match_patterns:
        - "*"  # Matches all repos not matched above
```

#### 7.4 CaC: User Definitions (users.yaml)

```yaml
apiVersion: danube.dev/v1
kind: User
metadata:
  name: alice
spec:
  email: alice@example.com
  # Generate with: htpasswd -bnBC 10 "" password | tr -d ':\n'
  password_hash: $2a$10$2b2cU8CPhOTaGrs1HRQuAueS7JTT5ZHsHSzYiFPm1leZck7Mc8T4W

---
apiVersion: danube.dev/v1
kind: User
metadata:
  name: bob
spec:
  email: bob@example.com
  password_hash: $2a$10$...
```

#### 7.5 CaC: Team Definitions (teams.yaml)

```yaml
apiVersion: danube.dev/v1
kind: Team
metadata:
  name: engineering
spec:
  members:
    - alice@example.com
    - bob@example.com

---
apiVersion: danube.dev/v1
kind: Team
metadata:
  name: platform
spec:
  members:
    - alice@example.com
  global_admin: true  # Full access to all pipelines
```

#### 7.6 CaC: Pipeline Definition (pipelines/frontend-build.yaml)

```yaml
apiVersion: danube.dev/v1
kind: Pipeline
metadata:
  name: frontend-build
  team: engineering  # Owned by engineering team

spec:
  repository: https://github.com/myorg/frontend
  branch_filter: ["main", "develop"]

  triggers:
    - on: push
    - on: cron
      schedule: "0 0 * * *"  # Daily at midnight UTC

  script: danubefile.py  # Path to pipeline script in repo

  max_duration_seconds: 3600  # 1 hour timeout
  workspace_size_gb: 10  # 10GB workspace

  permissions:
    - team: engineering
      level: admin
    - team: platform
      level: read
```

#### 7.7 Pipeline Execution Script (`danubefile.py` in app repo)

```python
from danube import pipeline, step, ctx, secrets, artifacts

@pipeline(name="Frontend Build")
def build():
    # Stage 1: Build builder image with Kaniko
    step.run(
        "/kaniko/executor "
        "--context=/workspace "
        "--dockerfile=Dockerfile.builder "
        "--destination=registry.danube-system:5000/frontend-builder:latest "
        "--cache=true",
        name="Build Builder Image",
        image="gcr.io/kaniko-project/executor:latest"
    )

    # Stage 2: Run tests in cached builder image
    step.run(
        "npm ci && npm test",
        name="Run Tests",
        image="registry.danube-system:5000/frontend-builder:latest"
    )

    # Upload test coverage
    artifacts.upload("coverage/", name="coverage-report")

    # Stage 3: Build production image (if on main branch)
    if ctx.branch == "main":
        api_key = secrets.get("DOCKER_HUB_TOKEN")

        step.run(
            f"/kaniko/executor "
            f"--context=/workspace "
            f"--dockerfile=Dockerfile "
            f"--destination=myorg/frontend:{ctx.commit_sha[:7]} "
            f"--destination=myorg/frontend:latest",
            name="Build Production Image",
            image="gcr.io/kaniko-project/executor:latest",
            env={"DOCKER_CONFIG": "/kaniko/.docker"}  # Credentials injected by Master
        )
```

---

### 8. Security Model

#### 8.1 Threat Model

| Threat | Mitigation |
|--------|------------|
| Malicious pipeline code escapes container | K3s uses containerd with default seccomp/AppArmor profiles. Pods run as non-root. |
| Secret exfiltration via logs | Secrets accessed via SecretService gRPC, never injected as env vars. Log scrubbing for known patterns. |
| Unauthorized API access | Dex OIDC authentication required. Team-based RBAC for pipeline/secret access. |
| Tampered build artifacts | SLSA provenance generation with Ed25519 signatures. |
| Coordinator → Worker spoofing | All commands route through Master. Workers cannot initiate connections. |
| Supply chain attacks via dependencies | Cilium NetworkPolicy restricts egress to allowlist only. |
| Configuration tampering | CaC repo is source of truth. Git commit history provides audit trail. |

#### 8.2 Secrets Management

**Architecture: SecretService Instead of Environment Variables**

1. Secrets stored in SQLite encrypted with AES-256-GCM.
2. Encryption key stored in `/var/lib/danube/keys/encryption.key` (file permissions: 0600).
3. When a job starts, Master loads all pipeline secrets into SecretService in-memory cache (decrypted).
4. Coordinator requests secrets via gRPC: `GetSecret(job_id, key) -> value`
5. Master validates job_id is active and has access to requested secret.
6. Secret value returned to Coordinator, which can inject into commands as needed.
7. Secrets never written to log files or Pod environment variables.
8. SecretService cache cleared when job completes.

**Benefits over env var injection:**

- Secrets not visible in `kubectl describe pod` or Pod YAML
- Secrets loaded on-demand only when needed
- Better audit trail (track which secrets accessed by which jobs)
- Secrets can be rotated without recreating Pods

#### 8.3 SLSA Level 3 Compliance

| Requirement | Implementation |
|-------------|----------------|
| **Hermetic builds** | Cilium NetworkPolicy denies egress except allowlisted domains. |
| **Ephemeral environments** | Pods are deleted immediately after job completion. |
| **Provenance generation** | Master generates signed JSON provenance document (in-toto format). |
| **Non-falsifiable** | All build steps executed in isolated pods, logs immutable. |
| **Two-person review** | Out of scope (organizational policy via Git PRs). |

#### 8.4 Authentication & Authorization

**Authentication: Dex OIDC Provider**

- Dex runs as pod in K3s cluster
- Users defined in CaC repo (users.yaml)
- Login flow: User → Dex login UI → JWT token → Danube validates token
- Future: Add GitHub/Google/LDAP connectors

**Authorization: Team-Based RBAC**

- Users belong to Teams
- Teams have permissions on Pipelines
- Permission levels: `read`, `write`, `admin`
- Global admin teams have access to all pipelines

**Example Permission Check:**

```
User alice wants to trigger pipeline frontend-build:
1. Check: Is alice member of engineering team? → Yes
2. Check: Does engineering team have write/admin on frontend-build? → Yes (admin)
3. Allow action
```

---

### 9. Observability

#### 9.1 Logging

- **Master logs:** Structured JSON to stdout (for container log collection).
- **Job logs:** Plain text, streamed to `/var/lib/danube/logs/<job_id>.log`.
- **Log level:** Configurable via `DANUBE_LOG` env var (trace, debug, info, warn, error).
- **Sensitive data:** Automatically redacted (secrets, tokens, emails).

#### 9.2 Metrics

**OpenTelemetry Integration (Early Setup)**

Prometheus endpoint at `/metrics` and OTLP export to configured collector:

**Master Metrics:**

- `danube_jobs_total{status, pipeline}` — Total jobs by status
- `danube_job_duration_seconds{pipeline}` — Histogram of job durations
- `danube_active_pods` — Current running pipeline Pods
- `danube_database_queries_total{operation}` — SQLite query counts
- `danube_secret_requests_total{pipeline}` — Secret access counts
- `danube_log_bytes_written_total` — Total log volume
- `danube_cac_sync_total{status}` — CaC repository sync attempts
- `danube_cac_sync_last_success` — Timestamp of last successful sync
- `danube_job_timeouts_total{pipeline}` — Jobs exceeding max duration

**K8s/Dex/Registry Metrics:**

- `danube_k8s_api_calls_total{operation, status}`
- `danube_dex_logins_total{status}`
- `danube_registry_pulls_total{image}`
- `danube_registry_pushes_total{image}`

**Application-Level Tracing:**

- All gRPC calls traced with OpenTelemetry spans
- HTTP requests traced end-to-end
- Job execution traces include: trigger → pod start → steps → completion

#### 9.3 Health Checks

**Endpoints:**

- `GET /health` — Returns 200 if API is responsive
- `GET /health/ready` — Returns 200 if all subsystems healthy:
  - SQLite connection active
  - K3s API reachable
  - Dex pod running
  - Registry pod running
  - CaC sync successful within last 5 minutes
- `GET /metrics` — Prometheus metrics endpoint (no auth required)

**Periodic Health Checks (Background):**

- Run every 5 minutes
- Check all subsystems
- Attempt self-healing:
  - Restart failed Dex/Registry pods
  - Retry failed CaC sync
  - Clear stale locks in database
- Log health check results
- (V2: Alert on failures)

---

### 10. Deployment Strategy

#### 10.1 Installation Flow

```bash
# 1. Download installer
curl -fsSL https://get.danube.dev | sh

# 2. Installer script:
#    - Detects OS/arch
#    - Downloads danube binary to /usr/local/bin
#    - Installs K3s with Cilium CNI (disables Flannel)
#    - Installs Cilium CLI
#    - Creates /var/lib/danube directory
#    - Generates encryption keys
#    - Generates SSH deploy key for CaC repo
#    - Creates minimal /etc/danube/danube.toml
#    - Creates systemd unit file
#    - Starts danube service

# 3. Post-install steps (printed by installer):
#    - Add SSH public key to CaC Git repository
#    - Create CaC repository with initial config
#    - Visit http://localhost:8080 to access UI

# 4. First login
#    - Dex login screen appears
#    - Enter credentials from users.yaml in CaC repo
```

#### 10.2 System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 20 GB | 100 GB |
| OS | Linux (x86_64, arm64) | Ubuntu 22.04 / Debian 12 |

**Additional for Cilium:**

- Kernel 4.9+ (for eBPF support)
- Kernel modules: `xt_socket`, `xt_mark`

#### 10.3 Upgrade Path

**Standard Upgrade:**

```bash
# 1. Enter draining mode
danube drain --timeout=3600  # Wait up to 1 hour for jobs to finish

# 2. Stop service
systemctl stop danube

# 3. Backup database
cp /var/lib/danube/danube.db /var/lib/danube/danube.db.backup

# 4. Download new binary
curl -fsSL https://get.danube.dev/v1.2.0 > /usr/local/bin/danube
chmod +x /usr/local/bin/danube

# 5. Run migrations
danube migrate

# 6. Restart service
systemctl start danube
```

**Pre-Upgrade Health Checks (Automated):**

- Database integrity (`PRAGMA integrity_check`)
- All jobs finished or drained
- K3s API reachable
- Disk space available (>10% free)
- Encryption keys exist and readable
- CaC sync healthy

**Job Draining:**

- When `danube drain` is run:
  1. Master enters "draining" mode
  2. No new jobs accepted (webhooks return 503, cron skipped)
  3. Wait for running jobs to complete (up to timeout)
  4. If jobs still running at timeout, prompt: [force-kill] or [abort drain]
  5. Once drained, safe to stop service

**K3s Upgrade:**

```bash
# K3s can be upgraded separately
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.28.0+k3s1 sh -s - \
  --flannel-backend=none \
  --disable-network-policy

# Reinstall Cilium if needed
cilium install
```

#### 10.4 Bootstrap: Self-Hosting Danube's CI/CD

**Once Danube is feature-complete, it will build itself:**

1. Create a Danube pipeline in the Danube CaC repository.
2. Configure webhook to trigger on push to `main`.
3. Pipeline runs tests, builds binaries (for multiple architectures), creates GitHub release.
4. No external CI systems (GitHub Actions, etc.) used for production builds.
5. This validates Danube is production-ready and dogfoods the platform.

---

### 11. Container Registry & Image Caching

#### 11.1 Internal Registry

Danube runs Docker Registry v2 as a K3s deployment:

**Registry Configuration:**

```yaml
# Deployed by Danube on startup
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
          env:
            - name: REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY
              value: /var/lib/registry
          volumeMounts:
            - name: registry-storage
              mountPath: /var/lib/registry
      volumes:
        - name: registry-storage
          hostPath:
            path: /var/lib/danube/registry
---
apiVersion: v1
kind: Service
metadata:
  name: registry
  namespace: danube-system
spec:
  selector:
    app: registry
  ports:
    - port: 5000
```

**Registry URL:** `registry.danube-system:5000`

**Authentication:**

- Internal registry requires no authentication (only accessible from `danube-jobs` namespace)
- Cilium NetworkPolicy restricts access
- For external registries (Docker Hub, etc.), credentials fetched from SecretService

#### 11.2 Kaniko Image Building

**Why Kaniko:**

- Builds container images **without Docker daemon**
- Runs in unprivileged pods (SLSA L3 compliant)
- Supports layer caching via registry
- Standard OCI image output

**Example Build Step:**

```python
# In danubefile.py
step.run(
    "/kaniko/executor "
    "--context=/workspace "
    "--dockerfile=Dockerfile.builder "
    "--destination=registry.danube-system:5000/my-app:builder "
    "--cache=true "
    "--cache-repo=registry.danube-system:5000/my-app/cache",
    name="Build Base Image",
    image="gcr.io/kaniko-project/executor:latest"
)
```

**Layer Caching:**

- Kaniko caches layers in `--cache-repo`
- Subsequent builds reuse cached layers
- Dramatically speeds up builds with unchanged dependencies

**Registry Credentials:**

- Master auto-injects Docker config for internal registry
- For external registries, user provides credentials via SecretService

#### 11.3 Garbage Collection

**Reaper handles registry cleanup:**

- Delete images older than `retention.registry_images_days`
- Delete unreferenced layers (dangling)
- Runs daily
- Logs space freed

---

### 12. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| K3s + Cilium installation complexity | High | Medium | Comprehensive installer script with error handling and rollback. |
| SQLite write contention under high job concurrency | Medium | Medium | WAL mode hardcoded. Limit concurrent jobs via configuration. |
| kube-rs API changes break compatibility | Low | High | Pin kube-rs version. Integration tests against K3s. |
| Users expect multi-node scaling | Medium | Medium | Document single-node limitation clearly. Roadmap for V2. |
| Log files fill disk | High | High | Reaper component with configurable retention. Alerts on low disk space. |
| SecretService memory exhaustion from large secrets | Low | Medium | Limit secret size (max 64KB per secret). Monitor SecretService memory usage. |
| OpenTelemetry overhead impacts performance | Medium | Low | Make metrics/tracing optional. Use sampling for traces. |
| CaC repo sync failures leave system outdated | Medium | High | Periodic health checks detect stale sync. Manual sync command available. |
| Dex pod failure blocks all logins | Medium | High | Self-healing restarts Dex pod. Health checks monitor Dex status. |
| Registry fills disk with cached layers | High | Medium | Reaper deletes old images. Configurable size limits per pipeline. |
| Cilium CNI learning curve | Medium | Low | Comprehensive documentation. Installer handles setup automatically. |

---

### 13. Alternatives Considered

| Alternative | Verdict | Reason |
|-------------|---------|--------|
| PostgreSQL instead of SQLite | Discarded | Adds operational complexity. Single-node doesn't need it. |
| Master-side pipeline execution | Discarded | Security risk (RCE in main process). |
| Peer-to-peer container networking | Discarded | Requires complex CNI configuration. |
| Python object serialization (pickle) | Discarded | Dependency version hell. |
| Bare Docker orchestration | Discarded | Reinventing Kubernetes scheduling. |
| gVisor runtime | Deferred | Performance overhead for file-heavy builds. |
| Node.js BFF for frontend | Discarded | Adds second runtime. Complicates deployment. |
| Server-side rendering | Discarded | Unnecessary for authenticated dashboard. |
| Env var secret injection | **Replaced with SecretService** | Env vars visible in Pod specs. Poor auditability. |
| External OIDC providers (Auth0, Okta) | **Replaced with Dex** | Adds external dependency. Dex is self-hosted. |
| UI-based pipeline creation | **Replaced with CaC** | GitOps provides better auditability and version control. |
| Docker-in-Docker | **Replaced with Kaniko** | DinD requires privileged containers. Kaniko is unprivileged. |
| Stock K3s Flannel CNI | **Replaced with Cilium** | Flannel doesn't support DNS-based egress filtering (SLSA L3). |

---

### 14. Open Questions

None. All major architectural decisions have been resolved.

---

### 15. Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Author | | | |
| Tech Lead | | | |
| Security | | | |
| Platform | | | |

---

## Part 2: Implementation Plan (Jira-Style Tickets)

---

### Epic Structure

| Epic ID | Epic Name | Description |
|---------|-----------|-------------|
| DANUBE-E1 | Core Infrastructure | Rust project setup, configuration, database layer, logging, metrics |
| DANUBE-E2 | Kubernetes Integration | K8s client, Pod management, exec API, registry |
| DANUBE-E3 | Execution Engine | gRPC server, Coordinator SDK, job orchestration |
| DANUBE-E4 | Configuration as Code | Git sync, YAML parsing, pipeline extraction |
| DANUBE-E5 | API & Authentication | REST API, Dex OIDC, Team-based RBAC |
| DANUBE-E6 | Log Management | Streaming, storage, retention |
| DANUBE-E7 | Frontend | SolidJS SPA, embedding, real-time updates |
| DANUBE-E8 | Integrations | Git webhooks, notifications |
| DANUBE-E9 | Installer & Packaging | Binary distribution, K3s+Cilium bundling, health checks |

---

### Dependency Graph (Simplified)

```
DANUBE-1 (Workspace)
    │
    ├── DANUBE-90 (Signing Keys)
    │       │
    │       ├── DANUBE-91 (Provenance) ──┬── DANUBE-93 (Verification Tool)
    │       │                             │
    │       └── DANUBE-92 (Signatures) ───┘
    │
    ├── DANUBE-2 (Logging) ──┬── DANUBE-3 (Metrics/OTEL)
    │                         │       │
    │                         │       └── DANUBE-84 (Prometheus Setup)
    │                         │
    │                         └── DANUBE-4 (Config)
    │                                 │
    │                                 ├── DANUBE-5 (SQLite)
    │                                 │       │
    │                                 │       └── DANUBE-6 (Repos)
    │                                 │               │
    │                                 │               ├── DANUBE-7 (Encryption)
    │                                 │               │       │
    │                                 │               │       └── DANUBE-8 (SecretService)
    │                                 │               │
    │                                 │               ├── DANUBE-15 (CaC Watcher)
    │                                 │               │       │
    │                                 │               │       └── DANUBE-16 (YAML Parser)
    │                                 │               │
    │                                 │               └── DANUBE-44 (RBAC)
    │                                 │                       │
    │                                 │                       └── DANUBE-45 (Middleware)
    │                                 │
    │                                 └── DANUBE-9 (Axum)
    │                                         │
    │                                         ├── DANUBE-10 (K8s Client)
    │                                         │       │
    │                                         │       ├── DANUBE-11 (Pod Builder)
    │                                         │       │       │
    │                                         │       │       └── DANUBE-12 (Pod Lifecycle)
    │                                         │       │
    │                                         │       ├── DANUBE-13 (K8s Exec)
    │                                         │       │
    │                                         │       ├── DANUBE-14 (Registry)
    │                                         │       │       │
    │                                         │       │       └── DANUBE-53 (Registry GC)
    │                                         │       │
    │                                         │       └── DANUBE-87 (Dex)
    │                                         │               │
    │                                         │               └── DANUBE-88 (OIDC Integration)
    │                                         │                       │
    │                                         │                       └── DANUBE-89 (User Sync)
    │                                         │
    │                                         └── DANUBE-80 (Health Checks)
    │                                                 │
    │                                                 └── DANUBE-86 (Periodic Health + Self-Heal)
    │
    └── DANUBE-20 (Proto)
            │
            ├── DANUBE-21 (gRPC Server)
            │       │
            │       ├── DANUBE-17 (GitHub App Auth)
            │       │       │
            │       │       └── DANUBE-24 (Git Clone Multi-Auth)
            │       │               │
            │       │               └── DANUBE-25 (Job Orchestrator)
            │       │               │
            │       │               ├── DANUBE-30 (Metadata Extractor)
            │       │               │       │
            │       │               │       ├── DANUBE-31 (Cron Scheduler)
            │       │               │       │
            │       │               │       ├── DANUBE-32 (Webhook Router)
            │       │               │       │       │
            │       │               │       │       └── DANUBE-33 (Webhook Verification)
            │       │               │       │
            │       │               │       └── DANUBE-50/51/52 (Logs & Reaper)
            │       │               │
            │       │               ├── DANUBE-70 (Notifications)
            │       │               │
            │       │               └── DANUBE-85 (Job Draining)
            │       │
            │       └── DANUBE-22 (Python SDK)
            │               │
            │               ├── DANUBE-23 (Coordinator Image)
            │               │
            │               └── DANUBE-26 (Kaniko Support)

DANUBE-60 (Frontend Init)
    │
    └── DANUBE-61 (Shell/Routing)
            │
            ├── DANUBE-62 (Pipelines UI - Read-Only)
            │
            ├── DANUBE-63 (Jobs/Logs UI)
            │
            └── DANUBE-65 (Embed in Rust)

DANUBE-81 (Release Build - Self-Hosted)
    │
    ├── DANUBE-82 (Installer + Cilium)
    │
    └── DANUBE-83 (Docker Image)
```

---

### Milestone Summary

| Milestone | Tickets | Description | Estimated Time |
|-----------|---------|-------------|----------------|
| **M1: Foundation** | DANUBE-1 through DANUBE-9 | Rust workspace, logging, metrics, config, database, Axum | Week 1-2 (40h) |
| **M2: Kubernetes Core** | DANUBE-10 through DANUBE-14, DANUBE-17 | K8s client, Pod management, exec API, registry, GitHub App auth | Week 2-4 (35h) |
| **M3: Execution Engine** | DANUBE-20 through DANUBE-26 | gRPC, SDK, orchestrator, Kaniko | Week 4-7 (57h) |
| **M4: Configuration as Code** | DANUBE-15, DANUBE-16, DANUBE-30 through DANUBE-33 | CaC sync, YAML parsing, webhooks, cron | Week 7-9 (35h) |
| **M5: Auth & RBAC** | DANUBE-87 through DANUBE-89, DANUBE-44, DANUBE-45, DANUBE-41 through DANUBE-43 | Dex, OIDC, teams, permissions, API | Week 9-11 (40h) |
| **M6: Logs & Artifacts** | DANUBE-50 through DANUBE-53, DANUBE-90 through DANUBE-93 | Log streaming, reaper, registry GC, provenance & signing | Week 11-12 (30h) |
| **M7: Frontend** | DANUBE-60 through DANUBE-65 | SolidJS SPA, routing, log viewer, embedding | Week 12-14 (25h) |
| **M8: Integrations** | DANUBE-70 | Notifications | Week 14 (4h) |
| **M9: Operations** | DANUBE-80, DANUBE-85, DANUBE-86 | Health checks, draining, self-healing | Week 14-15 (14h) |
| **M10: Packaging** | DANUBE-81 through DANUBE-84 | Self-hosted build, installer, Docker, Prometheus | Week 15-17 (23h) |

**Total Estimated Time: ~302 hours (~19 weeks at 16h/week, or ~12-13 weeks full-time)**

---

### Key Features Summary

✅ **Configuration as Code (GitOps)**

- All configuration in Git repository
- Declarative YAML for pipelines, teams, users
- Version controlled, auditable, rollback-friendly

✅ **Team-Based RBAC**

- Users belong to teams
- Teams have permissions on pipelines
- Dex OIDC for authentication

✅ **SLSA Level 3 Compliance**

- Hermetic builds with Cilium DNS-based egress filtering
- Ephemeral environments (pods deleted after execution)
- Signed provenance generation (in-toto format, Ed25519 signatures)
- Artifact signing with verification tool
- SecretService (no env var leakage)

✅ **Container Image Caching**

- Internal container registry
- Kaniko for unprivileged image builds
- Layer caching for fast builds

✅ **Self-Hosted & Single-Binary**

- No external dependencies (except K3s)
- Embedded frontend, Dex, registry
- SQLite with WAL mode
- Runs on 2 vCPU / 4GB RAM

✅ **Developer Experience**

- Python-native pipelines (danubefile.py)
- IDE support (type hints, autocomplete)
- Real-time log streaming
- Artifact upload/download

✅ **Operations**

- Self-healing health checks
- Job draining for upgrades
- OpenTelemetry metrics & tracing
- Prometheus integration

---

This completes the comprehensive Architecture Design Document and Implementation Plan for **Danube**.
