# Installation Guide

## Prerequisites

### System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 vCPU | 8+ vCPU |
| RAM | 8 GB | 16+ GB |
| Disk | 50 GB SSD | 200+ GB SSD |
| OS | Ubuntu 22.04, Debian 12 | Ubuntu 22.04 LTS |
| Kernel | Modern Linux with namespaces/cgroups | 5.10+ |

### Required Software

- Python 3.14+
- UV package manager
- Git
- Podman, managed by the Danube installer

## Installation Methods

### Method 1: Bootstrap Installer

The bootstrap installer prepares a single host for Danube:

```bash
curl -fsSL https://get.danube.dev | bash
```

Expected responsibilities:

1. Check OS, kernel, CPU, RAM, and disk
2. Install or validate Python/UV
3. Install or validate rootless Podman
4. Create `danube` user
5. Create `/var/lib/danube`
6. Generate required keys
7. Write `/etc/danube/danube.toml`
8. Install systemd service
9. Start Danube

### Method 2: Manual Installation

#### Step 1: Install Runtime

Install Podman:

```bash
sudo apt-get update
sudo apt-get install -y podman git sqlite3 uidmap
podman info
```

Danube uses Podman rootless mode through the Podman API.

#### Step 2: Create User and Data Directory

```bash
sudo useradd -r -s /bin/bash -d /var/lib/danube -m danube
sudo mkdir -p /var/lib/danube/{logs,artifacts,workspaces,registry,keys}
sudo chown -R danube:danube /var/lib/danube
sudo chmod 700 /var/lib/danube/keys
```

Configure rootless Podman for the `danube` user. The installer should normally handle `/etc/subuid`, `/etc/subgid`, user runtime directories, and the Podman API socket.

#### Step 3: Validate Rootless Podman

```bash
sudo -u danube podman info
sudo -u danube system service --time=0 unix:///run/user/$(id -u danube)/podman/podman.sock
```

In production, the Podman API service should be managed by systemd for the `danube` user. The Danube installer is responsible for configuring this reliably.

#### Step 4: Generate Keys

The encryption key and data-directory tree are created for you by `danube init`
(see Step 6), so you normally only need to generate the SSH keys here. To create
the encryption key manually instead:

```bash
sudo -u danube openssl rand -out /var/lib/danube/keys/encryption.key 32
sudo chmod 600 /var/lib/danube/keys/encryption.key
```

> **Warning:** Never regenerate the encryption key once secrets are stored — a new
> key cannot decrypt existing secrets. `danube init` refuses to overwrite an
> existing key unless `--force` is passed.

The SSH keys (Blueprint deploy key, provenance signing key) are not managed by
`danube init`:

```bash
sudo -u danube ssh-keygen -t ed25519 -f /var/lib/danube/keys/signing.key -N '' -C "danube-provenance"
sudo chmod 600 /var/lib/danube/keys/signing.key

sudo -u danube ssh-keygen -t ed25519 -f /var/lib/danube/keys/git_deploy_key -N '' -C "danube-blueprint"
sudo chmod 600 /var/lib/danube/keys/git_deploy_key
```

#### Step 5: Install Danube

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install danube
```

From source:

```bash
git clone https://github.com/yourorg/danube.git
cd danube
uv sync
uv run danube --help
```

#### Step 6: Bootstrap and Configure

Run `danube init` to create the data-directory tree, generate the encryption key
(0600), and write a starter `/etc/danube/danube.toml`:

```bash
sudo -u danube danube init --data-dir /var/lib/danube --config /etc/danube/danube.toml
```

`danube init` is idempotent and never overwrites an existing key or config without
`--force`. Then edit `/etc/danube/danube.toml` for your environment. A full
annotated example ships in the repository at `deploy/danube.toml.example`; the
field reference is in [server-config.md](../configuration/server-config.md). To
sync pipelines from a Blueprint repository, fill in the `[config_repo]` block:

```toml
[config_repo]
url = "git@github.com:yourorg/danube-blueprint.git"
branch = "main"
sync_interval = "60s"
ssh_key_path = "/var/lib/danube/keys/git_deploy_key"
```

Add `/var/lib/danube/keys/git_deploy_key.pub` to your Blueprint repository deploy keys.

#### Step 7: Install the Systemd Service

A ready-to-use unit ships at `deploy/danube.service`. Install it:

```bash
sudo cp deploy/danube.service /etc/systemd/system/danube.service
```

It runs `danube master --config /etc/danube/danube.toml` as the `danube` user with
filesystem hardening (`ProtectSystem=strict`, writable only under
`/var/lib/danube`). Adjust the `ExecStart` path if `danube` is installed elsewhere.

#### Step 8: Start Danube

```bash
sudo systemctl daemon-reload
sudo systemctl enable danube
sudo systemctl start danube
sudo systemctl status danube
```

## Post-Installation Setup

### Create Blueprint Repository

```bash
mkdir danube-blueprint
cd danube-blueprint
git init
mkdir pipelines
```

`config.json`:

```json
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {"name": "global"},
  "spec": {
    "retention": {
      "logs_days": 30,
      "artifacts_days": 14,
      "registry_images_days": 30,
      "workspaces_days": 0
    },
    "networking": {
      "default_deny_egress": true,
      "egress_proxy_enabled": true,
      "egress_allowlist": ["github.com", "registry.npmjs.org", "pypi.org"]
    }
  }
}
```

`users.json`:

```json
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "User",
    "metadata": {"name": "admin"},
    "spec": {
      "email": "admin@example.com",
      "password_hash": "$2b$12$KIXxKj5M..."
    }
  }
]
```

`teams.json`:

```json
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "Team",
    "metadata": {"name": "admins"},
    "spec": {
      "members": ["admin@example.com"],
      "global_admin": true
    }
  }
]
```

Commit and push:

```bash
git add .
git commit -m "Initial Danube blueprint"
git remote add origin git@github.com:yourorg/danube-blueprint.git
git push -u origin main
```

### Verify Installation

```bash
curl http://localhost:8080/health
curl http://localhost:8080/health/ready
sudo journalctl -u danube -f
```

Open:

```text
http://localhost:8080
```

## Network Configuration

Allow incoming HTTP if exposing directly:

```bash
sudo ufw allow 8080/tcp
```

Use a reverse proxy with TLS for production.

### Nginx Example

```nginx
server {
    listen 80;
    server_name danube.example.com;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/v1/jobs/ {
        proxy_pass http://localhost:8080;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
        proxy_buffering off;
        proxy_cache off;
    }
}
```

## Troubleshooting

### Danube won't start

```bash
sudo journalctl -u danube -n 100 --no-pager
```

Common issues:

- invalid `/etc/danube/danube.toml`
- data directory permissions
- Blueprint repo auth failure
- rootless Podman unavailable
- database locked

### Runtime problems

```bash
sudo -u danube podman info
```

Check Podman rootless/API access for the `danube` user.

### Blueprint sync failing

```bash
sudo journalctl -u danube | grep blueprint_sync
danube blueprint validate --repo /path/to/blueprint
```

## Uninstallation

```bash
sudo systemctl stop danube
sudo systemctl disable danube
uv tool uninstall danube
sudo rm -rf /var/lib/danube
sudo rm /etc/danube/danube.toml
sudo rm /etc/systemd/system/danube.service
```
