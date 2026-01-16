# Observability

## Logging

### Master Logs

**Format**: Structured JSON (via Python logging with JSON formatter)

**Destination**: stdout (captured by container log collector)

**Log Levels**:
- `ERROR`: Unrecoverable errors (database connection lost, K8s API unreachable)
- `WARNING`: Recoverable issues (failed secret access, webhook validation failed)
- `INFO`: Notable events (job started, pipeline triggered, Blueprint sync completed)
- `DEBUG`: Detailed diagnostics (HTTP/2 RPC calls, SQL queries)
- `TRACE`: Very verbose (not used by default)

**Configuration**: Set via environment variable:
```bash
export DANUBE_LOG_LEVEL=INFO  # Default
```

**Example log entry**:
```json
{
  "timestamp": "2026-01-10T12:34:56.789Z",
  "level": "info",
  "logger": "danube.orchestrator",
  "event": "job_started",
  "job_id": "abc123",
  "pipeline": "frontend-build",
  "trigger_type": "webhook",
  "trigger_ref": "main/abc123def",
  "user": "alice@example.com"
}
```

### Job Logs

**Format**: Plain text (stdout/stderr from Worker container)

**Storage**: `/var/lib/danube/logs/<job_id>.log`

**Write pattern**: Append-only during job execution (batched flushes)

**Streaming**: Master reads file and streams to SSE clients in real-time; backpressure blocks log ingestion when disk is saturated

**Retention**: Deleted by Reaper after `retention.logs_days`

### Sensitive Data Scrubbing

Master automatically redacts patterns before writing to log files:

- JWT tokens (`eyJ...`)
- Common secret patterns (`password=...`, `token=...`, `api_key=...`)
- Email addresses in command output (configurable)

**Implementation**: Regex-based scanner applied before disk write and SSE broadcast.

## Metrics

### OpenTelemetry Integration

Master exports metrics via:
- **Prometheus endpoint**: `GET /metrics` (Prometheus text format)
- **OTLP exporter**: Pushes to configured collector (optional)

**Configuration**:
```json
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {"name": "global"},
  "spec": {
    "observability": {
      "otel_endpoint": "http://otel-collector:4317",
      "metrics_enabled": true,
      "traces_enabled": true
    }
  }
}
```


### Key Metrics

**Job Metrics**:
```
danube_jobs_total{status="success|failure|timeout|cancelled", pipeline="..."}
danube_job_duration_seconds{pipeline="..."}  # Histogram
danube_active_jobs  # Gauge
danube_job_queue_size  # Gauge
danube_job_timeouts_total{pipeline="..."}
```

**Database Metrics**:
```
danube_db_queries_total{operation="select|insert|update|delete"}
danube_db_query_duration_seconds{operation="..."}  # Histogram
danube_db_connections_active  # Gauge
danube_db_lock_wait_seconds_total  # Counter (WAL mode contention)
```

**K8s Metrics**:
```
danube_k8s_api_calls_total{operation="create_pod|delete_pod|exec", status="success|error"}
danube_k8s_api_call_duration_seconds{operation="..."}  # Histogram
danube_k8s_pods_active{namespace="danube-jobs"}  # Gauge
```

**Secret Access**:
```
danube_secret_requests_total{pipeline="...", secret_key="..."}
danube_secret_cache_hits_total
danube_secret_cache_misses_total
```

**Log Streaming**:
```
danube_log_bytes_written_total{job_id="..."}
danube_sse_clients_active  # Gauge
danube_sse_messages_sent_total
```

**Blueprint Sync**:
```
danube_blueprint_sync_total{status="success|error"}
danube_blueprint_sync_last_success  # Unix timestamp
danube_blueprint_sync_duration_seconds  # Histogram
danube_blueprint_changes_applied_total{type="pipeline|user|team"}
```

**Registry**:
```
danube_registry_pulls_total{image="..."}
danube_registry_pushes_total{image="..."}
danube_registry_storage_bytes  # Gauge
```

**Dex/Auth**:
```
danube_auth_logins_total{status="success|failure"}
danube_auth_permission_denied_total{resource="pipeline|secret|artifact"}
danube_auth_active_sessions  # Gauge
```

### Prometheus Setup

Example Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: danube
    static_configs:
      - targets:
          - danube.example.com:8080
    metrics_path: /metrics
    scrape_interval: 15s
