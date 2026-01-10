# Testing Guide

## Testing Framework

Danube uses [snektest](https://pypi.org/project/snektest/) as the primary testing framework.

## Test Structure

```
tests/
├── unit/                   # Unit tests (no external dependencies)
│   ├── test_orchestrator.py
│   ├── test_k8s_client.py
│   ├── test_secrets.py
│   └── ...
├── integration/            # Integration tests (with K8s, DB)
│   ├── test_job_execution.py
│   ├── test_cac_sync.py
│   └── ...
├── e2e/                    # End-to-end tests (full system)
│   ├── test_pipeline_flow.py
│   └── ...
├── fixtures/               # Shared test fixtures
│   ├── k8s_fixtures.py
│   ├── db_fixtures.py
│   └── ...
└── conftest.py             # snektest configuration
```

## Running Tests

### All Tests

```bash
# Run all tests
uv run snektest

# With verbose output
uv run snektest -v

# With coverage
uv run snektest --cov=danube --cov-report=html
open htmlcov/index.html
```

### Specific Test Categories

```bash
# Unit tests only
uv run snektest tests/unit/

# Integration tests only
uv run snektest tests/integration/

# E2E tests only
uv run snektest tests/e2e/
```

### Specific Test Files

```bash
# Run specific file
uv run snektest tests/unit/test_orchestrator.py

# Run specific test
uv run snektest tests/unit/test_orchestrator.py::test_job_creation

# Run tests matching pattern
uv run snektest -k "test_job"
```

## Writing Unit Tests

### Basic Test Structure

```python
from danube.orchestrator import JobOrchestrator
from danube.db.models import Job, Pipeline

async def test_job_creation():
    """Test that jobs are created with correct initial state."""
    orchestrator = JobOrchestrator()
    
    pipeline = Pipeline(id="test-pipeline", name="Test")
    job = await orchestrator.create_job(
        pipeline=pipeline,
        trigger_type="manual"
    )
    
    assert job.status == "pending"
    assert job.pipeline_id == "test-pipeline"
    assert job.trigger_type == "manual"

async def test_job_timeout():
    """Test that jobs timeout after max_duration."""
    orchestrator = JobOrchestrator()
    
    job = Job(id="test-job", max_duration_seconds=1)
    await orchestrator.start_job(job)
    
    # Wait for timeout
    await asyncio.sleep(2)
    
    updated_job = await orchestrator.get_job(job.id)
    assert updated_job.status == "timeout"
```

### Mocking External Dependencies

```python
from unittest.mock import AsyncMock, MagicMock
from danube.k8s.client import K8sClient

async def test_pod_creation():
    """Test pod creation without real K8s cluster."""
    # Mock K8s client
    k8s_client = AsyncMock(spec=K8sClient)
    k8s_client.create_pod.return_value = {"name": "test-pod"}
    
    orchestrator = JobOrchestrator(k8s_client=k8s_client)
    pod = await orchestrator.create_pod(job_id="test-job")
    
    # Verify mock was called correctly
    k8s_client.create_pod.assert_called_once()
    assert pod["name"] == "test-pod"
```

### Testing Database Operations

```python
from danube.db.repos import JobRepository
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

async def test_job_repository():
    """Test job repository with in-memory SQLite."""
    # Create in-memory database
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Use repository
    async with AsyncSession(engine) as session:
        repo = JobRepository(session)
        
        job = await repo.create(
            pipeline_id="test-pipeline",
            trigger_type="manual"
        )
        
        assert job.id is not None
        
        # Retrieve job
        retrieved = await repo.get(job.id)
        assert retrieved.pipeline_id == "test-pipeline"
```

### Property-Based Testing

```python
from hypothesis import given, strategies as st

@given(
    job_id=st.text(min_size=1, max_size=50),
    status=st.sampled_from(["pending", "running", "success", "failure"])
)
async def test_job_status_updates(job_id: str, status: str):
    """Test job status updates with generated inputs."""
    repo = JobRepository(session)
    
    job = await repo.create(pipeline_id="test", trigger_type="manual")
    updated = await repo.update_status(job.id, status)
    
    assert updated.status == status
```

## Writing Integration Tests

### K8s Integration Tests

```python
from kubernetes import config, client
from danube.k8s.client import K8sClient

async def test_pod_lifecycle():
    """Test full pod lifecycle with real K8s cluster."""
    # Load kubeconfig
    config.load_kube_config()
    
    k8s_client = K8sClient()
    
    # Create pod
    pod = await k8s_client.create_pod(
        name="test-pod",
        namespace="danube-jobs",
        image="busybox",
        command=["/bin/sh", "-c", "echo hello"]
    )
    
    assert pod.metadata.name == "test-pod"
    
    # Wait for pod to complete
    await k8s_client.wait_for_pod_complete(pod.metadata.name, timeout=30)
    
    # Get logs
    logs = await k8s_client.get_pod_logs(pod.metadata.name)
    assert "hello" in logs
    
    # Delete pod
    await k8s_client.delete_pod(pod.metadata.name)
```

### CaC Sync Integration Tests

```python
import tempfile
import git
from danube.cac.syncer import CaCSyncer

async def test_cac_sync():
    """Test CaC repository sync."""
    # Create temporary Git repo
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = git.Repo.init(tmpdir)
        
        # Create config file
        config_file = Path(tmpdir) / "config.yaml"
        config_file.write_text("""
apiVersion: danube.dev/v1
kind: Config
metadata:
  name: global
spec:
  retention:
    logs_days: 30
""")
        
        repo.index.add(["config.yaml"])
        repo.index.commit("Initial config")
        
        # Sync
        syncer = CaCSyncer(repo_url=f"file://{tmpdir}")
        await syncer.sync()
        
        # Verify config loaded
        config = await syncer.get_config()
        assert config.spec.retention.logs_days == 30
```

## Writing E2E Tests

### Full Pipeline Execution

```python
async def test_full_pipeline_execution():
    """Test complete pipeline from trigger to completion."""
    # Create test pipeline in CaC repo
    pipeline_config = """
apiVersion: danube.dev/v1
kind: Pipeline
metadata:
  name: test-pipeline
spec:
  repository: https://github.com/test/repo
  script: danubefile.py
"""
    
    # Trigger webhook
    response = await http_client.post(
        "/webhooks/github",
        json={
            "repository": {"full_name": "test/repo"},
            "ref": "refs/heads/main",
            "after": "abc123"
        }
    )
    
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    
    # Wait for job to complete
    for _ in range(60):
        job = await get_job(job_id)
        if job.status in ["success", "failure"]:
            break
        await asyncio.sleep(1)
    
    assert job.status == "success"
    
    # Verify logs
    logs = await get_job_logs(job_id)
    assert "Pipeline completed" in logs
```

## Test Fixtures

### Database Fixtures

```python
# tests/fixtures/db_fixtures.py
from snektest import fixture
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

@fixture
async def db_engine():
    """Provide in-memory SQLite engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    await engine.dispose()

@fixture
async def db_session(db_engine):
    """Provide database session."""
    async with AsyncSession(db_engine) as session:
        yield session
        await session.rollback()
```

### K8s Fixtures

```python
# tests/fixtures/k8s_fixtures.py
from snektest import fixture
from kubernetes import config, client

@fixture(scope="session")
def k8s_config():
    """Load kubeconfig once per session."""
    config.load_kube_config()

@fixture
async def k8s_client(k8s_config):
    """Provide K8s API client."""
    return client.CoreV1Api()

@fixture
async def test_namespace(k8s_client):
    """Create temporary namespace for testing."""
    namespace = f"danube-test-{uuid.uuid4().hex[:8]}"
    
    k8s_client.create_namespace(
        client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
    )
    
    yield namespace
    
    k8s_client.delete_namespace(namespace)
```

## Test Configuration

### conftest.py

```python
# tests/conftest.py
from snektest import fixture
import asyncio

# Configure asyncio event loop
@fixture(scope="session")
def event_loop():
    """Provide event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

# Mock environment
@fixture(autouse=True)
def mock_env(monkeypatch):
    """Mock environment variables for all tests."""
    monkeypatch.setenv("DANUBE_LOG_LEVEL", "debug")
    monkeypatch.setenv("DANUBE_DATA_DIR", "/tmp/danube-test")
```

## Coverage Requirements

Aim for:
- **Unit tests**: 90%+ coverage
- **Integration tests**: Cover critical paths
- **E2E tests**: Cover main user workflows

### Generate Coverage Report

```bash
# Run with coverage
uv run snektest --cov=danube --cov-report=html --cov-report=term

# View HTML report
open htmlcov/index.html

# Check coverage threshold
uv run snektest --cov=danube --cov-fail-under=90
```

## Performance Testing

### Load Testing

```python
import asyncio
from danube.api.http import app

async def test_api_load():
    """Test API can handle 100 concurrent requests."""
    async def make_request():
        response = await http_client.get("/api/v1/pipelines")
        assert response.status_code == 200
    
    # Run 100 concurrent requests
    tasks = [make_request() for _ in range(100)]
    await asyncio.gather(*tasks)
```

### Benchmark Tests

```python
import time

async def test_job_creation_performance():
    """Benchmark job creation speed."""
    start = time.time()
    
    for _ in range(100):
        await orchestrator.create_job(pipeline_id="test", trigger_type="manual")
    
    duration = time.time() - start
    
    # Should create 100 jobs in under 1 second
    assert duration < 1.0
```

## Continuous Integration

### GitHub Actions

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Install UV
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      
      - name: Install dependencies
        run: uv sync
      
      - name: Run tests
        run: uv run snektest --cov=danube --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
```

## Test Best Practices

1. **Isolate tests**: Each test should be independent
2. **Use descriptive names**: `test_job_timeout_after_max_duration` not `test_timeout`
3. **Arrange-Act-Assert**: Structure tests clearly
4. **Mock external services**: Don't rely on real APIs in unit tests
5. **Clean up resources**: Delete test data, close connections
6. **Test edge cases**: Empty inputs, very large inputs, concurrent access
7. **Keep tests fast**: Unit tests should run in milliseconds
8. **Use fixtures**: Avoid code duplication
9. **Test error cases**: Not just happy paths
10. **Update tests with code**: When changing code, update relevant tests

## Debugging Failing Tests

```bash
# Run with verbose output
uv run snektest -vv tests/unit/test_orchestrator.py::test_job_creation

# Drop into debugger on failure
uv run snektest --pdb

# Show print statements
uv run snektest -s

# Run last failed tests only
uv run snektest --lf
```

## Test Documentation

Document complex test setups:

```python
async def test_concurrent_job_execution():
    """
    Test that multiple jobs can run concurrently without interfering.
    
    Setup:
    - Create 3 pipelines with different configurations
    - Trigger all 3 simultaneously
    
    Expected behavior:
    - All 3 jobs run in parallel
    - Each job completes successfully
    - No database contention errors
    - Logs don't intermix
    """
    # Test implementation...
```
