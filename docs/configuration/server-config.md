# Server Configuration

## Overview

Danube requires a small local server configuration file. Most operational configuration is managed through the Blueprint repository.

## Configuration File

**Location**: `/etc/danube/danube.toml`

**Format**: TOML

## Minimal Configuration

```toml
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
branch = "main"
sync_interval = "60s"
```

## Full Configuration Reference

```toml
[server]
bind_address = "0.0.0.0:8080"
rpc_address = "127.0.0.1:9000"
data_dir = "/var/lib/danube"

[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
branch = "main"
sync_interval = "60s"
ssh_key_path = "/var/lib/danube/keys/git_deploy_key"

[runner]
type = "local"
runtime = "podman"                # initial supported runtime
coordinator_image = "danube-coordinator:latest"
max_concurrent_jobs = 4

[networking]
default_deny_egress = true
egress_proxy_enabled = true
egress_proxy_address = "127.0.0.1:9080"
registry_address = "127.0.0.1:5000"

[database]
path = "/var/lib/danube/danube.db"

[logging]
level = "info"
format = "json"
output = "stdout"

[observability]
otel_endpoint = ""
metrics_enabled = true
traces_enabled = false
```

## Environment Variables

```bash
export DANUBE_BIND_ADDRESS="0.0.0.0:8080"
export DANUBE_RPC_ADDRESS="127.0.0.1:9000"
export DANUBE_DATA_DIR="/var/lib/danube"
export DANUBE_CONFIG_REPO_URL="git@github.com:myorg/danube-blueprint.git"
export DANUBE_CONFIG_REPO_BRANCH="main"
export DANUBE_RUNNER_RUNTIME="podman"
export DANUBE_LOG_LEVEL="debug"
```

Environment variables override config file values.

## Configuration Loading Order

1. Hardcoded defaults
2. `/etc/danube/danube.toml`
3. Environment variables

## Validation

On startup, Master validates:

- `config_repo.url` is a valid Git URL
- `server.data_dir` exists and is writable
- SQLite database path is writable
- rootless Podman API is available to the `danube` user
- runner can create/inspect containers
- egress proxy/firewall settings are compatible
- `sync_interval` is a valid duration

If validation fails, Master exits with an error.

## Blueprint Repository Authentication

### SSH Key

```toml
[config_repo]
url = "git@github.com:myorg/danube-blueprint.git"
ssh_key_path = "/var/lib/danube/keys/git_deploy_key"
```

Setup:

```bash
ssh-keygen -t ed25519 -f /var/lib/danube/keys/git_deploy_key -N ''
chmod 600 /var/lib/danube/keys/git_deploy_key
```

Add the public key to the Blueprint repository as a read-only deploy key.

### HTTPS with Token

Supported, but less preferred because credentials are easier to leak through config/process inspection.

## Production Recommendations

- Run Danube on a dedicated Linux host.
- Restrict config permissions: `chmod 600 /etc/danube/danube.toml`.
- Use SSH deploy keys for the Blueprint repo.
- Use SSD-backed `/var/lib/danube`.
- Keep Podman patched.
- Prefer rootless runtime mode where practical.
- Put Danube behind TLS.
- Keep `default_deny_egress = true` unless explicitly operating in a trusted dev mode.

## Configuration Reload

```bash
kill -HUP $(pgrep -f "danube master")
```

Reloadable:

- logging level/format
- observability settings
- Blueprint repo settings
- runner concurrency limits where supported
- egress allowlist through Blueprint sync

Requires restart:

- server bind addresses
- data directory
- selected runner runtime
