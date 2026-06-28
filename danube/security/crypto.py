"""AES-256-GCM secret encryption (`docs/architecture/security.md`, Encryption Details).

Secrets are stored at rest as a single opaque blob with the layout:

    [12-byte nonce][ciphertext][16-byte auth tag]

`AESGCM.encrypt` already appends the 16-byte tag to the ciphertext, so the stored
blob is simply ``nonce + AESGCM.encrypt(nonce, plaintext, None)``. A fresh random
nonce is drawn for every encryption; reusing a nonce under the same key breaks
GCM, so callers must always go through `SecretCipher.encrypt`.

The key is a raw 32-byte AES-256 key read from `/var/lib/danube/keys/encryption.key`.
"""

import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# AES-256 key length and the GCM nonce length, in bytes.
KEY_SIZE = 32
NONCE_SIZE = 12


class InvalidKeyError(Exception):
    """The configured encryption key is missing or not a 32-byte AES-256 key."""


class SecretDecryptError(Exception):
    """A stored secret blob is malformed or fails AES-256-GCM authentication."""


def generate_key() -> bytes:
    """Return a fresh random 32-byte AES-256 key suitable for `SecretCipher`."""
    return os.urandom(KEY_SIZE)


def load_key(path: Path | str) -> bytes:
    """Read and validate the raw 32-byte AES-256 key at ``path``.

    Raises `InvalidKeyError` if the file is absent or not exactly 32 bytes; the
    key file holds raw key bytes (not text), written with 0600 permissions.
    """
    key_path = Path(path)
    try:
        key = key_path.read_bytes()
    except OSError as error:
        msg = f"encryption key file {key_path} could not be read: {error}"
        raise InvalidKeyError(msg) from error
    if len(key) != KEY_SIZE:
        msg = f"encryption key at {key_path} must be {KEY_SIZE} bytes, got {len(key)}"
        raise InvalidKeyError(msg)
    return key


class SecretCipher:
    """AES-256-GCM encrypt/decrypt over the stored ``nonce || ciphertext+tag`` blob."""

    __slots__ = ("_aesgcm",)

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_SIZE:
            msg = f"AES-256-GCM key must be {KEY_SIZE} bytes, got {len(key)}"
            raise InvalidKeyError(msg)
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt ``plaintext`` under a fresh nonce, returning the storage blob."""
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ciphertext

    def decrypt(self, blob: bytes) -> str:
        """Decrypt a storage blob, raising `SecretDecryptError` on tamper/format."""
        if len(blob) <= NONCE_SIZE:
            msg = "secret blob is too short to contain a nonce and ciphertext"
            raise SecretDecryptError(msg)
        nonce, ciphertext = blob[:NONCE_SIZE], blob[NONCE_SIZE:]
        try:
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        except InvalidTag as error:
            msg = "secret blob failed AES-256-GCM authentication"
            raise SecretDecryptError(msg) from error
        return plaintext.decode("utf-8")
