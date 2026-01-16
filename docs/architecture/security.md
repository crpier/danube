# Security Architecture

## Threat Model

| Threat | Mitigation |
|--------|------------|
| Malicious pipeline code escapes container | K3s containerd with seccomp/AppArmor, pods run as non-root |
| Secret exfiltration via logs | SecretService HTTP/2 access only, log scrubbing for patterns |
| Unauthorized API access | Dex OIDC authentication required, team-based RBAC |
| Tampered build artifacts | SLSA provenance with Ed25519 signatures |
| Coordinator → Worker spoofing | All commands route through Master, Workers cannot initiate connections |
| Supply chain attacks via dependencies | Cilium NetworkPolicy restricts egress to allowlist |
| Configuration tampering | Blueprint repo is source of truth, Git commit history provides audit trail |
| Encryption key compromise | File permissions 0600, recommend hardware security module for production |

## Secrets Management

### Architecture: SecretService

**NOT using environment variables.** Secrets accessed via HTTP/2 JSON:

```
1. Secrets stored in SQLite encrypted with AES-256-GCM
2. Encryption key in /var/lib/danube/keys/encryption.key (0600)
3. Job starts → Master loads pipeline secrets into SecretService cache
4. Coordinator requests: GetSecret(job_id, key) → value (HTTP/2 JSON)
5. Master validates job_id active and has access
6. Secret returned to Coordinator
7. Secrets never in logs, Pod env vars, or JSON manifests
8. Cache cleared when job completes
```

### Benefits vs Environment Variables

- Secrets not visible in `kubectl describe pod`
- Loaded on-demand only
- Better audit trail (track access per job)
- Can rotate without recreating Pods
- Reduces attack surface

### Encryption Details

**Algorithm**: AES-256-GCM (authenticated encryption)

**Key derivation**: Direct 32-byte key, no KDF (recommend key rotation policy)

**Storage**:
```sql
CREATE TABLE secrets (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT REFERENCES pipelines(id),
    key TEXT NOT NULL,
    value_encrypted BLOB NOT NULL,  -- Includes nonce + ciphertext + tag
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**Encrypted blob format**:
```
[12-byte nonce][ciphertext][16-byte auth tag]
```

### Secret Rotation

Secrets can be updated in Blueprint repo or via API. Master re-encrypts with current key:

```bash
# Add/update secret
danube secret set --pipeline=frontend-build API_KEY=new_value

# Rotate encryption key (requires decrypt + re-encrypt all secrets)
danube secret rotate-key --new-key=/path/to/new.key
```

## SLSA Level 3 Compliance

| Requirement | Implementation |
|-------------|----------------|
| **Hermetic builds** | Cilium NetworkPolicy allows only allowlisted domains |
| **Ephemeral environments** | Pods deleted immediately after job completion |
| **Provenance generation** | Master generates signed JSON provenance (in-toto format) |
| **Non-falsifiable** | Build steps in isolated pods, logs immutable |
| **Two-person review** | Out of scope (organizational policy via Git PRs) |

### Hermetic Builds

Cilium NetworkPolicy applied to `danube-jobs` namespace:

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: egress-allowlist
  namespace: danube-jobs
spec:
  endpointSelector: {}
  egress:
    - toFQDNs:
        - matchName: "registry.npmjs.org"
        - matchName: "pypi.org"
        - matchName: "github.com"
        - matchName: "registry.danube-system"
    - toEntities:
        - kube-apiserver  # Allow K8s API access for Master
```

Only allowlisted domains reachable from pipeline Pods.

### Provenance Generation

Master generates SLSA provenance document after job completes:

**Format**: in-toto attestation (JSON)

**Contents**:
- Build definition (pipeline, commit SHA, trigger)
- Build steps executed
- Input artifacts (source code ref)
- Output artifacts (images, tarballs)
- Build environment (Worker image, resource limits)

**Signature**: Ed25519 signature using `/var/lib/danube/keys/signing.key`

**Storage**: Saved to `/var/lib/danube/artifacts/<job_id>/provenance.json`

**Verification**:
```bash
# Verify provenance signature
danube provenance verify --job=abc123 --public-key=/var/lib/danube/keys/signing.key.pub
```

## Authentication & Authorization

### Authentication: Dex OIDC

