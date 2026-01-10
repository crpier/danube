# Epic 2: Kubernetes Integration

**Description**: Set up Kubernetes client, Pod management, exec API streaming, and internal registry deployment.

**Total Estimate**: 30 hours

---

## DANUBE-006: K8s Client Wrapper

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 6h  
**Dependencies:** DANUBE-001  
**Assignee:** Unassigned

### Description

Create wrapper around official `kubernetes` Python client for pod operations, with connection pooling and error handling.

### Acceptance Criteria

- [ ] K8s client initialization (in-cluster and kubeconfig)
- [ ] Connection pooling configured
- [ ] Pod CRUD operations (create, get, delete, list)
- [ ] Namespace management
- [ ] Watch API for pod status changes
- [ ] Retry logic for transient failures
- [ ] Tests with mocked K8s API
- [ ] Integration tests with real K3s cluster

### Technical Notes

```python
from kubernetes import client, config
from kubernetes.client.rest import ApiException

class K8sClient:
    def __init__(self):
        try:
            config.load_incluster_config()
        except:
            config.load_kube_config()
        
        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()
    
    async def create_pod(self, pod_spec: dict, namespace: str) -> client.V1Pod:
        ...
```

---

## DANUBE-007: Pod Builder & Lifecycle Management

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 10h  
**Dependencies:** DANUBE-006  
**Assignee:** Unassigned

### Description

Build pod manifests with Coordinator + Worker containers, manage full pod lifecycle from creation to deletion.

### Acceptance Criteria

- [ ] Pod manifest builder for job pods
- [ ] Coordinator container configuration
- [ ] Worker container configuration (user-defined image)
- [ ] Shared workspace volume (emptyDir)
- [ ] Resource limits configured
- [ ] Pod creation with wait-for-ready
- [ ] Pod status monitoring
- [ ] Pod deletion with cleanup
- [ ] Tests for pod manifest generation
- [ ] Integration tests for full lifecycle

### Technical Notes

```python
def build_job_pod(job: Job, pipeline: Pipeline) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"job-{job.id}",
            "namespace": "danube-jobs",
            "labels": {
                "app": "danube",
                "job-id": job.id,
                "pipeline": pipeline.id
            }
        },
        "spec": {
            "containers": [
                {
                    "name": "coordinator",
                    "image": "danube-coordinator:latest",
                    "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}]
                },
                {
                    "name": "worker",
                    "image": pipeline.worker_image,
                    "command": ["/bin/sh", "-c", "sleep infinity"],
                    "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                    "resources": pipeline.resources
                }
            ],
            "volumes": [{"name": "workspace", "emptyDir": {}}],
            "restartPolicy": "Never"
        }
    }
```

---

## DANUBE-008: K8s Exec API Streaming

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 8h  
**Dependencies:** DANUBE-007  
**Assignee:** Unassigned

### Description

Implement command execution in Worker container via K8s exec API with stdout/stderr streaming.

### Acceptance Criteria

- [ ] Exec API connection via WebSocket
- [ ] Command execution in Worker container
- [ ] Stdout streaming (real-time)
- [ ] Stderr streaming (real-time)
- [ ] Exit code capture
- [ ] Timeout handling
- [ ] Connection failure retry
- [ ] Tests with mocked exec API
- [ ] Integration tests with real pods

### Technical Notes

```python
from kubernetes import stream

async def exec_command(
    pod_name: str,
    command: str,
    namespace: str = "danube-jobs"
) -> tuple[str, str, int]:
    resp = stream.stream(
        self.core_api.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=["/bin/sh", "-c", command],
        container="worker",
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False
    )
    
    stdout, stderr = [], []
    while resp.is_open():
        resp.update(timeout=1)
        if resp.peek_stdout():
            stdout.append(resp.read_stdout())
        if resp.peek_stderr():
            stderr.append(resp.read_stderr())
    
    return "".join(stdout), "".join(stderr), resp.returncode
```

---

## DANUBE-009: Internal Registry Deployment

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 6h  
**Dependencies:** DANUBE-006  
**Assignee:** Unassigned

### Description

Deploy Docker Registry v2 as K8s deployment in danube-system namespace with persistent storage.

### Acceptance Criteria

- [ ] Registry deployment manifest
- [ ] Service for registry (ClusterIP)
- [ ] Persistent volume for registry data
- [ ] Registry configured for internal use
- [ ] Registry accessible at `registry.danube-system:5000`
- [ ] Health check for registry
- [ ] Tests for registry availability
- [ ] Integration test: push/pull image

### Technical Notes

```python
def deploy_registry():
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "registry", "namespace": "danube-system"},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "registry"}},
            "template": {
                "metadata": {"labels": {"app": "registry"}},
                "spec": {
                    "containers": [{
                        "name": "registry",
                        "image": "registry:2",
                        "ports": [{"containerPort": 5000}],
                        "volumeMounts": [{
                            "name": "registry-storage",
                            "mountPath": "/var/lib/registry"
                        }]
                    }],
                    "volumes": [{
                        "name": "registry-storage",
                        "hostPath": {"path": "/var/lib/danube/registry"}
                    }]
                }
            }
        }
    }
```

---

## Updates

- 2026-01-10: Epic created
