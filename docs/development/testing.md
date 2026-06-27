# Testing Guide

## Testing Framework

Danube uses `snektest` as the primary Python testing framework.

## Test Structure

```text
tests/
├── unit/
│   ├── test_orchestrator.py
│   ├── test_runner.py
│   ├── test_secrets.py
│   └── ...
├── integration/
│   ├── test_job_execution.py
│   ├── test_blueprint_sync.py
│   ├── test_local_runner.py
│   └── ...
├── e2e/
│   └── test_pipeline_flow.py
├── fixtures/
└── conftest.py
```

## Running Tests

```bash
uv run snektest
uv run snektest -v
uv run snektest tests/unit/
uv run snektest tests/integration/
uv run snektest tests/e2e/
```

Specific file/test:

```bash
uv run snektest tests/unit/test_runner.py
uv run snektest tests/unit/test_runner.py::test_exec_step_streams_logs
```

## Unit Tests

Unit tests should not require real Podman access, network access, or external services.

Example:

```python
from unittest.mock import AsyncMock

from danube.orchestrator import JobOrchestrator
from danube.runner import Runner

async def test_job_creation_starts_in_pending_state():
    runner = AsyncMock(spec=Runner)
    orchestrator = JobOrchestrator(runner=runner)

    job = await orchestrator.create_job(
        pipeline_id="frontend-build",
        trigger_type="manual",
    )

    assert job.status == "pending"
```

## Runner Tests

Runner unit tests should mock the Podman adapter:

```python
async def test_runner_starts_containers(podman_adapter):
    runner = LocalContainerRunner(runtime=podman_adapter)

    await runner.start_job(job_id="job-1", worker_image="busybox")

    podman_adapter.create_container.assert_called()
```

Integration tests may use real rootless Podman:

```python
async def test_local_runner_execs_command(local_runner):
    job = await local_runner.start_job(
        job_id="test-job",
        worker_image="busybox",
    )

    result = await local_runner.exec_step(job, "echo hello")

    assert result.exit_code == 0
    assert "hello" in result.stdout

    await local_runner.cleanup(job)
```

Runtime integration tests should skip when rootless Podman or the Podman API socket is unavailable.

## Database Tests

Use in-memory SQLite for unit tests:

```python
import aiosqlite

async def test_job_queries(schema_sql):
    async with aiosqlite.connect(":memory:") as db:
        await db.executescript(schema_sql)
        # run query tests
```

## Blueprint Sync Tests

Use temporary Git repositories:

```python
import tempfile
from pathlib import Path

async def test_blueprint_sync(syncer_factory):
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir)
        # create Git repo and JSON files
        syncer = syncer_factory(repo_url=f"file://{path}")
        await syncer.sync()
```

## E2E Tests

E2E tests cover full flow:

1. Blueprint config loaded
2. Pipeline triggered
3. Job environment created
4. Coordinator runs `danubefile.py`
5. Worker executes command
6. Logs stream
7. Artifacts upload
8. Job cleans up

Example expectation:

```python
async def test_full_pipeline_execution(api_client):
    response = await api_client.post(
        "/webhooks/github",
        json={"ref": "refs/heads/main", "after": "abc123"},
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = await wait_for_job(job_id)
    assert job.status == "success"
```

## Coverage Targets

- Unit tests: 90%+ for pure logic
- Integration tests: runner, DB, Blueprint, API critical paths
- E2E tests: main happy path and one failure path

```bash
uv run snektest --cov=danube --cov-report=html --cov-fail-under=90
```

## Performance Tests

Focus areas:

- job creation throughput
- concurrent log streaming
- runner exec latency
- SQLite contention
- egress proxy overhead
- cleanup performance after failures

## Test Best Practices

1. Keep unit tests independent of host runtime.
2. Put runtime-dependent tests under integration.
3. Clean up containers, workspaces, and temp files.
4. Test failure paths: timeout, cancelled job, runtime failure, denied egress.
5. Use descriptive test names.
6. Add tests alongside code changes.
7. Keep fixtures small and explicit.

## Debugging

```bash
uv run snektest -vv tests/unit/test_runner.py::test_exec_step_streams_logs
uv run snektest --pdb
uv run snektest -s
```

For runtime integration tests, inspect local Podman state:

```bash
podman pod ps
podman ps -a
podman logs <container>
```
