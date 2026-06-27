"""Authentication for the mock shop.

Deliberately flawed candidate: it *counts* failed logins but never enforces a
lockout, so the account is never blocked. Proves the fixture catches a missing
rate-limit / lockout implementation (this candidate should FAIL the feature tests
while still passing the regression tests).
"""
from __future__ import annotations

import hashlib
import secrets

from . import db
from .models import User

# token -> user_id
_SESSIONS: dict[str, int] = {}

MAX_FAILED = 5  # tracked but never acted on


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
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        if user is not None:
            user.failed_logins += 1  # counted, but nothing ever checks this
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
