# Danube

Self-hosted, single-host CI/CD appliance. A Master process orchestrates ephemeral
rootless Podman job pods; each job pairs a Coordinator container (runs the user's
pipeline code over RPC) with a Worker container (runs build commands).

## Language

### Job execution

**Pipeline**:
A user-authored build/deploy program (`danubefile.py`) that the Coordinator runs.
Expressed as async calls against the SDK (`step.run`, `secrets.get`, …).

**Coordinator**:
The container that imports and runs the Pipeline, driving the Worker over RPC. Holds
no build tools itself.

**Worker**:
The user-imaged container where build commands actually run. Each `step.run` is a
fresh, stateless shell in `/workspace`.
_Avoid_: builder, executor

**Step**:
One recorded unit of pipeline work with its own status and log stream. Carries a
**Step Kind**: `run` (a command in a fresh Worker shell), `build` (an Image Build),
or `push` (an Image Push).

**Job Context**:
The run metadata a Pipeline can read about its own job — job id, pipeline name,
branch, commit sha, trigger type. Exposed to the Coordinator via the SDK so pipelines
can compose things like image tags. Distinct from secrets and from per-step env.

### Images

**Image Build**:
A host-side action that produces a tagged container image from a build context in the
job workspace. Executes on the host's rootless Podman (the same daemon that runs job
pods), driven by an SDK verb over RPC — never inside the Worker.
_Avoid_: docker build, nested build

**Local Image Store**:
The host Podman image store that job pods pull from and that Image Builds tag into.
Doubles as the image cache that the Reaper's ImagePruner prunes.
_Avoid_: registry (a Registry is external and pushed-to, not this)

**Image Push**:
A separate, opt-in action that uploads a tagged image from the Local Image Store to an
external Registry. Distinct from Image Build; a build never pushes implicitly.

**Registry**:
An external image registry that Danube can Push to. Not the Local Image Store.

### Isolation

**Isolation Profile**:
The set of kernel-isolation settings applied to a job's containers: capability drop,
read-only rootfs (with writable scratch), no-new-privileges, private PID/IPC, network
posture, and resource limits. Applied by the runner; verifiable on real Podman.

**Egress Policy**:
A pipeline's outbound-network posture. Default **deny** (job pod on an `internal`
network); a pipeline may opt into full egress in its Blueprint. Job-level, never
per-step (steps share the pod network namespace). Domain allowlists are a later concept.

**Resource Ceiling**:
The operator-set hard maximum (cpu/memory/pids/timeout) in server config. A pipeline's
Blueprint may request limits but the runner clamps them to the ceiling — a pipeline
cannot grant itself more than the operator allows.
