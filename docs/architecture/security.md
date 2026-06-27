# Security Architecture

## Threat Model

| Threat | Mitigation |
|--------|------------|
| Malicious pipeline code escapes container | Use standard container runtime isolation, no privileged containers, no host namespaces, seccomp/AppArmor, dropped capabilities |
| Pipeline reads host files | Mount only per-job workspace and required read-only assets |
| Pipeline consumes host resources | cgroup CPU/memory/pids limits, timeouts, global concurrency limits |
| Secret exfiltration via logs | Secrets fetched through RPC, scoped per job, scrubbed from logs |
| Unauthorized API access | OIDC/JWT authentication and team-based RBAC |
| Tampered artifacts | Signed provenance and artifact metadata |
| Coordinator spoofs Worker control | Coordinator cannot exec directly; all commands route through Master |
| Uncontrolled internet access | Default-deny job networking, egress proxy, allowlist policy, firewall enforcement |
| Configuration tampering | Blueprint Git repo is source of truth; sync validates schemas and logs diffs |
| Key compromise | Keys stored with 0600 permissions; production deployments should use secure backup/KMS practices |

## Security Boundary

Danube does not implement low-level container isolation itself. The default runner uses rootless Podman and Linux kernel primitives:

- namespaces
- cgroups
- seccomp
- AppArmor or SELinux where available
- Linux capabilities
- runtime network isolation
- host firewall rules

Danube owns the orchestration policy that applies these controls consistently for CI jobs.

## Container Hardening

Job containers should be created with secure defaults:

- no privileged mode
- no host network
- no host PID namespace
- no host IPC namespace
- no arbitrary host mounts
- non-root user where image supports it
- dropped capabilities by default
- read-only root filesystem where practical
- writable per-job `/workspace` only
- explicit CPU, memory, and pids limits
- automatic cleanup after completion or failure

The Worker image is user-controlled, so Danube must assume Worker code is untrusted.

## Secrets Management

### Architecture: SecretService

Secrets are not placed in container manifests by default. They are accessed through Master RPC.

```text
1. Secrets stored encrypted in SQLite using AES-256-GCM
2. Encryption key stored at /var/lib/danube/keys/encryption.key
3. Job starts; Master loads authorized secrets into a job-scoped cache
4. Coordinator requests GetSecret(job_id, key)
5. Master validates active job and secret permission
6. Secret value is returned to Coordinator
7. Cache is cleared when job completes
```

### Benefits

- Secrets are not visible in runtime inspect output by default
- Secrets are loaded on demand
- Access can be audited per job
- Secrets can be scoped to pipelines
- Log scrubbing can use the active secret set

### Encryption Details

**Algorithm**: AES-256-GCM.

**Encrypted blob format**:

```text
[12-byte nonce][ciphertext][16-byte auth tag]
```

**Storage**:

```sql
CREATE TABLE secrets (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT REFERENCES pipelines(id),
    key TEXT NOT NULL,
    value_encrypted BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Controlled Egress

Danube's default security posture is that job containers cannot directly reach arbitrary external networks.

Recommended model:

```text
Worker container
  └── only allowed outbound path: Danube egress proxy
          └── approved domains from Blueprint allowlist
```

The runner/firewall prevents bypassing the proxy. The proxy enforces domain allowlists and records outbound access.

Blueprint example:

```json
{
  "spec": {
    "egress_allowlist": [
      "github.com",
      "*.githubusercontent.com",
      "registry.npmjs.org",
      "pypi.org"
    ]
  }
}
```

For stricter deployments, teams should use internal mirrors for Git, packages, and images, then allow only local endpoints.

## SLSA-Oriented Build Security

Danube targets supply-chain-friendly build behavior:

| Requirement | Implementation |
|-------------|----------------|
| Ephemeral environments | Per-job containers and workspace deleted after completion |
| Controlled dependencies | Default-deny egress and allowlisted outbound access |
| Provenance generation | Master records build definition, inputs, outputs, and environment |
| Non-falsifiable control path | Master mediates command execution and logs |
| Secret isolation | Secrets fetched through audited RPC path |

### Provenance Generation

Master generates a provenance document after job completion.

**Format**: in-toto attestation JSON.

**Contents**:

- pipeline and job identifiers
- trigger type and Git ref/SHA
- Worker image and runtime metadata
- commands executed
- input source reference
- output artifacts/images
- timestamps and result status

**Signature**: Ed25519 using `/var/lib/danube/keys/signing.key`.

**Storage**:

```text
/var/lib/danube/artifacts/<job_id>/provenance.json
/var/lib/danube/artifacts/<job_id>/provenance.sig
```

## Authentication & Authorization

### Authentication

Danube authenticates UI/API users through OIDC/JWT or an embedded identity provider configured by the appliance.

JWT validation must check:

- signature
- expiry
- issuer
- audience
- subject/user mapping

### Authorization: Team-Based RBAC

Model:

- Users belong to Teams
- Teams receive Permissions on Pipelines
- Permission levels: `read`, `write`, `admin`

| Action | Required Level |
|--------|----------------|
| View pipeline | `read` |
| Trigger job | `write` |
| Cancel job | `write` |
| View logs | `read` |
| Download artifacts | `read` |
| Manage secrets | `admin` |
| Modify pipeline config | via Blueprint Git workflow |

Teams with `global_admin: true` have full access.

## Audit Logging

Security-relevant events are written to structured Master logs:

- user login/logout
- permission denied
- secret access
- pipeline trigger
- job cancellation
- Blueprint sync change
- egress allow/deny
- runner security failure
- cleanup failure

Example:

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

## Production Recommendations

1. Run Danube on a dedicated host.
2. Keep the host patched.
3. Use Danube-managed rootless Podman.
4. Restrict access to the Danube HTTP port.
5. Terminate TLS with a reverse proxy or built-in TLS.
6. Back up `/var/lib/danube/keys/` securely.
7. Require Blueprint PR review and branch protection.
8. Prefer internal package/image mirrors for high-security builds.
9. Alert on denied egress spikes, failed logins, cleanup failures, and disk usage.
10. Scan Worker images before use.
