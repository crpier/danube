# Observability

## Implementation notes

Metrics and tracing are implemented in-house (`danube.observability`) rather than
via `prometheus_client`/the OpenTelemetry SDK, to stay consistent with the
project's dependency-free, strictly-typed tooling (snekql, snektest):

- **Metrics**: a small registry (`Metrics`/`Registry`) renders the documented
  series in the Prometheus text exposition format (version 0.0.4) at `GET
  /metrics`. The registry is dependency-injected, so the orchestrator records into
  the same registry the endpoint renders. Series without instrumentation points
  yet (e.g. the DB metrics) are exposed at their zero value. `metrics_enabled`
  gates the endpoint: when false, `/metrics` is not mounted and scrapes get a 404.
  The push-based **OTLP exporter** (gated by `metrics_enabled` + `otel_endpoint`)
  is not implemented yet; `/metrics` scraping is the only export today.
- **Tracing**: `Tracer.span` models an OpenTelemetry span (name + attributes +
  duration), gated by `observability.traces_enabled`. It is a true no-op when
  disabled; when enabled it emits spans as DEBUG log records. `otel_endpoint` is
  recorded as the future OTLP-export hook — enabling tracing never touches the
  network, so no collector is required.
- **Readiness**: `GET /health/ready` currently probes `database`, and (when a
  runner is wired) `runner` and `container_runtime`. `blueprint_sync` and `disk`
  checks land with their components.

Configuration toggles come from `spec.observability` (or, until full
server-config parsing lands, the `DANUBE_METRICS_ENABLED`/`DANUBE_TRACES_ENABLED`/
`DANUBE_OTEL_ENDPOINT` environment variables). The log level is set by
`DANUBE_LOG_LEVEL`.

## Logging

### Master Logs

**Format**: Structured JSON.

**Destination**: stdout by default.

**Log Levels**:

- `ERROR`: unrecoverable errors, database unavailable, runner unavailable
- `WARNING`: recoverable issues, webhook validation failed, denied egress, cleanup retry
- `INFO`: job started/finished, pipeline triggered, Blueprint sync completed
- `DEBUG`: RPC calls, runner operations, SQL diagnostics

Configuration:

```bash
export DANUBE_LOG_LEVEL=INFO
```

Example:

```json
{
  "timestamp": "2026-01-10T12:34:56.789Z",
  "level": "info",
  "logger": "danube.orchestrator",
  "event": "job_started",
  "job_id": "abc123",
  "pipeline": "frontend-build",
  "trigger_type": "webhook",
  "trigger_ref": "main/abc123def"
}
```

### Job Logs

**Format**: Plain text stdout/stderr from Worker commands.

**Storage**:

```text
/var/lib/danube/logs/<job_id>.log
```

**Streaming**: Master writes logs to disk and broadcasts new log records to SSE clients.

**Retention**: Reaper deletes logs after `retention.logs_days`.

### Sensitive Data Scrubbing

Master redacts sensitive patterns before writing logs or broadcasting to clients:

- active job secrets
- JWT-like tokens
- common key/value secret patterns
- configurable custom regexes

## Metrics

Master exposes metrics through:

- `GET /metrics` in Prometheus text format (gated by `metrics_enabled`)
- optional OTLP exporter (planned; gated by `metrics_enabled` + `otel_endpoint`)

Configuration:

```json
{
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

```text
danube_jobs_total{status="success|failure|timeout|cancelled", pipeline="..."}
danube_job_duration_seconds{pipeline="..."}
danube_active_jobs
danube_job_queue_size
danube_job_timeouts_total{pipeline="..."}
```

**Runner Metrics**:

```text
danube_runner_operations_total{operation="start|exec|stop|cleanup", status="success|error"}
danube_runner_operation_duration_seconds{operation="..."}
danube_runner_containers_active
danube_runner_cleanup_failures_total
danube_runner_workspace_bytes
```

**Database Metrics**:

```text
danube_db_queries_total{operation="select|insert|update|delete"}
danube_db_query_duration_seconds{operation="..."}
danube_db_connections_active
danube_db_lock_wait_seconds_total
```

**Secret Access**:

```text
danube_secret_requests_total{pipeline="...", secret_key="..."}
danube_secret_cache_hits_total
danube_secret_cache_misses_total
```

**Log Streaming**:

```text
danube_log_bytes_written_total{job_id="..."}
danube_sse_clients_active
danube_sse_messages_sent_total
```

**Blueprint Sync**:

```text
danube_blueprint_sync_total{status="success|error"}
danube_blueprint_sync_last_success
danube_blueprint_sync_duration_seconds
danube_blueprint_changes_applied_total{type="pipeline|user|team"}
```

**Egress**:

```text
danube_egress_requests_total{decision="allow|deny", host="..."}
danube_egress_bytes_total{host="..."}
danube_egress_denied_total{reason="..."}
```

## Tracing

Master creates OpenTelemetry spans for:

**HTTP Requests**:

```text
Span: POST /api/v1/jobs
├─ Span: Check auth
├─ Span: Load pipeline
├─ Span: Create job record
└─ Span: Enqueue job
```

**Job Execution**:

```text
Span: Job abc123
├─ Span: Runner start environment
├─ Span: Wait for Coordinator
├─ Span: Execute step 1
│  ├─ Span: RPC RunStep
│  └─ Span: Runner exec
├─ Span: Upload artifacts
├─ Span: Generate provenance
└─ Span: Runner cleanup
```

**Egress Requests**:

```text
Span: Egress github.com
├─ Span: Match allowlist
├─ Span: Proxy request
└─ Span: Record audit event
```

## Health Checks

### `GET /health`

Liveness probe. Returns 200 when the API process is responsive.

```json
{
  "status": "ok",
  "timestamp": "2026-01-10T12:34:56Z"
}
```

### `GET /health/ready`

Readiness probe. Returns 200 when required subsystems are healthy.

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "runner": "ok",
    "container_runtime": "ok",
    "blueprint_sync": "ok",
    "disk": "ok"
  },
  "timestamp": "2026-01-10T12:34:56Z"
}
```

Returns 503 with `"status": "unavailable"` when a required check fails; the
offending check is marked `"error"` in `checks`.

## Periodic Health Checks

Master periodically checks:

1. SQLite connectivity
2. rootless Podman availability
3. runner cleanup backlog
4. Blueprint sync freshness
5. disk usage under `/var/lib/danube`
6. egress proxy health if enabled

## Alerting Recommendations

### Critical

- Master process down
- Database unavailable
- Container runtime unavailable
- Disk usage >90%
- Cleanup failures accumulating
- Job failure rate >50% over 1 hour

### Warning

- Blueprint sync stale
- High timeout rate
- High database lock contention
- Egress denied spike
- SSE connection failures
- Workspace/cache growth above expected limits

## Log Aggregation

Production deployments should forward structured Master logs to a central system such as Loki, Elasticsearch, or CloudWatch.

Job logs remain available through Danube's artifact/log storage and retention policy.
