"""Unit tests for AES-256-GCM secret crypto and the SecretService cache.

These exercise the real encryption path (no plaintext stand-in): values are
encrypted with `SecretCipher`, stored via `store_secret`, then loaded and
decrypted by a `SecretService` built with the same key. Authorization (pipeline
scoping plus global secrets) and the per-job cache lifecycle are covered too.
"""

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

from snekql.sqlite import Database, insert, select
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.db import open_database
from danube.db.models import Pipeline, Secret, Team
from danube.security import (
    KEY_SIZE,
    NONCE_SIZE,
    InvalidKeyError,
    SecretCipher,
    SecretService,
    generate_key,
    load_key,
    store_secret,
)
from danube.security.crypto import SecretDecryptError

KEY = b"0123456789abcdef0123456789abcdef"  # 32 bytes


async def _seed_pipelines(db: Database) -> None:
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        for pipeline_id in ("p1", "p2"):
            await tx.execute(
                insert(
                    Pipeline(
                        id=pipeline_id,
                        name=pipeline_id,
                        team_id="t1",
                        repo_url=f"https://example.test/{pipeline_id}.git",
                        worker_image=f"img:{pipeline_id}",
                    )
                )
            )


@fixture
async def db() -> AsyncGenerator[Database]:
    database = await open_database(":memory:")
    try:
        await _seed_pipelines(database)
        yield database
    finally:
        await database.close()


# --- crypto -----------------------------------------------------------------


@test(mark="fast")
def test_cipher_round_trips_value() -> None:
    cipher = SecretCipher(KEY)
    blob = cipher.encrypt("s3cr3t-value")
    assert_eq(cipher.decrypt(blob), "s3cr3t-value")


@test(mark="fast")
def test_blob_layout_is_nonce_plus_ciphertext_and_tag() -> None:
    cipher = SecretCipher(KEY)
    blob = cipher.encrypt("x")
    # 12-byte nonce + 1-byte ciphertext + 16-byte GCM tag.
    assert_eq(len(blob), NONCE_SIZE + 1 + 16)


@test(mark="fast")
def test_encrypt_uses_a_fresh_nonce_each_call() -> None:
    cipher = SecretCipher(KEY)
    assert cipher.encrypt("same") != cipher.encrypt("same")


@test(mark="fast")
def test_tampered_blob_fails_authentication() -> None:
    cipher = SecretCipher(KEY)
    blob = bytearray(cipher.encrypt("value"))
    blob[-1] ^= 0x01  # flip a tag bit
    with assert_raises(SecretDecryptError):
        cipher.decrypt(bytes(blob))


@test(mark="fast")
def test_decrypt_with_wrong_key_fails() -> None:
    blob = SecretCipher(KEY).encrypt("value")
    with assert_raises(SecretDecryptError):
        SecretCipher(generate_key()).decrypt(blob)


@test(mark="fast")
def test_short_blob_is_rejected() -> None:
    with assert_raises(SecretDecryptError):
        SecretCipher(KEY).decrypt(b"tooshort")


@test(mark="fast")
def test_wrong_key_size_is_rejected() -> None:
    with assert_raises(InvalidKeyError):
        _ = SecretCipher(b"too-short")


@test(mark="fast")
def test_generate_key_returns_aes256_key() -> None:
    assert_eq(len(generate_key()), KEY_SIZE)


@test(mark="fast")
def test_load_key_reads_a_valid_key() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        good = Path(raw_dir) / "good.key"
        good.write_bytes(KEY)
        assert_eq(load_key(good), KEY)


@test(mark="fast")
def test_load_key_rejects_wrong_length() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        bad = Path(raw_dir) / "bad.key"
        bad.write_bytes(b"short")
        with assert_raises(InvalidKeyError):
            _ = load_key(bad)


@test(mark="fast")
def test_load_key_rejects_missing_file() -> None:
    with tempfile.TemporaryDirectory() as raw_dir, assert_raises(InvalidKeyError):
        _ = load_key(Path(raw_dir) / "missing.key")


