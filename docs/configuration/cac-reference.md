# Configuration as Code Reference

## Overview

All Danube configuration (pipelines, users, teams, global settings) is managed via a Git repository using declarative YAML files.

## Repository Structure

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

## Global Configuration (config.yaml)

```yaml
apiVersion: danube.dev/v1
kind: Config
metadata:
  name: global

spec:
  # Server settings (optional overrides)
  server:
    bind_address: "0.0.0.0:8080"
    data_dir: "/var/lib/danube"

  # Kubernetes settings
  kubernetes:
    namespace_jobs: "danube-jobs"
    coordinator_image: "danube-coordinator:latest"

  # Retention policies
  retention:
    logs_days: 30
    artifacts_days: 14
    registry_images_days: 30

  # Observability
  observability:
    otel_endpoint: "http://otel-collector:4317"
    metrics_enabled: true
    traces_enabled: true

  # Egress allowlist (DNS-based filtering via Cilium)
  egress_allowlist:
    - "registry.npmjs.org"
    - "pypi.org"
    - "*.github.com"
    - "registry.danube-system"

  # Git authentication for pipeline repositories
  git_authentication:
    # GitHub App
    - type: github_app
      name: myorg-app
      app_id: "123456"
      installation_id: "78910"
      private_key_secret: "github-app-myorg-key"
      match_patterns:
        - "github.com/myorg/*"

    # GitLab token
    - type: gitlab_token
      name: gitlab-internal
      url: "https://gitlab.company.com"
      token_secret: "gitlab-token"
      match_patterns:
        - "gitlab.company.com/*"

    # SSH key fallback
    - type: ssh_key
      name: fallback
      private_key_path: "/var/lib/danube/keys/git_fallback_key"
      match_patterns:
        - "*"
```

## User Definitions (users.yaml)

```yaml
apiVersion: danube.dev/v1
kind: User
metadata:
  name: alice
spec:
  email: alice@example.com
  # Generate with: python -c "import bcrypt; print(bcrypt.hashpw(b'password', bcrypt.gensalt()).decode())"
  password_hash: "$2b$12$KIXxKj5M..."

---
apiVersion: danube.dev/v1
kind: User
metadata:
  name: bob
spec:
  email: bob@example.com
  password_hash: "$2b$12$..."
```

### Password Hash Generation

```bash
# Using Python
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"

# Using htpasswd (if installed)
htpasswd -bnBC 12 "" YOUR_PASSWORD | tr -d ':\n'
```

## Team Definitions (teams.yaml)

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

## Pipeline Definition (pipelines/frontend-build.yaml)

```yaml
apiVersion: danube.dev/v1
kind: Pipeline
metadata:
  name: frontend-build
  team: engineering  # Owning team

spec:
  # Git repository
  repository: https://github.com/myorg/frontend
  branch_filter:
    - "main"
    - "develop"
    - "release/*"  # Supports glob patterns

  # Triggers
  triggers:
    - on: push
      branches:
        - "main"
        - "develop"
    
    - on: pull_request
    
    - on: cron
      schedule: "0 0 * * *"  # Daily at midnight UTC

  # Pipeline script in app repo
  script: danubefile.py

  # Execution limits
  max_duration_seconds: 3600  # 1 hour timeout
  workspace_size_gb: 10       # Workspace volume size

  # Worker container
  worker:
    image: node:18-alpine
    resources:
      requests:
        cpu: "500m"
        memory: "512Mi"
      limits:
        cpu: "2000m"
        memory: "2Gi"

  # Permissions
  permissions:
    - team: engineering
      level: admin
    - team: qa
      level: read
```

## Pipeline Script (danubefile.py in app repo)

```python
from danube import pipeline, step, ctx, secrets, artifacts

@pipeline(name="Frontend Build")
def build():
    # Context variables available
    print(f"Building {ctx.repo} on {ctx.branch}")
    print(f"Commit: {ctx.commit_sha}")
    print(f"Trigger: {ctx.trigger_type}")  # webhook, cron, manual
    
    # Step 1: Install dependencies
    step.run(
        "npm ci",
        name="Install Dependencies"
    )
    
    # Step 2: Run tests
    exit_code = step.run(
        "npm test -- --coverage",
        name="Run Tests",
        check=False  # Don't fail pipeline if tests fail
    )
    
    # Upload coverage regardless of test result
    artifacts.upload("coverage/", name="coverage-report")
    
    if exit_code != 0:
        print("Tests failed, skipping build")
        return
    
    # Step 3: Build (only on main branch)
    if ctx.branch == "main":
        step.run(
            "npm run build",
            name="Build Production"
        )
        
        artifacts.upload("dist/", name="production-build")
        
        # Step 4: Build Docker image with Kaniko
        api_token = secrets.get("DOCKER_HUB_TOKEN")
        
        step.run(
            f"/kaniko/executor "
            f"--context=/workspace "
            f"--dockerfile=Dockerfile "
            f"--destination=myorg/frontend:{ctx.commit_sha[:7]} "
            f"--destination=myorg/frontend:latest "
            f"--cache=true",
            name="Build Container Image",
            image="gcr.io/kaniko-project/executor:latest",
            env={"DOCKER_CONFIG": "/kaniko/.docker"}
        )
```

