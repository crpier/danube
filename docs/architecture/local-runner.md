# Local Runner Architecture

## Overview

Danube's default execution backend is a rootless Podman local runner. The runner is responsible for translating Danube job semantics into Podman API operations on the appliance host.

The initial implementation supports one runtime:

- **Runtime**: Podman
- **Mode**: rootless
- **Integration**: Podman API only
- **Job unit**: one Podman pod per Danube job

## Responsibilities

The local runner owns:

- per-job workspace creation
- Podman pod creation
- Coordinator container creation
- Worker container creation
- command execution in Worker
- stdout/stderr streaming
- timeout/cancellation handling
- pod/container cleanup
- stale state reconciliation
- runtime health reporting

The runner does not implement container isolation itself. Podman and the Linux kernel provide namespaces, cgroups, mounts, seccomp/AppArmor integration, and container lifecycle primitives.

## Job Mapping

```text
Danube Job
  └── Podman Pod: danube-job-<job_id>
        ├── Coordinator container
        └── Worker container
```

Both containers mount the same workspace:

```text
/var/lib/danube/workspaces/<job_id>  →  /workspace
```

Recommended Podman labels on all created resources:

```text
io.danube.job_id=<job_id>
io.danube.pipeline_id=<pipeline_id>
io.danube.resource=pod|coordinator|worker
io.danube.managed=true
```

Labels are required for reconciliation, cleanup, debugging, and metrics.

## Podman API

Danube should use the Podman service/API rather than shelling out to the `podman` CLI.

Required API capabilities:

- runtime health/version inspection
- image pull/inspect
- pod create/inspect/start/stop/remove
- container create/start/inspect/remove
- exec create/start/inspect
- attach or stream stdout/stderr
- logs/events where needed for reconciliation

The implementation should wrap Podman API details behind a small adapter so the orchestrator depends only on Danube runner interfaces.

## Rootless Runtime Model

Danube runs as a dedicated `danube` user. Rootless Podman containers run under that user.

Installer responsibilities:

- create `danube` user
- configure `/etc/subuid` and `/etc/subgid`
- ensure rootless Podman works for `danube`
- configure Podman API service/socket for the `danube` user
- ensure storage paths are owned by `danube`
- validate runtime health through `danube doctor`

## Runner Interface Shape

The Master should depend on a runner interface similar to:

```python
class Runner:
    async def start_job(self, request: StartJobRequest) -> JobHandle: ...
    async def exec_step(self, job: JobHandle, request: ExecStepRequest) -> ExecResult: ...
    async def start_coordinator(self, job: JobHandle, env: Mapping[str, str]) -> None: ...
    async def wait_for_coordinator(self, job: JobHandle) -> CoordinatorExit: ...
    async def stop_job(self, job: JobHandle, reason: str) -> None: ...
    async def cleanup_job(self, job: JobHandle) -> None: ...
    async def reconcile(self) -> ReconcileReport: ...
    async def health(self) -> RunnerHealth: ...
```

The runner interface should expose Danube concepts, not Podman concepts.

`start_job` creates the pod with both containers idling. The Master then opens the
job's RPC session and calls `start_coordinator`, which launches the Coordinator
entrypoint (`python -m danube.coordinator`) with the SDK connection environment
(`DANUBE_RPC_ADDRESS`, `DANUBE_JOB_ID`, `DANUBE_RPC_TOKEN`) plus the run-metadata
variables behind the `danube.context` surface (`DANUBE_PIPELINE`,
`DANUBE_TRIGGER_TYPE`, and `DANUBE_TRIGGER_REF` when the job has a Git ref).
`wait_for_coordinator`
blocks until that process exits and reports its `CoordinatorExit` (the exit code is
the crash signal when no terminal `/rpc/report-status` arrived). In the
`LocalContainerRunner` these are a Podman exec of the entrypoint in the Coordinator
container; the Coordinator drives Worker steps only over the Master RPC, never
directly. Because the Master's RPC is already serving before `start_coordinator`,
Coordinator readiness needs no separate handshake.

## Execution Flow

