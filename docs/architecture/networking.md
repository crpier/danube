# Networking Architecture

## Hub-and-Spoke Pattern

All communication flows through the Master process:

```
┌─────────────┐
│ Coordinator │
│  Container  │
└──────┬──────┘
        │ HTTP/2 + JSON
        │ (plaintext, localhost in pod network namespace)
        ▼

┌─────────────┐
│   Master    │◀─────────────────┐
│   Process   │                  │
└──────┬──────┘                  │
       │ K8s Exec API            │
       │ (HTTPS + auth)          │
       ▼                         │
┌─────────────┐                  │
│   Worker    │                  │
│  Container  │                  │
└─────────────┘                  │
                                 │
       Pod deleted after job ────┘
```

### Why Hub-and-Spoke?

- **No service discovery**: Coordinator doesn't need to find Worker
- **Centralized auth**: Only Master has K8s credentials
- **Log aggregation**: Master is single point for all stdout/stderr
- **Simplified security**: Workers can't be accessed externally

## Communication Channels

### Coordinator → Master (HTTP/2 JSON)

**Protocol**: HTTP/2 with JSON payloads (plaintext)

**Endpoint**: `master.danube-system:9000`

**Notes**: Coordinator requests are synchronous; log streaming remains on the K8s exec WebSocket and is backpressured by Master disk writes.

**Endpoints**:
```http
POST /rpc/run-step
POST /rpc/get-secret
POST /rpc/upload-artifact  # streamed
POST /rpc/report-status
```

**Example request**:
```json
{
  "job_id": "abc123",
  "step_name": "Install Dependencies",
  "command": "npm ci",
  "image": "node:18-alpine",
  "env": {"CI": "true"},
  "timeout_seconds": 3600
}
```

**Example response**:
```json
{
  "exit_code": 0,
  "duration_ms": 1234
}
```

**Security**: No TLS (same cluster network, not exposed externally). Master validates job_id in each request.

### Master → Worker (K8s Exec API)

**Protocol**: WebSocket upgrade over HTTPS

**Endpoint**: K8s API server `/api/v1/namespaces/danube-jobs/pods/{pod_name}/exec`

**Command execution**:
```python
from kubernetes import client, stream

resp = stream.stream(
    api.connect_get_namespaced_pod_exec,
    name=pod_name,
    namespace="danube-jobs",
    container="worker",
    command=["/bin/sh", "-c", user_command],
    stderr=True,
    stdin=False,
    stdout=True,
    tty=False,
    _preload_content=False
)

# Stream stdout/stderr back to Master
while resp.is_open():
    resp.update(timeout=1)
    if resp.peek_stdout():
        print(resp.read_stdout())
    if resp.peek_stderr():
        print(resp.read_stderr(), file=sys.stderr)
```

**Security**: K8s client library handles mTLS, token auth.

### UI → Master (HTTP + SSE)

**REST API**: HTTP/JSON over port 8080

**Log streaming**: Server-Sent Events (SSE)

```javascript
// Frontend subscribes to log stream
const eventSource = new EventSource(`/api/v1/jobs/${jobId}/logs/stream`);
eventSource.onmessage = (event) => {
  appendLog(event.data);
};
```

Master reads log file and streams new lines as they're written.

**Security**: JWT authentication (from Dex OIDC).

## Egress Filtering (SLSA L3)

Cilium NetworkPolicy restricts pipeline Pod egress to allowlist:

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: egress-allowlist
  namespace: danube-jobs
spec:
  endpointSelector: {}  # Applies to all pods in namespace
  egress:
    # DNS-based allowlist
    - toFQDNs:
        - matchName: "registry.npmjs.org"
        - matchPattern: "*.pypi.org"
        - matchName: "github.com"
        - matchName: "registry.danube-system"
    
    # Allow DNS resolution
    - toEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: kube-system
            k8s-app: kube-dns
      toPorts:
        - ports:
            - port: "53"
              protocol: UDP
    
    # Allow K8s API server (for Master → Worker exec)
    - toEntities:
        - kube-apiserver
