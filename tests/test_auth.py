"""Tests for access-token verification.

Supabase projects sign access tokens either with an asymmetric key (current) or
a shared HS256 secret (legacy), so both paths are exercised. The algorithm
confusion test is the one that matters most: once a public key is published via
JWKS, accepting HS256 on that path would let anyone mint a valid token using the
published key as the HMAC secret.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import security
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_caches():
    get_settings.cache_clear()
    security._jwks_client.cache_clear()
    yield
    get_settings.cache_clear()
    security._jwks_client.cache_clear()


def make_token(key, alg, **overrides):
    claims = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "role": "authenticated",
        "email": "a@test.local",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm=alg)


# ---------------------------------------------------------------------------
# Legacy HS256 path
# ---------------------------------------------------------------------------

SECRET = "a-legacy-supabase-jwt-secret-of-sufficient-length"


def test_hs256_token_is_accepted_when_a_secret_is_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    principal = security.principal_from_token(make_token(SECRET, "HS256"))
    assert principal.profile_id == "11111111-1111-1111-1111-111111111111"
    assert principal.email == "a@test.local"


def test_hs256_token_signed_with_the_wrong_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(security.AuthError):
        security.decode_token(make_token("a-completely-different-secret-value", "HS256"))


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    stale = make_token(SECRET, "HS256", exp=int(time.time()) - 10)
    with pytest.raises(security.AuthError):
        security.decode_token(stale)


def test_token_for_another_audience_is_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(security.AuthError):
        security.decode_token(make_token(SECRET, "HS256", aud="some-other-service"))


def test_token_without_a_subject_is_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    claims = {"aud": "authenticated", "exp": int(time.time()) + 60}
    with pytest.raises(security.AuthError):
        security.decode_token(jwt.encode(claims, SECRET, algorithm="HS256"))


# ---------------------------------------------------------------------------
# Asymmetric JWKS path
# ---------------------------------------------------------------------------

class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJWKSClient:
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return FakeSigningKey(self._public_key)


@pytest.fixture
def es256_keys():
    private = ec.generate_private_key(ec.SECP256R1())
    return private, private.public_key()


def _use_jwks(monkeypatch, public_key, *, secret=None):
    if secret:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    else:
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(security, "_jwks_client", lambda url: FakeJWKSClient(public_key))


def test_es256_token_is_accepted_via_jwks(monkeypatch, es256_keys):
    private, public = es256_keys
    _use_jwks(monkeypatch, public)
    principal = security.principal_from_token(make_token(private, "ES256"))
    assert principal.profile_id == "11111111-1111-1111-1111-111111111111"


def test_es256_token_from_a_different_key_is_rejected(monkeypatch, es256_keys):
    _, public = es256_keys
    other = ec.generate_private_key(ec.SECP256R1())
    _use_jwks(monkeypatch, public)
    with pytest.raises(security.AuthError):
        security.decode_token(make_token(other, "ES256"))


def test_hs256_is_refused_on_the_jwks_path(monkeypatch, es256_keys):
    """Algorithm confusion: the public key must not double as an HMAC secret.

    JWKS publishes the verification key. If the JWKS path also accepted HS256,
    anyone could take that published key, sign a token with it as a shared
    secret, and be authenticated as any user they liked.
    """
    _, public = es256_keys
    _use_jwks(monkeypatch, public)

    import base64
    import hashlib
    import hmac
    import json as _json

    from cryptography.hazmat.primitives import serialization

    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # Built by hand rather than with jwt.encode: PyJWT refuses to *sign* HS256
    # with a PEM key, which is a guard on the attacker's side, not ours. An
    # attacker has no such scruples, so the token is assembled directly to prove
    # the decoder rejects it on its own merits.
    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(_json.dumps(
        {"sub": "attacker", "aud": "authenticated", "exp": int(time.time()) + 60}
    ).encode())
    signing_input = header + b"." + payload
    signature = b64(hmac.new(pem, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    with pytest.raises(security.AuthError):
        security.decode_token(forged)


def test_expired_es256_token_is_rejected(monkeypatch, es256_keys):
    private, public = es256_keys
    _use_jwks(monkeypatch, public)
    with pytest.raises(security.AuthError):
        security.decode_token(make_token(private, "ES256", exp=int(time.time()) - 5))


def test_a_jwks_fetch_failure_denies_rather_than_allows(monkeypatch):
    """If the key cannot be fetched the request must fail closed."""
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")

    class Exploding:
        def get_signing_key_from_jwt(self, _token):
            raise RuntimeError("network down")

    monkeypatch.setattr(security, "_jwks_client", lambda url: Exploding())
    with pytest.raises(security.AuthError):
        security.decode_token("whatever.the.token")


def test_garbage_is_rejected(monkeypatch, es256_keys):
    _, public = es256_keys
    _use_jwks(monkeypatch, public)
    for bad in ("", "not-a-token", "a.b.c", "Bearer x"):
        with pytest.raises(security.AuthError):
            security.decode_token(bad)


def test_bearer_header_parsing():
    assert security.bearer_token("Bearer abc123") == "abc123"
    assert security.bearer_token("bearer abc123") == "abc123"
    for bad in (None, "", "abc123", "Basic abc123", "Bearer "):
        with pytest.raises(security.AuthError):
            security.bearer_token(bad)


# ---------------------------------------------------------------------------
# Both key types at once, which is what a project mid-migration serves
# ---------------------------------------------------------------------------

def test_both_algorithms_work_when_both_are_configured(monkeypatch, es256_keys):
    private, public = es256_keys
    _use_jwks(monkeypatch, public, secret=SECRET)

    # An asymmetric token routes to JWKS...
    assert security.decode_token(make_token(private, "ES256"))["sub"]
    # ...and a legacy one to the shared secret, in the same process.
    assert security.decode_token(make_token(SECRET, "HS256"))["sub"]


def test_forged_hs256_still_refused_when_a_secret_is_configured(monkeypatch, es256_keys):
    """The public key must not be usable as an HMAC secret even now that HS256 is accepted."""
    import base64, hashlib, hmac, json as _json
    from cryptography.hazmat.primitives import serialization

    _, public = es256_keys
    _use_jwks(monkeypatch, public, secret=SECRET)

    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def b64(raw): return base64.urlsafe_b64encode(raw).rstrip(b"=")
    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(_json.dumps({"sub": "attacker", "aud": "authenticated",
                               "exp": int(time.time()) + 60}).encode())
    si = header + b"." + payload
    forged = (si + b"." + b64(hmac.new(pem, si, hashlib.sha256).digest())).decode()

    with pytest.raises(security.AuthError):
        security.decode_token(forged)


def test_alg_none_is_refused(monkeypatch, es256_keys):
    import base64, json as _json
    _, public = es256_keys
    _use_jwks(monkeypatch, public, secret=SECRET)

    def b64(raw): return base64.urlsafe_b64encode(raw).rstrip(b"=")
    header = b64(_json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = b64(_json.dumps({"sub": "attacker", "aud": "authenticated",
                               "exp": int(time.time()) + 60}).encode())
    with pytest.raises(security.AuthError):
        security.decode_token((header + b"." + payload + b".").decode())
