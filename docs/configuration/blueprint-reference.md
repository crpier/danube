# Blueprint (GitOps) Reference

## Overview

All Danube configuration is managed through a Git repository containing declarative JSON files. The Blueprint repository is the source of truth for pipelines, users, teams, permissions, retention, networking policy, and global appliance settings.

## `danubefile.py` Quick Example

```python
from danube import pipeline, step

@pipeline(name="Build")
def build():
    step.run("npm ci")
    step.run("npm test")
```

## Repository Structure

```text
danube-blueprint/
├── config.json
├── users.json
├── teams.json
└── pipelines/
    ├── frontend-build.json
    ├── backend-build.json
    └── deploy-prod.json
```

## Global Configuration (`config.json`)

```json
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {
    "name": "global"
  },
  "spec": {
    "server": {
      "bind_address": "0.0.0.0:8080",
      "rpc_address": "127.0.0.1:9000",
      "data_dir": "/var/lib/danube"
    },
    "runner": {
      "type": "local",
      "runtime": "podman",
      "coordinator_image": "danube-coordinator:latest",
      "max_concurrent_jobs": 4,
      "default_worker_resources": {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "2000m", "memory": "2Gi"}
      }
    },
    "retention": {
      "logs_days": 30,
      "artifacts_days": 14,
      "registry_images_days": 30,
      "workspaces_days": 0
    },
    "networking": {
      "default_deny_egress": true,
      "egress_proxy_enabled": true,
      "egress_allowlist": [
        "github.com",
        "*.githubusercontent.com",
        "registry.npmjs.org",
        "pypi.org",
        "registry.local"
      ]
    },
    "observability": {
      "otel_endpoint": "http://otel-collector:4317",
      "metrics_enabled": true,
      "traces_enabled": true
    },
    "git_authentication": [
      {
        "type": "ssh_key",
        "name": "fallback",
        "private_key_path": "/var/lib/danube/keys/git_fallback_key",
        "match_patterns": ["*"]
      }
    ]
  }
}
```

The initial local runner supports `runtime: "podman"`. Danube manages Podman rootless mode and creates one Podman pod per job.

## User Definitions (`users.json`)

```json
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "User",
    "metadata": {
      "name": "alice"
    },
    "spec": {
      "email": "alice@example.com",
      "password_hash": "$2b$12$KIXxKj5M..."
    }
  }
]
```

Generate password hashes with:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
```

## Team Definitions (`teams.json`)

```json
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "Team",
    "metadata": {
      "name": "engineering"
    },
    "spec": {
      "members": ["alice@example.com", "bob@example.com"]
    }
  },
  {
    "apiVersion": "danube.dev/v1",
    "kind": "Team",
    "metadata": {
      "name": "platform"
    },
    "spec": {
      "members": ["alice@example.com"],
      "global_admin": true
    }
  }
]
```

## Pipeline Definition (`pipelines/frontend-build.json`)

```json
{
  "apiVersion": "danube.dev/v1",
  "kind": "Pipeline",
  "metadata": {
    "name": "frontend-build",
    "team": "engineering"
  },
  "spec": {
    "repository": "https://github.com/myorg/frontend",
    "branch_filter": ["main", "develop", "release/*"],
    "triggers": [
      {"on": "push", "branches": ["main", "develop"]},
      {"on": "pull_request"},
      {"on": "cron", "schedule": "0 0 * * *"}
    ],
    "script": "danubefile.py",
    "max_duration_seconds": 3600,
    "workspace_size_gb": 10,
    "worker": {
      "image": "node:20-alpine",
      "resources": {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "2000m", "memory": "2Gi"}
      }
    },
    "networking": {
      "egress_allowlist": ["registry.npmjs.org", "github.com"]
    },
    "permissions": [
      {"team": "engineering", "level": "admin"},
      {"team": "qa", "level": "read"}
    ]
  }
}
```

Pipeline-level networking settings narrow or extend global policy according to the server's configured rules.

## Pipeline Script (`danubefile.py` in app repo)

```python
from danube import pipeline, step, ctx, secrets, artifacts

@pipeline(name="Frontend Build")
def build():
    print(f"Building {ctx.repo} on {ctx.branch}")
    print(f"Commit: {ctx.commit_sha}")

    step.run("npm ci", name="Install Dependencies")

    exit_code = step.run(
        "npm test -- --coverage",
        name="Run Tests",
        check=False,
    )

    artifacts.upload("coverage/", name="coverage-report")

    if exit_code != 0:
        print("Tests failed, skipping build")
        return

    if ctx.branch == "main":
        step.run("npm run build", name="Build Production")
        artifacts.upload("dist/", name="production-build")

        token = secrets.get("DEPLOY_TOKEN")
        step.run(
            "./deploy.sh",
            name="Deploy",
            env={"DEPLOY_TOKEN": token},
        )
```

## Context Variables

| Variable | Type | Description |
|----------|------|-------------|
| `ctx.job_id` | str | Unique job ID |
| `ctx.pipeline` | str | Pipeline name |
| `ctx.repo` | str | Repository URL |
| `ctx.branch` | str | Git branch |
| `ctx.commit_sha` | str | Full commit SHA |
| `ctx.trigger_type` | str | `webhook`, `cron`, or `manual` |
| `ctx.trigger_ref` | str | Ref that triggered the job |
| `ctx.workspace` | str | Workspace path, usually `/workspace` |

## SDK API Reference

### `step.run()`

```python
step.run(
    command: str,
    name: str | None = None,
    image: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    timeout: int | None = None,
) -> int | str
```

Returns an exit code unless `capture=True`, in which case it returns stdout.

### `secrets.get()`

```python
secrets.get(key: str) -> str
```

Retrieves a secret through the Master SecretService.

### `artifacts.upload()`

```python
artifacts.upload(path: str, name: str | None = None) -> None
```

Uploads a file or directory as a job artifact.

## Validation

Blueprint sync validates:

- JSON Schema
- duplicate names
- referenced teams/users/pipelines
- permission levels
- cron expressions
- runner/runtime values
- egress allowlist syntax

If validation fails, sync is aborted and the previous active configuration remains in use.

## Best Practices

1. Keep Blueprint changes reviewed through pull requests.
2. Prefer pipeline-specific secrets over global secrets.
3. Keep Worker images minimal and scanned.
4. Chain shell commands when directory or environment state matters.
5. Use internal mirrors for high-security or repeatable builds.
6. Keep egress allowlists narrow.
7. Upload required outputs before the job ends.
