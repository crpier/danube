# Epic 3: Execution Engine

**Description**: Implement gRPC server, Python Coordinator SDK, and job orchestration engine.

**Total Estimate**: 32 hours

---

## DANUBE-010: gRPC Proto Definitions & Codegen

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 4h  
**Dependencies:** DANUBE-001  
**Assignee:** Unassigned

### Description

Define gRPC service protocol for Coordinator ↔ Master communication and set up code generation.

### Acceptance Criteria

- [ ] `proto/danube.proto` file created
- [ ] `JobService` defined (RunStep, GetSecret, UploadArtifact, ReportStatus)
- [ ] Request/response messages defined
- [ ] Code generation script in `Makefile` or `pyproject.toml`
- [ ] Generated Python code in `danube/api/grpc_pb2.py`
- [ ] Generated stubs in `danube/api/grpc_pb2_grpc.py`
- [ ] `.gitignore` updated to exclude generated files (or commit them)

### Technical Notes

```protobuf
syntax = "proto3";

package danube;

service JobService {
  rpc RunStep(RunStepRequest) returns (RunStepResponse);
  rpc GetSecret(GetSecretRequest) returns (GetSecretResponse);
  rpc UploadArtifact(stream UploadArtifactRequest) returns (UploadArtifactResponse);
  rpc ReportStatus(ReportStatusRequest) returns (ReportStatusResponse);
}

message RunStepRequest {
  string job_id = 1;
  string command = 2;
  map<string, string> env = 3;
  string image = 4;  // Optional worker image override
}

message RunStepResponse {
  int32 exit_code = 1;
  string stdout = 2;
  string stderr = 3;
}
```

Generate code:
```bash
python -m grpc_tools.protoc -I./proto \
  --python_out=./danube/api \
  --grpc_python_out=./danube/api \
  ./proto/danube.proto
```

---

## DANUBE-011: gRPC Server Implementation

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 8h  
**Dependencies:** DANUBE-010, DANUBE-008  
**Assignee:** Unassigned

### Description

Implement gRPC server with handlers for all JobService methods, integrated with K8s exec API and encryption service.

### Acceptance Criteria

- [ ] gRPC server listens on port 9000
- [ ] `RunStep` handler executes command via K8s exec
- [ ] `GetSecret` handler retrieves from SecretService
- [ ] `UploadArtifact` handler streams file to disk
- [ ] `ReportStatus` handler updates job status in database
- [ ] Job ID validation in all handlers
- [ ] Error handling and status codes
- [ ] Tests with grpcurl or grpcio client
- [ ] Integration tests with real gRPC calls

### Technical Notes

```python
from grpc import aio
from danube.api import grpc_pb2_grpc

class JobServiceServicer(grpc_pb2_grpc.JobServiceServicer):
    def __init__(self, k8s_client, secret_service, db):
        self.k8s = k8s_client
        self.secrets = secret_service
        self.db = db
    
    async def RunStep(self, request, context):
        # Validate job_id
        job = await self.db.get_job(request.job_id)
        if not job or job.status != "running":
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid job_id")
        
        # Execute command in worker container
        stdout, stderr, exit_code = await self.k8s.exec_command(
            pod_name=f"job-{request.job_id}",
            command=request.command,
            env=dict(request.env)
        )
        
        return grpc_pb2.RunStepResponse(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr
        )
```

---

## DANUBE-012: Python Coordinator SDK

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 8h  
**Dependencies:** DANUBE-011  
**Assignee:** Unassigned

### Description

Create Python SDK for pipeline scripts with `step.run()`, `secrets.get()`, `artifacts.upload()`, and `ctx` object.

### Acceptance Criteria

- [ ] `danube` package installable via pip
- [ ] `step.run()` function calls gRPC RunStep
- [ ] `secrets.get()` function calls gRPC GetSecret
- [ ] `artifacts.upload()` function calls gRPC UploadArtifact
- [ ] `ctx` object provides job metadata
- [ ] Error handling and retries
- [ ] Type stubs for IDE autocomplete
- [ ] Documentation and examples
- [ ] Tests for all SDK functions

### Technical Notes

```python
# danube/sdk/coordinator.py
import grpc
from danube.api import grpc_pb2, grpc_pb2_grpc

class StepRunner:
    def __init__(self, grpc_channel):
        self.stub = grpc_pb2_grpc.JobServiceStub(grpc_channel)
        self.job_id = os.getenv("DANUBE_JOB_ID")
    
    def run(
        self,
        command: str,
        name: str | None = None,
        image: str | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
        capture: bool = False
    ) -> int | str:
        response = self.stub.RunStep(grpc_pb2.RunStepRequest(
            job_id=self.job_id,
            command=command,
            env=env or {},
            image=image or ""
        ))
        
        if check and response.exit_code != 0:
            raise RuntimeError(f"Step failed with exit code {response.exit_code}")
        
        return response.stdout if capture else response.exit_code

step = StepRunner(grpc.insecure_channel("master.danube-system:9000"))
```

---

## DANUBE-013: Job Orchestrator & State Machine

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 12h  
**Dependencies:** DANUBE-007, DANUBE-011, DANUBE-004  
**Assignee:** Unassigned

### Description

Implement job orchestration engine with state machine (pending → scheduling → running → success/failure/timeout/cancelled).

### Acceptance Criteria

- [ ] Job state machine implemented
- [ ] Job creation from pipeline trigger
- [ ] Pod creation and scheduling
- [ ] Job monitoring during execution
- [ ] Timeout detection and enforcement
- [ ] Job completion handling
- [ ] Pod cleanup after job
- [ ] Log streaming integration
- [ ] Tests for all state transitions
- [ ] Integration test for full job lifecycle

### Technical Notes

```python
class JobOrchestrator:
    async def start_job(self, job: Job, pipeline: Pipeline):
        # Update status: pending → scheduling
        await self.db.update_job_status(job.id, "scheduling")
        
        # Create pod
        pod_spec = build_job_pod(job, pipeline)
        pod = await self.k8s.create_pod(pod_spec, "danube-jobs")
        
        # Wait for pod ready
        await self.k8s.wait_for_pod_ready(pod.metadata.name, timeout=300)
        
        # Update status: scheduling → running
        await self.db.update_job_status(job.id, "running")
        job.started_at = datetime.utcnow()
        
        # Monitor job
        asyncio.create_task(self._monitor_job(job, pod))
    
    async def _monitor_job(self, job: Job, pod):
        # Wait for coordinator to finish
        while True:
            pod_status = await self.k8s.get_pod_status(pod.metadata.name)
            
            if pod_status.phase in ["Succeeded", "Failed"]:
                break
            
            # Check timeout
            if job.started_at and (datetime.utcnow() - job.started_at).seconds > job.max_duration_seconds:
                await self._timeout_job(job, pod)
                return
            
            await asyncio.sleep(5)
        
        # Update final status
        final_status = "success" if pod_status.phase == "Succeeded" else "failure"
        await self.db.update_job_status(job.id, final_status)
        job.finished_at = datetime.utcnow()
        
        # Delete pod
        await self.k8s.delete_pod(pod.metadata.name, "danube-jobs")
```

---

## Updates

- 2026-01-10: Epic created