```text
1. Master creates job record
2. Master calls Runner.start_job
3. Runner creates workspace
4. Runner creates Podman pod with Danube labels
5. Runner creates Worker container in pod
6. Runner creates Coordinator container in pod
7. Runner starts containers (both idle)
8. Master opens the RPC session and calls Runner.start_coordinator
9. Runner launches `python -m danube.coordinator` with the SDK connection env
10. Master calls Runner.wait_for_coordinator and blocks on the pipeline
11. Coordinator imports danubefile.py and calls Master RPC per step
12. Master calls Runner.exec_step
13. Runner creates Podman exec session in Worker
14. Runner streams stdout/stderr to Master
15. Master records step result
16. Coordinator exits; wait_for_coordinator returns its exit code
17. Master calls Runner.cleanup_job when job ends
18. Runner removes pod, containers, temporary network state, and workspace
```

## Security Defaults

The runner should request secure Podman settings by default:

- rootless execution
- no privileged containers
- no host network
- no host PID namespace
- no host IPC namespace
- no arbitrary host mounts
- drop unnecessary capabilities
- set CPU, memory, and pids limits
- mount only the per-job workspace plus required read-only assets
- use read-only root filesystem where practical, with explicit tmpfs scratch (`/tmp`, `/run`, `/var/tmp`) mounted so build tooling that writes to `/tmp` still works

## Networking

Each Podman pod is the job network boundary.

Initial networking goals:

- Coordinator can reach Master RPC
- Worker can reach Master/local services as required
- direct internet egress is denied by default
- allowed external HTTP(S) traffic goes through Danube egress proxy
- cleanup removes any job-specific network state

Rootless networking behavior must be validated early because it is the riskiest part of the runner design.

## Reconciliation

Danube must handle crashes and partial cleanup. Reconciliation compares database `runner_state` with actual Podman resources labeled `io.danube.managed=true`.

Reconciliation should detect:

- pod exists but job is finished
- container exists but DB record is missing
- workspace exists for non-active job
- job is running in DB but pod is gone
- failed cleanup attempts

The CLI command `danube runner reconcile` should expose this behavior to operators.

## First Spike

The first runner development slice should prove:

1. rootless Podman API connection
2. create one pod per job
3. create Coordinator and Worker containers in the pod
4. mount shared workspace
5. exec command in Worker
6. stream stdout/stderr
7. cleanup pod/containers/workspace
8. inspect/reconcile labeled resources

Egress enforcement should be prototyped before building higher-level product features.

## Implementation Notes

The first spike is implemented as:

- `danube/runner/podman/adapter.py` — `PodmanAdapter`, a thin async wrapper over
  the libpod HTTP API spoken over the rootless Unix socket with `httpx`. It is
  the only module that knows Podman wire formats. Callers depend on the
  `PodmanAPI` protocol and the small Danube spec/result dataclasses
  (`PodSpec`, `ContainerSpec`, `ExecSpec`, `ResourceSummary`, ...), so the runner
  and its unit tests never touch Podman concepts directly.
- `danube/runner/local.py` — `LocalContainerRunner`, the `Runner` implementation.

### Default-deny egress

The job pod is attached to a dedicated **internal** Podman network
(`danube-egress`, created on demand via `ensure_network(internal=True)`). An
internal network has no route to the internet, so direct egress from the Worker
is denied by default. Allowlisted egress through a proxy is a later issue; the
integration test asserts a direct outbound connection from the Worker fails.

### Tracked state (`runner_state`)

On `start_job` the runner records one `runner_state` row per created resource
when a database is configured:

- `kind` is `pod`, `worker`, or `coordinator` (mirrors the
  `io.danube.resource` label).
- `external_id` is the Podman id of the pod/container.
- `status` is `running`; a failed `cleanup_job` instead writes a row with status
  `cleanup_failed` so reconciliation can surface it.

`cleanup_job` deletes the job's `runner_state` rows after removing the pod.

### Reconciliation and the CLI

`reconcile()` compares `runner_state`/`jobs` against the live resources labelled
`io.danube.managed=true` and reports: pods for finished or unrecognised jobs
(`stale_pods`),
containers with no tracked state (`orphaned_containers`), workspaces for inactive
jobs (`stale_workspaces`), active jobs with no pod (`missing_pods`), and
`cleanup_failed` rows (`failed_cleanups`). The operator command
`danube runner reconcile` prints this report.
