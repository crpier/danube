# Server Configuration

## Overview

Danube requires minimal server configuration. Most settings are managed via the Blueprint (GitOps) repository.

## Configuration File

**Location**: `/etc/danube/danube.toml`

**Format**: TOML

## Minimal Configuration

This is the ONLY required configuration:

```toml
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
branch = "main"
sync_interval = "60s"
```

## Full Configuration Reference

```toml
# Server settings
[server]
bind_address = "0.0.0.0:8080"  # HTTP API listen address
rpc_address = "0.0.0.0:9000"   # HTTP/2 control plane address
data_dir = "/var/lib/danube"   # Data directory path

# Blueprint (GitOps) repository
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"  # Git URL (SSH or HTTPS)
branch = "main"                                     # Branch to track
sync_interval = "60s"                               # Poll interval (e.g., "30s", "5m")
ssh_key_path = "/var/lib/danube/keys/git_deploy_key"  # SSH key for private repos

# Kubernetes
[kubernetes]
context = ""                    # K8s context (empty = in-cluster config)
namespace_system = "danube-system"  # System namespace
namespace_jobs = "danube-jobs"      # Jobs namespace

# Database
[database]
path = "/var/lib/danube/danube.db"  # SQLite database file

# Logging
[logging]
level = "info"                  # trace, debug, info, warn, error
format = "json"                 # json or text
output = "stdout"               # stdout or file path

# Observability (optional)
[observability]
otel_endpoint = ""              # OpenTelemetry collector (e.g., "http://localhost:4317")
metrics_enabled = true          # Enable /metrics endpoint
traces_enabled = false          # Enable trace export
```

## Environment Variables

Configuration can be overridden via environment variables:

```bash
# Server
export DANUBE_BIND_ADDRESS="0.0.0.0:8080"
export DANUBE_DATA_DIR="/var/lib/danube"

# Config repo
export DANUBE_CONFIG_REPO_URL="git@github.com:myorg/danube-blueprint.git"
export DANUBE_CONFIG_REPO_BRANCH="main"

# Logging
export DANUBE_LOG_LEVEL="debug"
export DANUBE_LOG_FORMAT="json"

# K8s
export DANUBE_K8S_NAMESPACE_JOBS="danube-jobs"
```

Environment variables take precedence over config file values.

## Configuration Loading Order

1. Default values (hardcoded)
2. Config file (`/etc/danube/danube.toml`)
3. Environment variables (highest priority)

## Validation

Master validates configuration on startup:

- `config_repo.url` must be valid Git URL
- `server.data_dir` must exist and be writable
- `kubernetes.namespace_*` must exist in cluster
- `sync_interval` must be valid duration (e.g., "30s", "5m")

If validation fails, Master exits with error.

## Blueprint Repository Authentication

### SSH Key (Recommended)

```toml
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
ssh_key_path = "/var/lib/danube/keys/git_deploy_key"
```

**Setup**:
1. Generate key: `ssh-keygen -t ed25519 -f /var/lib/danube/keys/git_deploy_key -N ''`
2. Add public key to Git repository deploy keys (read-only)
3. Set permissions: `chmod 600 /var/lib/danube/keys/git_deploy_key`

### HTTPS with Token

```toml
[config_repo]
url = "https://oauth2:YOUR_TOKEN@github.com/myorg/danube-blueprint.git"
```

**Not recommended**: Token visible in config file and process list.

### HTTPS with Credentials Store

```bash
# Configure Git credential helper
git config --global credential.helper store
echo "https://user:token@github.com" > ~/.git-credentials
```

## Production Recommendations

### Security

- Store config file with restricted permissions: `chmod 600 /etc/danube/danube.toml`
- Use SSH key authentication for Blueprint repo
- Rotate SSH keys periodically
- Never commit secrets to config file (use Blueprint repo for secret references only)

### Performance

- Increase `sync_interval` for large Blueprint repos: `"5m"` instead of `"60s"`
- Use SSD storage for `data_dir`
- Co-locate Master and K8s API server on same network for low latency

### Monitoring

- Enable observability settings
- Configure OTLP endpoint for centralized metrics/traces
- Set log level to `info` or `warn` in production (not `debug`)

## Configuration Reload

To reload configuration without restarting Master:

```bash
# Send SIGHUP to Master process
kill -HUP $(pgrep -f "danube master")
```

Master reloads:
- Logging configuration
- Observability settings
- Blueprint repo settings (triggers immediate sync)

**Not reloaded** (requires restart):
- Server bind addresses
- K8s namespaces
- Data directory path