# --- store + service --------------------------------------------------------


@test(mark="fast")
async def test_store_then_load_round_trips_through_db() -> None:
    database = await load_fixture(db())
    cipher = SecretCipher(KEY)
    _ = await store_secret(
        database, cipher, key="DEPLOY_TOKEN", value="tok-123", pipeline_id="p1"
    )

    # Stored ciphertext must not equal the plaintext.
    async with database.transaction() as tx:
        row = await tx.fetch_one(select(Secret).where(Secret.key.eq("DEPLOY_TOKEN")))
    assert row.value_encrypted != b"tok-123"

    service = SecretService(database, cipher)
    await service.load_for_job("j1", "p1")
    assert_eq(service.get("j1", "DEPLOY_TOKEN"), "tok-123")


@test(mark="fast")
async def test_store_secret_upserts_existing_key() -> None:
    database = await load_fixture(db())
    cipher = SecretCipher(KEY)
    first = await store_secret(
        database, cipher, key="API_KEY", value="v1", pipeline_id="p1"
    )
    second = await store_secret(
        database, cipher, key="API_KEY", value="v2", pipeline_id="p1"
    )
    assert_eq(first, second)  # same row, updated in place

    service = SecretService(database, cipher)
    await service.load_for_job("j1", "p1")
    assert_eq(service.get("j1", "API_KEY"), "v2")


@test(mark="fast")
async def test_global_secret_is_authorized_for_every_pipeline() -> None:
    database = await load_fixture(db())
    cipher = SecretCipher(KEY)
    _ = await store_secret(
        database, cipher, key="GLOBAL", value="glob", pipeline_id=None
    )

    service = SecretService(database, cipher)
    await service.load_for_job("j1", "p1")
    assert_eq(service.get("j1", "GLOBAL"), "glob")


@test(mark="fast")
async def test_unauthorized_pipeline_secret_is_not_loaded() -> None:
    database = await load_fixture(db())
    cipher = SecretCipher(KEY)
    _ = await store_secret(
        database, cipher, key="OTHER", value="nope", pipeline_id="p2"
    )

    service = SecretService(database, cipher)
    await service.load_for_job("j1", "p1")
    # p1 may not read a p2-scoped secret: it is absent from the cache.
    assert service.get("j1", "OTHER") is None


@test(mark="fast")
async def test_unknown_key_returns_none() -> None:
    database = await load_fixture(db())
    service = SecretService(database, SecretCipher(KEY))
    await service.load_for_job("j1", "p1")
    assert service.get("j1", "NOPE") is None


@test(mark="fast")
async def test_cache_is_per_job_and_cleared_on_job_end() -> None:
    database = await load_fixture(db())
    cipher = SecretCipher(KEY)
    _ = await store_secret(
        database, cipher, key="DEPLOY_TOKEN", value="tok-123", pipeline_id="p1"
    )

    service = SecretService(database, cipher)
    await service.load_for_job("j1", "p1")
    await service.load_for_job("j2", "p1")

    # Clearing one job leaves the other intact.
    service.clear_job("j1")
    assert service.get("j1", "DEPLOY_TOKEN") is None
    assert_eq(service.get("j2", "DEPLOY_TOKEN"), "tok-123")

    service.clear_job("j2")
    assert service.get("j2", "DEPLOY_TOKEN") is None
    # Idempotent: clearing an absent job is a no-op.
    service.clear_job("j2")


@test(mark="fast")
async def test_active_values_lists_only_the_job_secrets() -> None:
    database = await load_fixture(db())
    cipher = SecretCipher(KEY)
    _ = await store_secret(database, cipher, key="A", value="aaa", pipeline_id="p1")
    _ = await store_secret(database, cipher, key="B", value="bbb", pipeline_id="p2")

    service = SecretService(database, cipher)
    await service.load_for_job("j1", "p1")
    await service.load_for_job("j2", "p2")

    assert_eq(sorted(service.active_values("j1")), ["aaa"])
    assert_eq(sorted(service.active_values("j2")), ["bbb"])
