# Execution Model

## Pod Pattern

Each pipeline execution spawns a single Kubernetes Pod containing two containers:

| Container | Image | Role |
|-----------|-------|------|
| **Coordinator** | `danube-coordinator:latest` | Executes `danubefile.py`, calls Master via gRPC |
| **Worker** | User-defined (e.g., `node:18`, Kaniko) | Receives shell commands from Master via K8s Exec API |

### Why Two Containers?

- **Security**: Coordinator has no shell access; can't execute arbitrary code
- **Isolation**: Build tools live in Worker, SDK lives in Coordinator
- **Flexibility**: Users choose Worker image (any base image)

## Execution Flow

```
1. Webhook arrives or cron triggers pipeline
         │
         ▼
2. Master creates Pod manifest (Coordinator + Worker)
         │
         ▼
3. K3s schedules Pod, pulls images
         │
         ▼
4. Coordinator starts, imports danubefile.py
         │
         ▼
5. User code calls step.run("npm install")
         │
         ▼
6. Coordinator → gRPC → Master: RunStep(cmd="npm install")
         │
         ▼
7. Master → K8s Exec API: Run in Worker container
         │
         ▼
8. Master streams stdout/stderr → disk + SSE (UI)
         │
         ▼
9. Step completes, Master returns exit code
         │
         ▼
10. Coordinator continues or exits if step failed
         │
         ▼
11. Master detects Pod exit, updates job status
         │
         ▼
12. Master deletes Pod (ephemeral)
```

## Networking Strategy

All communication flows through the Master (hub-and-spoke):

```
Coordinator ──gRPC──▶ Master ──K8s Exec──▶ Worker
     ▲                  │
     │                  │
     └──────────────────┘
          (Response)
```

**No direct Coordinator ↔ Worker communication.**

### Benefits

- No service discovery needed
- Centralized authentication (only Master has K8s credentials)
- Single point for log aggregation
- Simplified network policies

## State Management

| State Type | Location | Scope | Persistence |
|------------|----------|-------|-------------|
| Python variables | Coordinator memory | Pipeline execution | Ephemeral |
| Shell variables | Worker process | Single command | Does NOT persist |
| Environment variables | Passed via `env={}` | Per-step | Per-command |
| Captured output | gRPC response | Per-step | Returned to Coordinator |

### Shell Execution Model: Stateless

**Each `step.run()` spawns a fresh `/bin/sh -c` process.**

```python
# ❌ WRONG: cd does not persist
step.run("cd /app")
step.run("npm install")  # Runs in /, not /app

# ✅ CORRECT: Chain commands
step.run("cd /app && npm install")

# ✅ ALSO CORRECT: Use Python state
step.run("cd /app && pwd", capture=True)  # Returns "/app"
app_dir = "/app"
step.run(f"cd {app_dir} && npm install")
```

### Environment Variables

Passed explicitly per-step:

```python
step.run(
    "echo $API_KEY",
    env={"API_KEY": secrets.get("API_KEY")}
)
```

**Environment variables do NOT persist between steps.**

## Workspace

Pipeline Pods have a shared volume mounted at `/workspace` in both containers.

- Coordinator can write files (e.g., generated config)
- Worker reads files, runs builds, writes artifacts
- Volume deleted when Pod is deleted

## Secrets Access

Secrets are NOT injected as environment variables. Instead:

```python
from danube import secrets

# Coordinator calls Master via gRPC
api_key = secrets.get("API_KEY")

# Use in commands
step.run(f"curl -H 'Authorization: Bearer {api_key}' https://api.example.com")
```

Master validates:
1. Job is active
2. Job's pipeline has access to secret
3. Returns decrypted value

Secrets never appear in:
- Pod environment variables
- Pod YAML manifests
- Log files (scrubbed by Master)

## Artifact Upload

```python
from danube import artifacts

# Upload single file
artifacts.upload("dist/app.tar.gz", name="app-bundle")

# Upload directory
artifacts.upload("coverage/", name="coverage-report")
```

Artifacts stored in `/var/lib/danube/artifacts/<job_id>/<artifact_name>`.

## Container Image Building

Use Kaniko for unprivileged Docker builds:

```python
step.run(
    "/kaniko/executor "
    "--context=/workspace "
    "--dockerfile=Dockerfile "
    "--destination=registry.danube-system:5000/myapp:latest "
    "--cache=true",
    name="Build Image",
    image="gcr.io/kaniko-project/executor:latest"
)
```

Kaniko:
- Runs without Docker daemon
- Supports layer caching
- Outputs to internal registry
- SLSA L3 compliant (hermetic)

## Job Lifecycle States

```
pending → scheduling → running → [success | failure | timeout | cancelled]
```

- **pending**: Job created, waiting for Pod creation
- **scheduling**: Pod submitted to K8s, waiting for scheduling
- **running**: Pod running, Coordinator executing pipeline
- **success**: Pipeline completed with exit code 0
- **failure**: Step exited non-zero
- **timeout**: Exceeded `max_duration_seconds`
- **cancelled**: User cancelled via API

## Timeouts

Configured per-pipeline in CaC YAML:

```yaml
spec:
  max_duration_seconds: 3600  # 1 hour
```

Master monitors job duration. If exceeded:
1. Delete Pod immediately
2. Mark job as `timeout`
3. Log timeout event

## Error Handling

### Step Failure

By default, if a step exits non-zero, pipeline stops:

```python
step.run("npm test")  # If fails, pipeline stops here
step.run("npm run build")  # Not executed
```

Continue on failure:

```python
exit_code = step.run("npm test", check=False)
if exit_code != 0:
    print("Tests failed, skipping build")
else:
    step.run("npm run build")
```

### Network Failures

If Master ↔ Coordinator gRPC connection breaks:
- Coordinator retries for 30 seconds
- If still failing, Coordinator exits with error
- Master marks job as `failure`
- Pod deleted

## Resource Limits

Pod resource requests and limits:

```yaml
# Hard-coded defaults (TODO: make configurable)
resources:
  requests:
    cpu: "500m"
    memory: "512Mi"
  limits:
    cpu: "2000m"
    memory: "2Gi"
```

If Worker exceeds memory limit, K8s kills container → job marked `failure`.

## Concurrency

Master can run multiple jobs concurrently. Each job gets its own Pod.

**Considerations**:
- K8s cluster capacity limits concurrent pods
- SQLite write contention (WAL mode helps)
- Log streaming overhead (SSE connections)

**Default**: No hard limit, rely on K8s scheduling.

**Future**: Configurable max concurrent jobs per pipeline or globally.
