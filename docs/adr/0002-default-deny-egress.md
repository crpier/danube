# Job pods deny outbound network by default

Status: accepted

## Decision

Job pods attach to an `internal` (no-egress) Podman network by default. A pipeline opts
into outbound network access with `egress: true` in its Blueprint, which attaches the
pod to a normal outbound network instead. Egress is a job-level posture, never per-step.

## Why

On a single-host appliance running repo-authored pipelines, denying egress by default
contains the blast radius of a compromised or malicious build (data exfiltration, SSRF,
pulling attacker payloads). Making it an explicit, version-controlled Blueprint field
keeps the grant auditable under GitOps — unlike a trigger-time parameter, which would
let anyone who can trigger a run silently grant themselves network access.

Egress is necessarily job-level because Podman `exec` shares the container's network
namespace and there is one network per job pod; per-step egress is not expressible
without a separate netns per step.

## Consequences

- Many real pipelines (dependency fetches, deploys) require `egress: true`; the secure
  default is the inconvenient one, by design.
- Domain/host **allowlists** are out of scope: plain Podman has no built-in egress
  filtering, so a true allowlist needs an egress proxy — tracked as a separate epic.
  v1 is binary deny/allow.
