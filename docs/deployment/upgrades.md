# Upgrade Guide

## Overview

Danube upgrades involve updating the Python package, applying database migrations, and restarting the appliance service. Use draining mode to avoid interrupting active jobs.

## Pre-Upgrade Checklist

- [ ] Review release notes
- [ ] Back up SQLite database
- [ ] Back up `/var/lib/danube/keys/`
- [ ] Check disk space under `/var/lib/danube`
- [ ] Verify current version: `danube version`
- [ ] Verify rootless Podman health
- [ ] Test upgrade in staging if available

## Upgrade Procedure

### 1. Enter Draining Mode

```bash
danube drain --timeout=3600
danube status
```

Draining mode:

- rejects new manual/webhook jobs
- skips cron triggers
- lets running jobs finish
- shows draining status in API/UI

If needed:

```bash
danube drain --force
danube drain --cancel
```

### 2. Stop Service

```bash
sudo systemctl stop danube
```

### 3. Back Up Database and Keys

```bash
sqlite3 /var/lib/danube/danube.db ".backup /var/lib/danube/danube.db.backup-$(date +%Y%m%d-%H%M%S)"
cp -r /var/lib/danube/keys /var/lib/danube/keys.backup-$(date +%Y%m%d-%H%M%S)
```

### 4. Upgrade Package

```bash
uv tool upgrade danube
# or a specific version
uv tool upgrade danube --version=1.2.0
```

### 5. Run Migrations

```bash
uv run alembic upgrade head
```

### 6. Start Service

```bash
sudo systemctl start danube
sudo systemctl status danube
sudo journalctl -u danube -f
```

### 7. Verify Health

```bash
curl http://localhost:8080/health/ready
danube status
```

Trigger a small test job if available.

## Rollback Procedure

```bash
sudo systemctl stop danube
cp /var/lib/danube/danube.db.backup-YYYYMMDD-HHMMSS /var/lib/danube/danube.db
uv tool uninstall danube
uv tool install danube==<previous-version>
sudo systemctl start danube
```

If a migration must be rolled back:

```bash
uv run alembic downgrade -1
```

## Podman Upgrades

Podman can be upgraded separately. Drain Danube first:

```bash
danube drain --timeout=3600
sudo systemctl stop danube
# upgrade podman through OS package manager
sudo systemctl start danube
curl http://localhost:8080/health/ready
```

After Podman upgrades, verify:

- `danube` user can access rootless Podman
- Podman API socket is available
- existing stale pods/containers can be cleaned
- a test job can start, exec, stream logs, and clean up

## Automated Upgrade Skeleton

```bash
#!/bin/bash
set -euo pipefail

BACKUP_FILE="/var/lib/danube/danube.db.backup-$(date +%Y%m%d-%H%M%S)"
sqlite3 /var/lib/danube/danube.db ".backup $BACKUP_FILE"

danube drain --timeout=1800
sudo systemctl stop danube
uv tool upgrade danube
uv run alembic upgrade head
sudo systemctl start danube

sleep 5
curl -f http://localhost:8080/health/ready >/dev/null
```

## Common Issues

### Database locked

```bash
lsof /var/lib/danube/danube.db
pkill -f danube
uv run alembic upgrade head
```

### Podman unavailable after upgrade

```bash
sudo -u danube podman info
```

Check subuid/subgid setup, rootless Podman storage, API socket permissions, and systemd service environment.

### Stale job containers

```bash
danube runner reconcile
```

The runner should reconcile recorded `runner_state` with actual Podman resources and remove abandoned job resources.

### Blueprint sync fails after upgrade

```bash
danube blueprint validate --repo /path/to/blueprint
danube blueprint sync --force
```

## Downtime Estimate

| Upgrade Type | Typical Downtime |
|--------------|------------------|
| Patch | 1-2 minutes |
| Minor | 2-5 minutes |
| Major | 5-15 minutes |

Draining adds time for active jobs to finish.
