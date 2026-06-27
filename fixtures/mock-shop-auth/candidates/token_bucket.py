"""Authentication for the mock shop.

Adds login rate limiting + account lockout using a consecutive-failure counter
plus a hard cooldown timestamp per email. Public API is unchanged.
"""
from __future__ import annotations

import hashlib
import secrets
import time

from . import db
from .models import User

# token -> user_id
_SESSIONS: dict[str, int] = {}
# email -> monotonic time the lockout expires
_LOCKED_UNTIL: dict[str, float] = {}

MAX_FAILED = 5
LOCKOUT_SECONDS = 600.0


class AccountLockedError(ValueError):
    """Raised when an account is locked after too many consecutive failures."""


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
    now = time.monotonic()
    locked_until = _LOCKED_UNTIL.get(email)
    if locked_until is not None and now < locked_until:
        raise AccountLockedError("account locked, try again later")

    user = db.get_user_by_email(email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                _LOCKED_UNTIL[email] = now + LOCKOUT_SECONDS
        raise ValueError("invalid credentials")

    user.failed_logins = 0
    _LOCKED_UNTIL.pop(email, None)
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