```

### Grafana Dashboards

Recommended dashboard panels:

1. **Job Overview**:
   - Jobs per hour (rate)
   - Success rate (%)
   - Average job duration
   - Active jobs

2. **Performance**:
   - HTTP/2 RPC request rate
   - K8s API latency
   - Database query duration (p50, p95, p99)
   - Log streaming throughput

3. **Errors**:
   - Failed jobs by pipeline
   - K8s API errors
   - Database lock waits
   - Blueprint sync failures

4. **Capacity**:
   - Active pods
   - Disk usage (logs, artifacts, registry)
   - Memory usage
   - CPU usage

## Tracing

### OpenTelemetry Traces

Master creates trace spans for:

**HTTP Requests**:
```
Span: POST /api/v1/jobs
├─ Span: Check auth
├─ Span: Load pipeline from DB
├─ Span: Create job record
└─ Span: Trigger orchestrator
```

**Job Execution**:
```
Span: Job abc123
├─ Span: Create Pod
├─ Span: Wait for Pod ready
├─ Span: Execute step 1
│  ├─ Span: HTTP/2 RunStep
│  └─ Span: K8s Exec API
├─ Span: Execute step 2
└─ Span: Delete Pod
```

**HTTP/2 RPC Calls**:
```
Span: POST /rpc/run-step
├─ Span: Validate job_id
├─ Span: Load secret (if needed)
├─ Span: K8s Exec
└─ Span: Stream logs
```

### Trace Context Propagation

- HTTP requests: `traceparent` header
- HTTP/2 RPC calls: OpenTelemetry metadata
- Job logs: Trace ID injected into structured logs

### Jaeger Integration

Example configuration:

```yaml
spec:
  observability:
    otel_endpoint: "http://jaeger-collector:4317"
    traces_enabled: true
```

Traces exported via OTLP to Jaeger collector.

## Health Checks

### Endpoints

**`GET /health`**

Returns 200 if API is responsive (liveness probe).

```json
{
  "status": "ok",
  "timestamp": "2026-01-10T12:34:56Z"
}
```

**`GET /health/ready`**

Returns 200 if all subsystems healthy (readiness probe).

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "k8s": "ok",
    "dex": "ok",
    "registry": "ok",
    "blueprint_sync": "ok"
  },
  "timestamp": "2026-01-10T12:34:56Z"
}
```

Returns 503 if any check fails:

```json
{
  "status": "not_ready",
  "checks": {
    "database": "ok",
    "k8s": "ok",
    "dex": "degraded",  # Dex pod not running
    "registry": "ok",
    "blueprint_sync": "stale"  # Last sync >5min ago
  },
  "timestamp": "2026-01-10T12:34:56Z"
}
```

### Kubernetes Probes

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 10
```

### Periodic Health Checks

Master runs background task every 5 minutes:

1. Check SQLite connection (`SELECT 1`)
2. Check K8s API (`GET /api/v1/namespaces/danube-system`)
3. Check Dex pod status
4. Check Registry pod status
5. Check Blueprint sync freshness (last sync <5min ago)

If checks fail, attempt self-healing:
- Restart failed Dex/Registry pods
- Force Blueprint sync
- Clear stale database locks
- Log health check results

Alert on repeated failures (integrate with monitoring system).

## Alerting Recommendations

### Critical Alerts

- Master process down
- Database unavailable
- K8s API unreachable
- Disk usage >90%
- Job failure rate >50% (over 1 hour)

### Warning Alerts

- Dex pod restart
- Registry pod restart
- Blueprint sync stale (>10min)
- Job timeout rate >10%
- High database lock contention
- SSE client connection failures

### Alert Destinations

Configure via external tools (Prometheus Alertmanager, Grafana Alerts):

- PagerDuty (critical)
- Slack (warning)
- Email (all)

## Log Aggregation

For production, send Master logs to centralized system:

**Fluentd**:
```yaml
# DaemonSet collects container logs
- match: danube-system.master.*
  type: elasticsearch
  host: elasticsearch.logging.svc
```

**Promtail + Loki**:
```yaml
scrape_configs:
  - job_name: danube
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - danube-system
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: danube-master
        action: keep
```

**CloudWatch** (AWS):
```bash
# Forward logs via CloudWatch agent
aws logs create-log-group --log-group-name /danube/master
```

## Performance Profiling

For debugging performance issues:

**Python profiling**:
```python
import cProfile
import pstats

# In master.py
if os.getenv("DANUBE_PROFILE"):
    cProfile.run("main()", "danube.prof")
    stats = pstats.Stats("danube.prof")
    stats.sort_stats("cumulative")
    stats.print_stats(20)
```

**Memory profiling**:
```bash
# Install memory_profiler
uv add --dev memory-profiler

# Run with profiling
python -m memory_profiler danube/master.py
```

**Async profiling**:
```python
# Use py-spy for async profiling
pip install py-spy
sudo py-spy record -o profile.svg --pid <master_pid>
```
