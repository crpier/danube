"""SecretService: job-scoped in-memory cache of decrypted secrets.

The Master loads a job's authorized secrets from SQLite when the job starts,
decrypts them with AES-256-GCM, and caches the plaintext keyed by
``(job_id, secret_key)`` (`docs/architecture/components.md`, SecretService). The
Coordinator then fetches a value on demand over RPC; the cache is cleared when the
job ends so plaintext never outlives the job.

A pipeline is authorized for a secret when the secret is scoped to that pipeline
or is global (``pipeline_id IS NULL``). Authorization is therefore resolved at
load time: a key absent from the cache is, by construction, one the pipeline may
not read.

`store_secret` is the write side used by the admin CLI: it encrypts a value and
upserts it into the ``secrets`` table.
"""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Self

from snekql.sqlite import Database, insert, select, update

from danube.db.models import Secret
from danube.security.crypto import SecretCipher, load_key

SecretDecryptor = Callable[[bytes], str]


def _plaintext_decrypt(blob: bytes) -> str:
    """Stand-in decryptor that treats the stored blob as UTF-8 plaintext.

    Used only when no encryption key is configured (e.g. in tests that seed
    plaintext bytes). Production callers build the service with a `SecretCipher`
    via `SecretService.from_key_file` so real AES-256-GCM decryption is used.
    """
    return blob.decode("utf-8")


class SecretService:
    """Loads, caches, and serves a job's authorized decrypted secrets."""

    def __init__(
        self, db: Database, *, decrypt: SecretDecryptor = _plaintext_decrypt
    ) -> None:
        self._db = db
        self._decrypt = decrypt
        # Plaintext secrets keyed by (job_id, secret_key); cleared per job on end.
        self._cache: dict[tuple[str, str], str] = {}

    @classmethod
    def with_cipher(cls, db: Database, cipher: SecretCipher) -> Self:
        """Build a service that decrypts with the given AES-256-GCM cipher."""
        return cls(db, decrypt=cipher.decrypt)

    @classmethod
    def from_key_file(cls, db: Database, key_path: Path | str) -> Self:
        """Build a service whose cipher key is read from ``key_path``."""
        return cls.with_cipher(db, SecretCipher(load_key(key_path)))

    async def load_for_job(self, job_id: str, pipeline_id: str) -> None:
        """Decrypt and cache every secret the pipeline is authorized for.

        Loads pipeline-scoped secrets plus global (pipeline-less) secrets, so the
        cached key set is exactly what this job may read.
        """
        async with self._db.transaction() as tx:
            rows = await tx.fetch_all(
                select(Secret).where(
                    Secret.pipeline_id.eq(pipeline_id) | Secret.pipeline_id.is_null()
                )
            )
        for row in rows:
            self._cache[(job_id, row.key)] = self._decrypt(row.value_encrypted)

    def get(self, job_id: str, key: str) -> str | None:
        """Return the cached secret value, or ``None`` if not authorized/loaded."""
        return self._cache.get((job_id, key))

    def active_values(self, job_id: str) -> list[str]:
        """Return every cached plaintext value for the job, for log scrubbing."""
        return [
            value
            for (cached_job, _), value in self._cache.items()
            if cached_job == job_id
        ]

    def clear_job(self, job_id: str) -> None:
        """Drop all cached secrets for the job. Idempotent."""
        for cache_key in [key for key in self._cache if key[0] == job_id]:
            del self._cache[cache_key]


async def store_secret(
    db: Database,
    cipher: SecretCipher,
    *,
    key: str,
    value: str,
    pipeline_id: str | None = None,
) -> str:
    """Encrypt ``value`` and upsert it as ``(pipeline_id, key)``; return its id.

    A matching ``(pipeline_id, key)`` row has its ciphertext replaced; otherwise a
    new row is inserted with a fresh UUID.
    """
    blob = cipher.encrypt(value)
    async with db.transaction() as tx:
        predicate = Secret.key.eq(key)
        predicate = (
            predicate & Secret.pipeline_id.is_null()
            if pipeline_id is None
            else predicate & Secret.pipeline_id.eq(pipeline_id)
        )
        existing = await tx.fetch_one_or_none(select(Secret).where(predicate))
        if existing is not None:
            _ = await tx.execute(
                update(Secret)
                .set(Secret.value_encrypted.to(blob))
                .where(Secret.id.eq(existing.id))
            )
            return existing.id
        secret_id = str(uuid.uuid4())
        await tx.execute(
            insert(
                Secret(
                    id=secret_id,
                    pipeline_id=pipeline_id,
                    key=key,
                    value_encrypted=blob,
                )
            )
        )
        return secret_id
