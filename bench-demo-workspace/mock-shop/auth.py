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


"""Authentication for the mock shop.

Intentionally simple/weak in places so Bench has room to suggest improvements
(no rate limiting, sha256 password hashing, no token expiry).
"""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict
from typing import Dict, Optional

from . import db
from .models import User

# token -> user_id
_SESSIONS: dict[str, int] = {}

# Rate limiting settings (fixed window)
_MAX_ATTEMPTS_PER_WINDOW = 5
_WINDOW_SECONDS = 15 * 60  # 15 minutes
_COOLDOWN_SECONDS = 30 * 60  # 30 minutes cooldown after rate limit hit

# Account lockout settings
_LOCKOUT_THRESHOLD = 5  # failed logins before account lockout

# email -> attempt count and window start
_LOGIN_ATTEMPTS: Dict[str, dict] = defaultdict(lambda: {"count": 0, "window_start": 0})
# email -> cooldown expiry timestamp
_COOLDOWN_UNTIL: Dict[str, float] = {}


def _is_in_cooldown(email: str) -> bool:
    """Check if email is currently in cooldown period."""
    if email not in _COOLDOWN_UNTIL:
        return False
    if time.time() >= _COOLDOWN_UNTIL[email]:
        del _COOLDOWN_UNTIL[email]
        return False
    return True


def _check_and_increment_rate_limit(email: str) -> bool:
    """Check rate limit and increment attempt count. Returns True if limit exceeded."""
    now = time.time()
    attempts = _LOGIN_ATTEMPTS[email]
    
    # Reset window if expired
    if now - attempts["window_start"] >= _WINDOW_SECONDS:
        attempts["count"] = 0
        attempts["window_start"] = now
    
    attempts["count"] += 1
    
    if attempts["count"] > _MAX_ATTEMPTS_PER_WINDOW:
        _COOLDOWN_UNTIL[email] = now + _COOLDOWN_SECONDS
        return True
    return False


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
    if _is_in_cooldown(email):
        remaining = int(_COOLDOWN_UNTIL[email] - time.time())
        raise ValueError(f"too many attempts; try again in {remaining} seconds")
    
    user = db.get_user_by_email(email)
    if user is None or not user.is_active:
        raise ValueError("invalid credentials")
    
    if not verify_password(password, user.password_hash):
        # Check rate limit on failed attempt
        if _check_and_increment_rate_limit(email):
            raise ValueError("rate limit exceeded; please try again later")
        
        user.failed_logins += 1
        if user.failed_logins >= _LOCKOUT_THRESHOLD:
            user.is_active = False
        raise ValueError("invalid credentials")
    
    # Successful login - reset rate limit and failed logins
    _LOGIN_ATTEMPTS[email] = {"count": 0, "window_start": 0}
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
