# Installation Guide

## Prerequisites

### System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 vCPU | 8+ vCPU |
| RAM | 8 GB | 16+ GB |
| Disk | 50 GB SSD | 200+ GB SSD |
| OS | Ubuntu 22.04, Debian 12 | Ubuntu 22.04 LTS |
| Kernel | 4.9+ (eBPF) | 5.10+ |

### Required Software

- Python 3.14+
- [UV](https://github.com/astral-sh/uv) package manager
- Git
- K3s (installed by Danube installer)
- Cilium CLI (installed by Danube installer)

## Installation Methods

### Method 1: Helm Chart (Recommended)

The Helm chart deploys Danube, Dex, and the registry into your K3s cluster with local-path storage and best-effort durability.

```bash
helm repo add danube https://charts.danube.dev
helm repo update

helm upgrade --install danube danube/danube \
  --namespace danube-system \
  --create-namespace \
  --values values.yaml
```

**What the chart does**:
1. Creates `danube-system` and `danube-jobs` namespaces
2. Deploys Master, Dex, and the registry
3. Provisions PVCs for SQLite, logs, artifacts, and registry storage
4. Applies default NetworkPolicies and services
5. Enables Blueprint sync configuration
6. Pins stateful workloads to the node with local-path volumes

### Method 2: Bootstrap Installer (Optional)

If you want a single-command install on a fresh host, use the bootstrap installer. It installs K3s + Cilium and then runs the Helm chart.

```bash
curl -fsSL https://get.danube.dev | bash
```

### Method 3: Manual Installation

#### Step 1: Install K3s

```bash
# Install K3s with Cilium
curl -sfL https://get.k3s.io | sh -s - \
  --flannel-backend=none \
  --disable-network-policy \
  --write-kubeconfig-mode=644

# Wait for K3s to be ready
kubectl wait --for=condition=ready node --all --timeout=60s
```

#### Step 2: Install Cilium

```bash
# Install Cilium CLI
CILIUM_CLI_VERSION=$(curl -s https://raw.githubusercontent.com/cilium/cilium-cli/main/stable.txt)
CLI_ARCH=amd64
curl -L --fail --remote-name-all https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${CLI_ARCH}.tar.gz{,.sha256sum}
sha256sum --check cilium-linux-${CLI_ARCH}.tar.gz.sha256sum
sudo tar xzvfC cilium-linux-${CLI_ARCH}.tar.gz /usr/local/bin
rm cilium-linux-${CLI_ARCH}.tar.gz{,.sha256sum}

# Install Cilium CNI
cilium install --version 1.14.5

# Verify installation
cilium status --wait
```

#### Step 3: Create Data Directory

```bash
sudo mkdir -p /var/lib/danube/{logs,artifacts,registry,keys}
sudo chown -R $USER:$USER /var/lib/danube
chmod 700 /var/lib/danube/keys
```

#### Step 4: Generate Keys

```bash
# Encryption key for secrets
openssl rand -out /var/lib/danube/keys/encryption.key 32
chmod 600 /var/lib/danube/keys/encryption.key

# Signing key for provenance
ssh-keygen -t ed25519 -f /var/lib/danube/keys/signing.key -N '' -C "danube-provenance"
chmod 600 /var/lib/danube/keys/signing.key

# Git deploy key for Blueprint repository
ssh-keygen -t ed25519 -f /var/lib/danube/keys/git_deploy_key -N '' -C "danube-blueprint"
chmod 600 /var/lib/danube/keys/git_deploy_key
```

#### Step 5: Install Danube

```bash
# Install UV if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Danube from PyPI (when published)
uv tool install danube

# Or from source
git clone https://github.com/yourusername/danube.git
cd danube
uv sync
uv run danube --version
```

#### Step 6: Configure Blueprint Repository

Create `/etc/danube/danube.toml`:

```toml
[config_repo]
url = "git@github.com:yourorg/danube-blueprint.git"
branch = "main"
sync_interval = "60s"

[server]
bind_address = "0.0.0.0:8080"
data_dir = "/var/lib/danube"
```

**Add deploy key to your Git repository**:
```bash
# Print public key
cat /var/lib/danube/keys/git_deploy_key.pub

# Add this to your Git repository's deploy keys (read-only access)
```

Optionally configure a webhook in the Blueprint repository to trigger immediate syncs on push (fallback polling still runs every `sync_interval`).

#### Step 7: Create Systemd Service

Create `/etc/systemd/system/danube.service`:

```ini
[Unit]
Description=Danube CI/CD Master
After=network.target k3s.service
Requires=k3s.service

[Service]
Type=simple
User=danube
Group=danube
WorkingDirectory=/var/lib/danube
Environment="PATH=/home/danube/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
ExecStart=/home/danube/.local/bin/uv run danube master
Restart=on-failure
RestartSec=10s

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/danube

[Install]
WantedBy=multi-user.target
```

#### Step 8: Create Danube User

```bash
sudo useradd -r -s /bin/bash -d /var/lib/danube -m danube
sudo chown -R danube:danube /var/lib/danube
sudo usermod -aG k3s danube  # Grant access to K3s kubeconfig
```

#### Step 9: Start Danube

```bash
sudo systemctl daemon-reload
sudo systemctl enable danube
sudo systemctl start danube

# Check status
sudo systemctl status danube

# View logs
sudo journalctl -u danube -f
```

## Post-Installation Setup

### 1. Create Blueprint Repository

Create a Git repository with initial configuration:

```bash
mkdir danube-blueprint
cd danube-blueprint

# Create initial config
cat > config.json <<EOF
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {"name": "global"},
  "spec": {
    "retention": {
      "logs_days": 30,
      "artifacts_days": 14,
      "registry_images_days": 30
    },
    "egress_allowlist": [
      "registry.npmjs.org",
      "pypi.org",
      "github.com",
      "registry.danube-system"
    ]
  }
}
EOF

# Create initial user
cat > users.json <<EOF
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "User",
    "metadata": {"name": "admin"},
    "spec": {
      "email": "admin@example.com",
      "password_hash": "\$2b\$12\$KIXxKj5M..."
    }
  }
]
EOF

# Create initial team
cat > teams.json <<EOF
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
EOF

mkdir pipelines

git init
git add .
git commit -m "Initial Danube blueprint"
git remote add origin git@github.com:yourorg/danube-blueprint.git
git push -u origin main
```

### 2. Verify Installation

```bash
# Check Danube is running
curl http://localhost:8080/health

# Check Kubernetes resources
kubectl get pods -n danube-system
kubectl get pods -n danube-jobs

# Access UI
open http://localhost:8080
```

### 3. First Login

1. Open browser to `http://localhost:8080`
2. Redirected to Dex login page
3. Enter credentials from `users.json`
4. Redirected back to Danube UI

## Network Configuration

### Firewall Rules

Allow incoming connections on port 8080:

```bash
# UFW
sudo ufw allow 8080/tcp

# iptables
sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
```

### Reverse Proxy Setup (Optional but Recommended)

#### Nginx

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
    
    # SSE log streaming requires these headers
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

#### Traefik

```yaml
# docker-compose.yml
services:
  traefik:
    image: traefik:v2.10
    command:
      - --providers.docker=true
      - --entrypoints.web.address=:80
    ports:
      - "80:80"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

  danube:
    image: danube:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.danube.rule=Host(`danube.example.com`)"
      - "traefik.http.services.danube.loadbalancer.server.port=8080"
```

## Troubleshooting

### Danube won't start

**Check logs**:
```bash
sudo journalctl -u danube -n 100 --no-pager
```

**Common issues**:
- K3s not running: `sudo systemctl status k3s`
- Invalid config: Check `/etc/danube/danube.toml` syntax
- Blueprint repo auth failed: Verify deploy key added to Git repo
- Database locked: Check file permissions on `/var/lib/danube/danube.db`


### Blueprint sync failing

```bash
# Check Blueprint sync logs
sudo journalctl -u danube | grep "blueprint_sync"

# Manual sync
 danube blueprint sync --verbose
```


### Kubernetes pods not starting

```bash
# Check pod status
kubectl get pods -n danube-jobs
kubectl describe pod <pod-name> -n danube-jobs

# Check events
kubectl get events -n danube-jobs --sort-by='.lastTimestamp'
```

### Cilium issues

```bash
# Check Cilium status
cilium status

# Restart Cilium
cilium restart

# Check connectivity
cilium connectivity test
```

## Upgrading

See [Upgrades](./upgrades.md) for upgrade procedures.

## Uninstallation

```bash
# Stop Danube
sudo systemctl stop danube
sudo systemctl disable danube

# Remove Danube
uv tool uninstall danube

# Uninstall K3s
/usr/local/bin/k3s-uninstall.sh

# Remove data (CAUTION: Deletes all jobs, logs, artifacts)
sudo rm -rf /var/lib/danube

# Remove config
sudo rm /etc/danube/danube.toml
sudo rm /etc/systemd/system/danube.service
```
