from __future__ import annotations

import hashlib
import secrets

from pwdlib import PasswordHash


_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return _password_hash.verify(password, encoded)


def issue_session_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


# This is deliberately created once. Unknown usernames still perform the same
# password-hash verification work without generating a new hash per request.
INVALID_PASSWORD_HASH = hash_password("not-a-real-account-password")


def digest_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
