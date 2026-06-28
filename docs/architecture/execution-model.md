# Execution Model

## Job Environment Pattern

Each pipeline execution creates an ephemeral local job environment with two containers and one shared workspace.

| Container | Image | Role |
|-----------|-------|------|
| **Coordinator** | Danube-provided Python image | Executes `danubefile.py`, calls Master RPC |
| **Worker** | User-defined image | Receives shell commands from Master through the runner |

The containers are managed by Danube's local runner using rootless Podman. Each Danube job maps to one Podman pod containing the Coordinator and Worker containers.

## Why Two Containers?

- **Separation of concerns**: Pipeline control code lives in Coordinator; build tools live in Worker.
- **Security**: Coordinator does not directly execute shell commands.
- **Flexibility**: Users choose Worker images per pipeline or step.
- **Clean logging/control**: Master mediates every command and captures output centrally.

## Execution Flow

```text
1. Webhook, cron, or manual trigger creates job
        │
        ▼
2. Master records job as pending
        │
        ▼
3. Master asks Local Runner to create job environment
        │
        ▼
4. Runner creates workspace and one Podman pod with Coordinator and Worker containers
        │
        ▼
5. Coordinator starts and imports danubefile.py
        │
        ▼
6. User code calls step.run("npm install")
        │
        ▼
7. Coordinator → Master RPC: RunStep(command="npm install")
        │
        ▼
8. Master → Runner: exec command in Worker
        │
        ▼
9. Runner streams stdout/stderr back to Master
        │
        ▼
10. Master writes logs to disk and streams to UI clients
        │
        ▼
11. Step completes; Master returns exit code
        │
        ▼
12. Coordinator continues or fails pipeline
        │
        ▼
13. Job finishes, times out, or is cancelled
        │
        ▼
14. Master stores final state, artifacts, provenance
        │
        ▼
15. Runner deletes containers/network/temp state
```

## Communication Strategy

All control flows through the Master:

```text
Coordinator ──HTTP/JSON──▶ Master ──runtime exec──▶ Worker
```

There is no direct Coordinator → Worker command channel.

### Benefits

- Centralized authorization
- Centralized log capture
- Consistent timeout/cancellation behavior
- Single audit point for command execution
- Runner backend can change without changing pipeline semantics

## State Management

| State Type | Location | Scope | Persistence |
|------------|----------|-------|-------------|
| Python variables | Coordinator memory | Pipeline execution | Ephemeral |
| Shell variables | Worker process | Single command | Does not persist |
| Environment variables | Passed via `env={}` | Per step | Per command |
| Files | `/workspace` | Job | Deleted after job unless uploaded |
| Captured output | RPC response | Per step | Optional |
| Logs | Master log file | Job | Retained by policy |

## Stateless Shell Execution

Each `step.run()` starts a fresh shell process in the Worker.

```python
# Wrong: cd does not persist
step.run("cd /app")
step.run("npm install")

# Correct: chain commands
step.run("cd /app && npm install")
```

Environment variables are also per-command:

```python
step.run(
    "echo $API_KEY",
    env={"API_KEY": secrets.get("API_KEY")},
)
```

## Workspace

Every job gets a private workspace directory under the Danube data directory, mounted into both containers at `/workspace`.

- Coordinator can write generated files.
- Worker runs commands and writes build outputs.
- Artifacts must be uploaded before job cleanup.
- Workspace is deleted after job completion unless retention/debug settings preserve it.

## Secrets Access

Secrets are not injected into container manifests by default. They are fetched on demand:

```python
from danube import secrets, step

api_key = secrets.get("API_KEY")
step.run(
    "curl -H 'Authorization: Bearer $API_KEY' https://api.example.com",
    env={"API_KEY": api_key},
)
```

Master validates:

1. Job is active
2. Pipeline has access to the secret
3. Secret is requested through the Coordinator RPC path

Secrets are scrubbed from logs before storage and streaming.

## Artifact Upload

```python
from danube import artifacts

artifacts.upload("dist/app.tar.gz", name="app-bundle")
artifacts.upload("coverage/", name="coverage-report")
```

Artifacts are stored under:

```text
/var/lib/danube/artifacts/<job_id>/<artifact_name>
```

## Container Image Building

Container image builds should use unprivileged build tools where possible, such as BuildKit rootless, Kaniko, Buildah, or runtime-supported build commands.

Example:

```python
step.run(
    "buildctl build "
    "--frontend=dockerfile.v0 "
    "--local context=/workspace "
    "--local dockerfile=/workspace "
    "--output type=image,name=registry.local/myapp:latest,push=true",
    name="Build Image",
)
```

The chosen build strategy must not require privileged containers by default.

## Job Lifecycle States

```text
pending → scheduling → running → [success | failure | timeout | cancelled]
```

- **pending**: Job created, waiting for runner capacity
- **scheduling**: Runner is creating workspace/containers/network
- **running**: Coordinator is executing pipeline
- **success**: Pipeline completed successfully
- **failure**: A step or runtime operation failed
- **timeout**: Job exceeded `max_duration_seconds`
- **cancelled**: User or operator cancelled the job

## Timeouts

Configured per pipeline:

```json
{
  "spec": {
    "max_duration_seconds": 3600
  }
}
```

On timeout, Master:

1. Marks cancellation reason
2. Asks runner to stop containers
3. Kills remaining runtime processes if needed
4. Finalizes logs
5. Marks job `timeout`
6. Runs cleanup

## Error Handling

### Step Failure

By default, a non-zero step exits the pipeline:

```python
step.run("npm test")
step.run("npm run build")  # not reached if tests fail
```

Continue manually:

```python
exit_code = step.run("npm test", check=False)
if exit_code == 0:
    step.run("npm run build")
```

### Coordinator Failure

If Coordinator exits unexpectedly:

- Master marks job `failure`
- Runner stops Worker
- Logs and artifacts already received remain available
- Cleanup runs

### Runner Failure

If the local runner cannot create, exec, or clean containers:

- Master records the failure reason
- Job moves to `failure`
- Reaper later retries cleanup of stale runtime state

## Resource Limits

Pipeline config may define Worker limits:

```json
{
  "spec": {
    "worker": {
      "resources": {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "2000m", "memory": "2Gi"}
      }
    }
  }
}
```

The local runner maps these values to Podman's CPU, memory, and process-limit controls.

## Concurrency

The Master can run multiple jobs concurrently on the same host. Each job receives separate containers, workspace, and network controls.

Capacity is bounded by:

- host CPU/RAM/disk
- rootless Podman runtime limits
- configured global concurrency
- per-pipeline concurrency policy
- SQLite write contention
- log I/O throughput

Default behavior should include a conservative global concurrency limit rather than relying on unbounded host scheduling. The Scheduler enforces this cap on the shared enqueue path: triggers beyond the cap stay `pending` and are dispatched to the orchestrator as running jobs finish. The per-pipeline policy is dedup by pipeline/ref — at most one active job per pipeline/ref at a time (see `components.md`, Scheduler).
