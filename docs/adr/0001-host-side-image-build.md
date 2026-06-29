# Image builds run on the host Podman, not inside the Worker

Status: accepted

## Decision

Image builds execute on the host's rootless Podman — the same daemon that runs job
pods — driven by an SDK verb (`danube.images.build`) over the Coordinator→Master RPC.
Builds never run inside the Worker container. Built images land in the host's Local
Image Store (the same store pods pull from); pushing to an external Registry is a
separate opt-in verb. Build `RUN` steps default to `--network=none`.

## Why

The alternative — building inside the Worker (nested Podman/buildah) — requires either
privileged containers or relaxed user-namespace isolation, which would break the
Isolation Profile that the runner otherwise enforces (cap-drop ALL, no-new-privs,
read-only rootfs). Host-side build keeps that security model intact and reuses the
rootless Podman already present on the appliance.

The trade-off: a build's `RUN` steps execute repo-authored code on the host Podman as
the danube service account, with less isolation than a Worker step (which runs in a
cap-dropped, per-job-network pod). We accept this because the build context comes from
the same repository Danube already trusts to run arbitrary Worker commands, and rootless
`podman build` still user-namespaces each `RUN`. `--network=none` by default denies the
most dangerous capability (egress/SSRF/exfiltration from build steps) unless a pipeline
explicitly opts in.

## Consequences

- `build_args` are visible in image history and are therefore **not** a secret channel;
  build-time secrets (BuildKit `--secret` mounts) are a deliberate v1 non-goal.
- The Local Image Store is shared across concurrent jobs: identical tags collide
  (last-writer-wins). Tags stay raw/user-controlled; pipelines disambiguate using the
  Job Context (e.g. commit sha) rather than Danube auto-namespacing.
- Pushing is the separate `danube.images.push(tag, registry, credentials=...)` verb. It
  pushes a Local Image Store tag to `<registry>/<tag>` and records a `kind=push` step.
  Registry credentials travel in the RPC body and on to host Podman as an
  `X-Registry-Auth` header — never in the recorded step command or a logged URL — and
  the password (a secret value) is scrubbed from the streamed push log like any other
  secret. TLS verification is on by default; a pipeline opts out only for an insecure
  (HTTP/self-signed) registry. A push the registry rejects (bad/absent auth) is a step
  failure, not an implicit retry.
