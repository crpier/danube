"""Unit tests for the dependency-free JWT verifier.

Covers the registered-claim checks the security doc requires (signature, expiry,
issuer, audience, subject) plus the algorithm-confusion / `none` downgrade
defences, across both the HS256 (embedded-IdP) and RS256 (OIDC) paths.
"""

import base64
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from snektest import assert_eq, assert_raises, test

from danube.auth.config import AuthConfig
from danube.auth.jwt import (
    Claims,
    ExpiredTokenError,
    InvalidClaimError,
    InvalidSignatureError,
    InvalidTokenError,
    encode,
    verify,
)

SECRET = "super-secret-signing-key"
ISSUER = "https://idp.test"
AUDIENCE = "danube"


def _hs256_config(leeway_seconds: int = 0) -> AuthConfig:
    return AuthConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithm="HS256",
        secret=SECRET,
        leeway_seconds=leeway_seconds,
    )


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sub": "subject-123",
        "email": "alice@example.com",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return payload


@test(mark="fast")
def test_valid_hs256_token_verifies() -> None:
    token = encode(_payload(), secret=SECRET)

    claims = verify(token, _hs256_config())

    assert isinstance(claims, Claims)
    assert_eq(claims.subject, "subject-123")
    assert_eq(claims.email, "alice@example.com")
    assert_eq(claims.issuer, ISSUER)


@test(mark="fast")
def test_expired_token_rejected() -> None:
    token = encode(_payload(exp=int(time.time()) - 10), secret=SECRET)

    with assert_raises(ExpiredTokenError):
        _ = verify(token, _hs256_config())


@test(mark="fast")
def test_expiry_leeway_allows_recent_expiry() -> None:
    token = encode(_payload(exp=int(time.time()) - 5), secret=SECRET)

    claims = verify(token, _hs256_config(leeway_seconds=30))

    assert_eq(claims.subject, "subject-123")


@test(mark="fast")
def test_wrong_signature_rejected() -> None:
    token = encode(_payload(), secret="a-different-secret")

    with assert_raises(InvalidSignatureError):
        _ = verify(token, _hs256_config())


@test(mark="fast")
def test_tampered_payload_rejected() -> None:
    token = encode(_payload(), secret=SECRET)
    header_b64, _, signature_b64 = token.split(".")
    forged_payload = (
        base64.urlsafe_b64encode(json.dumps(_payload(sub="attacker")).encode())
        .rstrip(b"=")
        .decode()
    )
    forged = f"{header_b64}.{forged_payload}.{signature_b64}"

    with assert_raises(InvalidSignatureError):
        _ = verify(forged, _hs256_config())


@test(mark="fast")
def test_wrong_issuer_rejected() -> None:
    token = encode(_payload(iss="https://evil.test"), secret=SECRET)

    with assert_raises(InvalidClaimError):
        _ = verify(token, _hs256_config())


@test(mark="fast")
def test_wrong_audience_rejected() -> None:
    token = encode(_payload(aud="someone-else"), secret=SECRET)

    with assert_raises(InvalidClaimError):
        _ = verify(token, _hs256_config())


@test(mark="fast")
def test_audience_list_containing_expected_is_accepted() -> None:
    token = encode(_payload(aud=["other", AUDIENCE]), secret=SECRET)

    claims = verify(token, _hs256_config())

    assert_eq(claims.subject, "subject-123")


@test(mark="fast")
def test_missing_subject_rejected() -> None:
    payload = _payload()
    del payload["sub"]
    token = encode(payload, secret=SECRET)

    with assert_raises(InvalidClaimError):
        _ = verify(token, _hs256_config())


@test(mark="fast")
def test_missing_expiry_rejected() -> None:
    payload = _payload()
    del payload["exp"]
    token = encode(payload, secret=SECRET)

    with assert_raises(InvalidClaimError):
        _ = verify(token, _hs256_config())


@test(mark="fast")
def test_alg_none_rejected() -> None:
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload = (
        base64.urlsafe_b64encode(json.dumps(_payload()).encode()).rstrip(b"=").decode()
    )
    token = f"{header}.{payload}."

    with assert_raises(InvalidTokenError):
        _ = verify(token, _hs256_config())


@test(mark="fast")
def test_algorithm_confusion_rejected() -> None:
    """An HS256 token is rejected when the appliance expects RS256."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    rs256_config = AuthConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithm="RS256",
        public_key_pem=public_pem,
    )
    # Attacker signs HS256 using the public key bytes as the HMAC secret.
    hmac_token = encode(_payload(), secret=public_pem)

    with assert_raises(InvalidTokenError):
        _ = verify(hmac_token, rs256_config)


@test(mark="fast")
def test_malformed_token_rejected() -> None:
    with assert_raises(InvalidTokenError):
        _ = verify("not-a-jwt", _hs256_config())


def _rs256_token(private_key: rsa.RSAPrivateKey, payload: dict[str, object]) -> str:
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signing_input = f"{header}.{body}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{header}.{body}.{sig_b64}"


@test(mark="fast")
def test_valid_rs256_token_verifies() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    config = AuthConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithm="RS256",
        public_key_pem=public_pem,
    )
    token = _rs256_token(private_key, _payload())

    claims = verify(token, config)

    assert_eq(claims.subject, "subject-123")


@test(mark="fast")
def test_rs256_wrong_key_rejected() -> None:
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        other_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    config = AuthConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithm="RS256",
        public_key_pem=public_pem,
    )
    token = _rs256_token(signing_key, _payload())

    with assert_raises(InvalidSignatureError):
        _ = verify(token, config)