## Context Variables

Available in `danubefile.py` via `ctx` object:

| Variable | Type | Description | Example |
|----------|------|-------------|---------|
| `ctx.job_id` | str | Unique job ID | `"abc123"` |
| `ctx.pipeline` | str | Pipeline name | `"frontend-build"` |
| `ctx.repo` | str | Repository URL | `"github.com/myorg/frontend"` |
| `ctx.branch` | str | Git branch | `"main"` |
| `ctx.commit_sha` | str | Full commit SHA | `"abc123def456..."` |
| `ctx.trigger_type` | str | Trigger type | `"webhook"`, `"cron"`, `"manual"` |
| `ctx.trigger_ref` | str | Ref that triggered job | `"refs/heads/main"` |
| `ctx.workspace` | str | Workspace path | `"/workspace"` |

## SDK API Reference

### step.run()

Execute command in Worker container.

```python
step.run(
    command: str,                   # Shell command to execute
    name: str | None = None,        # Step name (for UI)
    image: str | None = None,       # Override Worker image
    env: dict[str, str] | None = None,  # Environment variables
    check: bool = True,             # Raise on non-zero exit
    capture: bool = False,          # Return stdout as string
    timeout: int | None = None      # Timeout in seconds
) -> int | str
```

**Returns**:
- `int`: Exit code (if `capture=False`)
- `str`: Captured stdout (if `capture=True`)

**Example**:
```python
# Basic execution
step.run("npm install")

# With environment variable
step.run("echo $API_KEY", env={"API_KEY": "secret"})

# Capture output
version = step.run("node --version", capture=True)
print(f"Node version: {version}")

# Continue on failure
exit_code = step.run("npm test", check=False)
if exit_code != 0:
    print("Tests failed")
```

### secrets.get()

Retrieve secret from SecretService.

```python
secrets.get(key: str) -> str
```

**Example**:
```python
api_key = secrets.get("API_KEY")
db_password = secrets.get("DB_PASSWORD")
```

Secrets must be defined via API or CaC (future feature).

### artifacts.upload()

Upload file or directory as job artifact.

```python
artifacts.upload(
    path: str,              # File or directory path
    name: str | None = None # Artifact name (default: basename)
) -> None
```

**Example**:
```python
# Upload single file
artifacts.upload("app.tar.gz", name="app-bundle")

# Upload directory
artifacts.upload("coverage/", name="coverage-report")

# Multiple artifacts
artifacts.upload("dist/app.js", name="app-js")
artifacts.upload("dist/app.css", name="app-css")
```

## Validation

CaC YAML files are validated on sync:

- Schema validation (Pydantic models)
- Reference validation (teams exist, pipelines reference valid teams)
- Duplicate detection (no duplicate pipeline names)

If validation fails, sync aborted and error logged. Previous configuration remains active.

## Migration from Other CI/CD Systems

### From GitHub Actions

```yaml
# .github/workflows/build.yml
name: Build
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: npm install
      - run: npm test
```

**Equivalent danubefile.py**:
```python
from danube import pipeline, step

@pipeline(name="Build")
def build():
    step.run("npm install")
    step.run("npm test")
```

### From GitLab CI

```yaml
# .gitlab-ci.yml
build:
  script:
    - npm install
    - npm test
  only:
    - main
```

**Equivalent**:
```python
from danube import pipeline, step, ctx

@pipeline(name="Build")
def build():
    if ctx.branch != "main":
        return  # Skip on non-main branches
    
    step.run("npm install")
    step.run("npm test")
```

## Best Practices

1. **Separate concerns**: Use multiple pipelines for different workflows (build, test, deploy)
2. **Fail fast**: Place quickest checks first (linting before full test suite)
3. **Cache dependencies**: Use Kaniko caching for Docker builds
4. **Parameterize**: Use secrets for credentials, not hardcoded values
5. **Test locally**: Run `danubefile.py` with Danube CLI before pushing
6. **Version control**: Treat CaC repo as infrastructure code (review PRs, require approvals)