**Flow**:
```
1. User visits Danube UI
2. FastAPI checks for JWT in cookie/header
3. No JWT → redirect to Dex login page
4. Dex shows login form (username/password)
5. Dex validates against users.json from Blueprint repo
6. Dex issues JWT with claims: sub, email, name
7. Redirect back to Danube with JWT
8. Danube validates JWT signature
9. Extract user identity from sub claim
10. Look up user in SQLite
11. Grant access based on team memberships
```

**JWT Validation**:
- Signature verified using Dex public key
- Expiry checked (default: 24 hours)
- Issuer claim matches Dex URL
- Audience claim matches Danube URL

### Authorization: Team-Based RBAC

**Model**:
- Users belong to Teams
- Teams have Permissions on Pipelines
- Permission levels: `read`, `write`, `admin`

**Permission Checks**:

| Action | Required Level |
|--------|----------------|
| View pipeline | `read` |
| Trigger job | `write` |
| Cancel job | `write` |
| View logs | `read` |
| Download artifacts | `read` |
| Modify pipeline config (via Blueprint PR) | N/A (Git-based) |
| Manage secrets | `admin` |
| Delete pipeline (via Blueprint) | `admin` |

**Global Admins**:

Teams with `global_admin: true` in Blueprint have full access to all pipelines.

**Example Permission Check**:
```python
async def check_permission(user: User, pipeline: Pipeline, level: str) -> bool:
    # Get user's teams
    teams = await get_user_teams(user.id)
    
    # Check if any team has required permission level
    for team in teams:
        perm = await get_pipeline_permission(pipeline.id, team.id)
        if perm and perm.level in get_levels_gte(level):
            return True
        if team.global_admin:
            return True
    
    return False

def get_levels_gte(level: str) -> list[str]:
    levels = {"read": ["read", "write", "admin"],
              "write": ["write", "admin"],
              "admin": ["admin"]}
    return levels[level]
```

## Network Security

### Pod Network Policies

Applied to `danube-jobs` namespace:

**Egress**:
- Allowlist-based (Cilium DNS filtering)
- No unrestricted internet access
- Internal registry allowed
- K8s API server allowed (for Master)

**Ingress**:
- No ingress allowed to pipeline Pods
- Coordinator and Worker cannot receive connections

### TLS

**External**:
- Recommend terminating TLS at reverse proxy (nginx, Traefik)
- Danube listens on HTTP internally
- Future: Built-in TLS with Let's Encrypt

**Internal**:
- HTTP/2 JSON between Coordinator and Master: plaintext (same pod network namespace, not exposed)
- K8s API: TLS (handled by kubernetes client library)

## Container Security

### Pod Security Standards

Applied to `danube-jobs` namespace:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: danube-jobs
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

**Enforced**:
- No privileged containers
- No host network/PID/IPC
- No host path volumes (except Master for logs/artifacts)
- Run as non-root user
- Read-only root filesystem (where possible)
- Drop all capabilities

### Image Scanning

**Recommendation**: Scan Coordinator and Worker images before use.

```bash
# Example with Trivy
trivy image danube-coordinator:latest
trivy image node:18
```

## Audit Logging

All security-relevant events logged to Master logs:

- User login/logout
- Permission denied events
- Secret access
- Pipeline trigger (who, what, when)
- Configuration changes (from Blueprint sync)
- Job cancellations

**Log format**: Structured JSON with fields:
```json
{
  "timestamp": "2026-01-10T12:34:56Z",
  "level": "info",
  "event": "secret_accessed",
  "user": "alice@example.com",
  "job_id": "abc123",
  "secret_key": "API_KEY",
  "pipeline": "frontend-build"
}
```

## Security Recommendations

### Production Deployment

1. **TLS**: Use reverse proxy with valid certificate
2. **Firewall**: Restrict access to Danube port (default 8080)
3. **Key Management**: Store encryption key in HSM or KMS
4. **Backups**: Encrypt backups of `/var/lib/danube/keys/`
5. **Monitoring**: Alert on failed login attempts, permission denied events
6. **Updates**: Subscribe to security advisories, apply patches promptly
7. **Blueprint Repo**: Require signed commits, enforce branch protection
8. **Image Registry**: Use private registry with scanning, don't pull untrusted images

### Secret Management Best Practices

1. **Rotation**: Rotate secrets regularly (recommend 90 days)
2. **Scope**: Use pipeline-specific secrets, not global where possible
3. **Principle of Least Privilege**: Grant access only to pipelines that need it
4. **Audit**: Review secret access logs periodically
5. **Storage**: Never commit secrets to Git; use Blueprint only for configuration structure
