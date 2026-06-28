"""Minimal, dependency-free JWT verification for the Danube UI/API.

Danube ships no third-party JWT library, so this module implements just the
slice the appliance needs: compact-serialization JWS verification for ``HS256``
(HMAC-SHA256, the embedded-IdP / symmetric path) and ``RS256`` (RSA PKCS#1 v1.5
over SHA-256, the OIDC asymmetric path), plus the registered-claim checks the
security doc requires (``exp``, ``iss``, ``aud``, ``sub``).

Security notes:

- The algorithm is pinned to the configured one; a token whose header ``alg``
  disagrees is rejected. This defeats the classic *algorithm-confusion* attack
  (an attacker re-signing an RS256 token as HS256 using the public key as the
  HMAC secret) and the ``alg: "none"`` downgrade.
- HMAC comparison is constant-time (`hmac.compare_digest`).

`encode` exists only for the embedded-IdP HS256 path (and for tests to mint
tokens); verification is the module's real job.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

if TYPE_CHECKING:
    from danube.auth.config import AuthConfig


class JwtError(Exception):
    """Base class for every token-rejection reason; all map to HTTP 401."""


class InvalidTokenError(JwtError):
    """The token is malformed, unsigned, or uses an unexpected algorithm."""


class InvalidSignatureError(JwtError):
    """The signature does not verify against the configured key."""


class ExpiredTokenError(JwtError):
    """The token's ``exp`` claim is in the past."""


class InvalidClaimError(JwtError):
    """A registered claim (``iss``, ``aud``, ``sub``, ``exp``) is missing or wrong."""


@dataclass(frozen=True, slots=True)
class Claims:
    """The validated claims a verified token carries."""

    subject: str
    email: str | None
    issuer: str | None
    expires_at: int | None
    raw: dict[str, object]


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding_needed = -len(segment) % 4
    try:
        # binascii.Error (raised on malformed input) is a subclass of ValueError.
        return base64.urlsafe_b64decode(segment + "=" * padding_needed)
    except ValueError as error:
        msg = "token contains invalid base64url"
        raise InvalidTokenError(msg) from error


def _decode_json_segment(segment: str, label: str) -> dict[str, object]:
    try:
        decoded = json.loads(_b64url_decode(segment))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        msg = f"token {label} is not valid JSON"
        raise InvalidTokenError(msg) from error
    if not isinstance(decoded, dict):
        msg = f"token {label} is not a JSON object"
        raise InvalidTokenError(msg)
    return cast("dict[str, object]", decoded)


def encode(payload: dict[str, object], *, secret: str) -> str:
    """Sign ``payload`` as a compact HS256 JWT with ``secret``.

    Only HS256 is supported here -- this is the embedded-IdP signing path; an
    external OIDC provider mints its own tokens.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def verify(token: str, config: AuthConfig) -> Claims:
    """Verify ``token`` against ``config`` and return its validated `Claims`.

    Raises a `JwtError` subclass for every rejection reason: malformed token,
    wrong/`none` algorithm, bad signature, expired, or a failed issuer/audience/
    subject check.
    """
    parts = token.split(".")
    if len(parts) != 3:  # noqa: PLR2004 - a JWS compact token is exactly header.payload.signature
        msg = "token is not a well-formed JWT"
        raise InvalidTokenError(msg)
    header_b64, payload_b64, signature_b64 = parts

    header = _decode_json_segment(header_b64, "header")
    alg = header.get("alg")
    if alg == "none":
        msg = "unsigned tokens are not accepted"
        raise InvalidTokenError(msg)
    if alg != config.algorithm:
        msg = f"unexpected token algorithm {alg!r}; expected {config.algorithm!r}"
        raise InvalidTokenError(msg)

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = _b64url_decode(signature_b64)
    _verify_signature(config, signing_input, signature)

    payload = _decode_json_segment(payload_b64, "payload")
    _check_claims(payload, config)
    return _build_claims(payload)


def _verify_signature(
    config: AuthConfig, signing_input: bytes, signature: bytes
) -> None:
    if config.algorithm == "HS256":
        secret = config.secret or ""
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            msg = "token signature does not verify"
            raise InvalidSignatureError(msg)
        return
    public_key = _load_rsa_public_key(config)
    try:
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as error:
        msg = "token signature does not verify"
        raise InvalidSignatureError(msg) from error


def _load_rsa_public_key(config: AuthConfig) -> rsa.RSAPublicKey:
    pem = config.public_key_pem or ""
    key = serialization.load_pem_public_key(pem.encode())
    if not isinstance(key, rsa.RSAPublicKey):
        msg = "configured RS256 public key is not an RSA key"
        raise InvalidTokenError(msg)
    return key


def _check_claims(payload: dict[str, object], config: AuthConfig) -> None:
    _check_expiry(payload, config)
    issuer = payload.get("iss")
    if issuer != config.issuer:
        msg = f"token issuer {issuer!r} does not match {config.issuer!r}"
        raise InvalidClaimError(msg)
    if not _audience_matches(payload.get("aud"), config.audience):
        msg = f"token audience does not include {config.audience!r}"
        raise InvalidClaimError(msg)
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        msg = "token is missing a subject claim"
        raise InvalidClaimError(msg)


def _check_expiry(payload: dict[str, object], config: AuthConfig) -> None:
    expires_at = payload.get("exp")
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
        msg = "token is missing a valid expiry claim"
        raise InvalidClaimError(msg)
    if time.time() > expires_at + config.leeway_seconds:
        msg = "token has expired"
        raise ExpiredTokenError(msg)


def _audience_matches(audience: object, expected: str) -> bool:
    if isinstance(audience, str):
        return audience == expected
    if isinstance(audience, list):
        return expected in cast("list[object]", audience)
    return False


def _build_claims(payload: dict[str, object]) -> Claims:
    subject = payload["sub"]
    email = payload.get("email")
    issuer = payload.get("iss")
    expires_at = payload.get("exp")
    return Claims(
        subject=cast("str", subject),
        email=email if isinstance(email, str) else None,
        issuer=issuer if isinstance(issuer, str) else None,
        expires_at=int(expires_at) if isinstance(expires_at, (int, float)) else None,
        raw=payload,
    )
