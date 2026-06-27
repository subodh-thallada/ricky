"""Authentication for the mock shop.

Intentionally simple/weak in places so Bench has room to suggest improvements
(no rate limiting, sha256 password hashing, no token expiry).
"""
from __future__ import annotations

import hashlib
import secrets

from . import db
from .models import User

# token -> user_id
_SESSIONS: dict[str, int] = {}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash


def signup(email: str, password: str) -> User:
    if db.get_user_by_email(email) is not None:
        raise ValueError("email already registered")
    user_id = max(db.USERS) + 1 if db.USERS else 1
    user = User(id=user_id, email=email, password_hash=hash_password(password))
    db.USERS[user_id] = user
    return user


def login(email: str, password: str) -> str:
    user = db.get_user_by_email(email)
    if user is None or not user.is_active:
        raise ValueError("invalid credentials")
    if not verify_password(password, user.password_hash):
        user.failed_logins += 1
        raise ValueError("invalid credentials")
    user.failed_logins = 0
    token = secrets.token_hex(16)
    _SESSIONS[token] = user.id
    return token


def current_user(token: str) -> User | None:
    user_id = _SESSIONS.get(token)
    if user_id is None:
        return None
    return db.USERS.get(user_id)


def logout(token: str) -> None:
    _SESSIONS.pop(token, None)
