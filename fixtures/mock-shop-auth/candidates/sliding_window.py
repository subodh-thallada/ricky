"""Authentication for the mock shop.

Adds login rate limiting + account lockout using a sliding window of recent
failed-attempt timestamps per email. Public API is unchanged so the rest of the
mock-shop package keeps working.
"""
from __future__ import annotations

import hashlib
import secrets
import time

from . import db
from .models import User

# token -> user_id
_SESSIONS: dict[str, int] = {}
# email -> recent failed-attempt timestamps (monotonic seconds)
_FAILED_ATTEMPTS: dict[str, list[float]] = {}

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300.0
LOCKOUT_SECONDS = 900.0


class AccountLockedError(ValueError):
    """Raised when an account is temporarily locked after repeated failures."""


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


def _recent_failures(email: str, now: float) -> list[float]:
    horizon = now - WINDOW_SECONDS
    recent = [ts for ts in _FAILED_ATTEMPTS.get(email, []) if ts >= horizon]
    _FAILED_ATTEMPTS[email] = recent
    return recent


def _is_locked(email: str, now: float) -> bool:
    recent = _recent_failures(email, now)
    if len(recent) < MAX_ATTEMPTS:
        return False
    return now - recent[-1] < LOCKOUT_SECONDS


def login(email: str, password: str) -> str:
    now = time.monotonic()
    if _is_locked(email, now):
        raise AccountLockedError("account temporarily locked due to repeated failures")

    user = db.get_user_by_email(email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        _FAILED_ATTEMPTS.setdefault(email, []).append(now)
        if user is not None:
            user.failed_logins += 1
        raise ValueError("invalid credentials")

    _FAILED_ATTEMPTS.pop(email, None)
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
