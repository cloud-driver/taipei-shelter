"""Password hashing, token digests and HTTP hardening shared by the API."""

import hashlib
import hmac
import json
import secrets

from fastapi import Request

# scrypt cost parameters (RFC 7914 interactive-login recommendation).
SCRYPT_PARAMS = {'n': 16384, 'r': 8, 'p': 1}

SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'X-Frame-Options': 'DENY',
    'Permissions-Policy': 'geolocation=(self), camera=(), microphone=()',
    'Content-Security-Policy': '; '.join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data: blob: https://tile.openstreetmap.org",
            "connect-src 'self'",
            "worker-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "frame-ancestors 'none'",
            "form-action 'self'",
        ]
    ),
}


def digest(value: str) -> str:
    """SHA-256 hex digest; used so the database never stores raw session tokens."""
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str, salt: str | None = None) -> str:
    """Return ``salt:hash`` using salted scrypt."""
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **SCRYPT_PARAMS).hex()
    return salt + ':' + value


def verify_password(password: str, stored: str) -> bool:
    """Constant-time password check; a malformed stored hash never matches."""
    try:
        candidate = password_hash(password, stored.split(':', 1)[0])
    except ValueError:
        return False
    return constant_time_equal(candidate, stored)


def constant_time_equal(a: str, b: str) -> bool:
    """Compare secrets without leaking timing.

    ``hmac.compare_digest`` raises ``TypeError`` for non-ASCII ``str`` input, and HTTP header
    values may contain arbitrary Latin-1 characters, so compare UTF-8 bytes instead.
    """
    return hmac.compare_digest(a.encode(), b.encode())


def hash_request(model) -> str:
    """Stable fingerprint of a pydantic request body, used for idempotent retries."""
    return digest(json.dumps(model.model_dump(), sort_keys=True, separators=(',', ':')))


def client_ip(request: Request) -> str:
    """Best-effort client address for rate limiting (``request.client`` may be ``None``)."""
    return request.client.host if request.client else 'unknown'
