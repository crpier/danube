"""Security primitives for Danube: secret encryption and the SecretService.

`SecretCipher` is the AES-256-GCM encrypt/decrypt over the on-disk blob format
documented in `docs/architecture/security.md`; `SecretService` is the job-scoped
in-memory cache of decrypted secrets served to the Coordinator over RPC
(`docs/architecture/components.md`, SecretService).
"""

from danube.security.crypto import (
    KEY_SIZE,
    NONCE_SIZE,
    InvalidKeyError,
    SecretCipher,
    generate_key,
    load_key,
)
from danube.security.service import SecretService, store_secret

__all__ = [
    "KEY_SIZE",
    "NONCE_SIZE",
    "InvalidKeyError",
    "SecretCipher",
    "SecretService",
    "generate_key",
    "load_key",
    "store_secret",
]
