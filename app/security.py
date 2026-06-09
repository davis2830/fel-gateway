"""API key generation + hashing (argon2)."""
from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def generate_api_key() -> str:
    """Returns a high-entropy URL-safe key. Show once, store the hash."""
    return f"flg_{secrets.token_urlsafe(40)}"


def hash_api_key(key: str) -> str:
    return _hasher.hash(key)


def verify_api_key(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except Exception:
        return False
