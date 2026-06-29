# Networking Architecture

## Overview

Danube runs as a single-host appliance. Pipeline jobs execute as rootless Podman pods attached to Danube-controlled networks. The default posture is deny-by-default: job containers should not have direct unrestricted internet access.

## Hub-and-Spoke Pattern

All control traffic flows through the Master:

```text
┌─────────────┐
│ Coordinator │
└──────┬──────┘
       │ HTTP/JSON RPC
       ▼
┌─────────────┐
│   Master    │
└──────┬──────┘
       │ Local runner exec
       ▼
┌─────────────┐
│   Worker    │
└─────────────┘
```

The Coordinator does not directly command the Worker.

## Communication Channels

### Coordinator → Master

**Protocol**: HTTP/JSON RPC.

**Endpoints**:

```http
POST /rpc/run-step
POST /rpc/get-secret
POST /rpc/upload-artifact
POST /rpc/report-status
```

**Security**:

- Master validates job identity on every request.
- RPC endpoint is only reachable from controlled local job networks.
- Job-scoped credentials or tokens authenticate Coordinator requests.

### Master → Worker

**Protocol**: local runner exec through the Podman API.

**Behavior**:

- Master asks the runner to execute `/bin/sh -c <command>` in the Worker.
- Runner streams stdout/stderr to Master.
- Master writes logs and returns the exit code to Coordinator.

### UI → Master

**Protocol**: HTTP/JSON plus SSE for logs.

```javascript
const eventSource = new EventSource(`/api/v1/jobs/${jobId}/logs/stream`);
eventSource.onmessage = (event) => appendLog(event.data);
```

External TLS is normally terminated by a reverse proxy, or by Danube if built-in TLS is configured.

## Job Network Model

Each job maps to one Podman pod with a controlled network context. The runner should support:

- no inbound access to job containers from external networks
- Coordinator can reach Master RPC
- Worker can reach only approved destinations
- direct internet is denied unless explicitly configured
- network state is deleted after job cleanup

```text
Worker container
  ├─ allowed: Danube Master / RPC
  ├─ allowed: local registry/cache
  ├─ allowed: Danube egress proxy
  └─ denied: direct internet
```

### Control path vs. egress denial

The Coordinator reaching the Master RPC and the Worker being denied direct internet
are two requirements on the **same** pod network namespace (both containers share
it). The current `LocalContainerRunner` default attaches the pod to a single
`internal` Podman network, which denies the internet but also blocks the host RPC,
so a real Coordinator cannot call back over it.

Until the egress proxy is productized, the end-to-end path uses a deliberate
carve-out: a non-`internal` control network the pod can use to reach the Master at
`host.containers.internal:<rpc_port>`. Splitting these cleanly — a reachable control
path while the Worker's general egress stays denied — is the job of the egress-proxy
work and is out of scope here.

### Per-pipeline egress opt-in

Egress is denied by default but a pipeline can opt the whole job into outbound access
with `egress: true` in its Blueprint (`docs/adr/0002-default-deny-egress.md`). The
runner reads this off the job's `StartJobRequest`: when set, it attaches the pod to a
normal outbound network (`danube-egress-allow`) instead of the default-deny `internal`
one (`danube-egress`). Egress is a job-level posture, never per-step, because the two
containers share one pod network namespace. The grant is binary deny/allow today; a
filtered allowlist needs the egress proxy above.

## Egress Control

Danube supports controlled egress for builds that need package registries, Git forges, or deployment targets.

### Recommended Model: Egress Proxy + Firewall

```text
Worker ──allowed──▶ Danube Egress Proxy ──policy──▶ approved external domains
```

The runner/firewall denies direct outbound traffic from job containers. The egress proxy enforces domain allowlists and logs outbound requests.

**Benefits**:

- Central policy enforcement
- Domain-based allowlists
- Auditable outbound access
- No dependency on cluster networking
- Works with the rootless Podman local runner

**Limitations**:

- HTTP(S) traffic is easiest to support
- SSH/Git and raw TCP protocols need explicit handling
- Tools may need proxy environment variables
- Firewall rules must prevent bypassing the proxy

### Allowlist Configuration

Defined in Blueprint config:

```json
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {"name": "global"},
  "spec": {
    "egress_allowlist": [
      "github.com",
      "*.githubusercontent.com",
      "registry.npmjs.org",
      "pypi.org",
      "registry.local"
    ]
  }
}
```

### Firewall Enforcement

The local runner is responsible for applying network restrictions using runtime-supported networking plus host firewall primitives where needed.

Minimum policy:

- deny direct internet from job containers
- allow DNS only through approved resolver path
- allow Master RPC
- allow local registry/cache
- allow egress proxy
- deny inbound connections to job containers

## Internal Services

| Service | Endpoint | Purpose |
|---------|----------|---------|
| Master HTTP | configured bind address, default `:8080` | UI, REST API, webhooks |
| Master RPC | local-only or job-network-only address, default `:9000` | Coordinator communication |
| Egress Proxy | local/job-network address | Controlled outbound HTTP(S) |
| Registry/Cache | local address | Built images and cache |

## Port Allocation

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| Master HTTP | 8080 | HTTP | REST API, webhooks, UI |
| Master RPC | 9000 | HTTP | Coordinator RPC |
| Egress Proxy | 9080 | HTTP proxy | Controlled egress |
| Registry | 5000 | HTTP | Local image registry/cache |

Ports are configurable in server configuration.

## DNS

The runner should provide job containers with a controlled DNS path. DNS should not become a way to bypass egress policy.

Recommended behavior:

- job containers use a Danube-managed resolver or runtime-controlled DNS
- DNS resolution for egress is logged where possible
- wildcard allowlists are resolved by the egress proxy rather than static IP rules

## Multi-Machine Direction

The initial product is single-host. If Danube later supports multiple machines, networking should evolve through runner agents:

```text
Master ──mTLS/control API──▶ Runner Agent ──local runtime──▶ job containers
```

Each runner agent remains responsible for local container networking and egress enforcement on its host.

## Network Performance Considerations

### Log Streaming

Log flow is:

```text
Worker stdout/stderr → Runner → Master → disk + SSE clients
```

Disk I/O is the likely bottleneck for noisy jobs.

### Egress Proxy

The egress proxy may become a bottleneck for dependency-heavy builds. Mitigations:

- local package mirrors
- local image/cache registry
- connection pooling
- per-job bandwidth limits
- metrics on request rate, latency, and denied requests

### Runtime Exec

Runner exec operations should be asynchronous and stream output incrementally. Long commands must not block the Master event loop.