```

### Allowlist Configuration

Defined in Blueprint repo `config.json`:

```json
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {"name": "global"},
  "spec": {
    "egress_allowlist": [
      "registry.npmjs.org",
      "pypi.org",
      "*.cdn.example.com",
      "registry.danube-system"
    ]
  }
}
```

Master applies this as Cilium NetworkPolicy when configuration syncs.

### Testing Egress Filtering

```python
# In danubefile.py - this should work
step.run("curl https://registry.npmjs.org")

# This should be blocked (403 or timeout)
step.run("curl https://evil.com")
```

## Service Discovery

### Internal Services

| Service | Namespace | Endpoint | Purpose |
|---------|-----------|----------|---------|
| Master RPC | `danube-system` | `master.danube-system:9000` | Coordinator communication |
| Dex | `danube-system` | `dex.danube-system:5556` | OIDC login |
| Registry | `danube-system` | `registry.danube-system:5000` | Container images |

All services use ClusterIP (internal only).

### DNS Resolution

K8s CoreDNS provides service discovery:

```
master.danube-system.svc.cluster.local → 10.43.x.x
```

Short form works within same namespace:
```
master → 10.43.x.x (from danube-system)
master.danube-system → 10.43.x.x (from any namespace)
```

## Port Allocation

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| Master HTTP | 8080 | HTTP | REST API, webhooks, UI |
| Master RPC | 9000 | HTTP/2 | Coordinator communication |
| Dex | 5556 | HTTP | OIDC login |
| Registry | 5000 | HTTP | Docker Registry API |

## Network Policies

### danube-system Namespace

**Ingress**:
- Allow all from `danube-jobs` namespace (Coordinator → Master)
- Allow from ingress controller (external traffic)

**Egress**:
- Allow all (Master needs to reach K8s API, Git, external webhooks)

### danube-jobs Namespace

**Ingress**:
- Deny all (no incoming connections to pipeline Pods)

**Egress**:
- Allowlist-based (see Egress Filtering above)

## Multi-Cluster Deployment Pattern

**Note**: A single Danube Master manages ONE K8s cluster. For multiple clusters, deploy multiple Danube instances.

### Scenario: Dev, Staging, Production

```
┌─────────────────────┐
│ Dev Cluster         │
│ ┌─────────────────┐ │
│ │ Danube Master   │ │
│ │ (dev config)    │ │
│ └─────────────────┘ │
└─────────────────────┘

┌─────────────────────┐
│ Staging Cluster     │
│ ┌─────────────────┐ │
│ │ Danube Master   │ │
│ │ (staging config)│ │
│ └─────────────────┘ │
└─────────────────────┘

┌─────────────────────┐
│ Production Cluster  │
│ ┌─────────────────┐ │
│ │ Danube Master   │ │
│ │ (prod config)   │ │
│ └─────────────────┘ │
└─────────────────────┘
```

**Configuration Sharing**:
- Use same Blueprint repository
- Different branches or directories per environment
- Or separate repos with shared templates

```toml
# dev-config.toml
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
branch = "dev"

# staging-config.toml
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
branch = "staging"

# prod-config.toml
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
branch = "main"
```

## Load Balancing

For external access, use Ingress controller (nginx, Traefik):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: danube
  namespace: danube-system
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts:
        - danube.example.com
      secretName: danube-tls
  rules:
    - host: danube.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: master
                port:
                  number: 8080
```

## Network Performance Considerations

### Log Streaming

Master streams logs to:
- Disk (append-only file writes)
- SSE clients (UI browsers)

**Bottleneck**: Disk I/O for high-volume logs.

**Mitigation**:
- Use SSD storage
- Compress logs async (future feature)
- Rate limit SSE clients if needed

### HTTP/2 Control Plane Throughput

Coordinator → Master HTTP/2 calls are frequent (every step execution).

**Typical load**: 10-50 RPC/sec per job.

**Scaling**: Python asyncio + uvloop handles 10k+ RPC/sec easily.

### K8s API Rate Limiting

K8s API server has rate limits. Master makes frequent calls:
- Pod create/delete
- Exec API for command execution
- Log streaming

**Mitigation**:
- Use watch API for pod status (not polling)
- Connection pooling (kubernetes client handles this)
- Increase QPS limits if needed:

```python
from kubernetes import client

config = client.Configuration()
config.qps = 100  # Default 5
config.burst = 200  # Default 10
```
