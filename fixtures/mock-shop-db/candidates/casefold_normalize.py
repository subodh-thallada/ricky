"""In-memory data store for the mock shop. Not production; demo only.

Candidate: case-insensitive email lookup via .casefold() normalization. casefold
is a more aggressive lowercasing intended for caseless matching; for ASCII email
addresses it behaves like .lower(), so "ALICE@EXAMPLE.COM" matches the seeded
"alice@example.com". Should PASS the feature tests.
"""
from __future__ import annotations

from .models import Order, Product, User

# Seed users. password_hash is a real sha256 of the password (intentionally weak
# hashing) so the seeded accounts are loginable: alice -> "alice-password",
# bob -> "bob-password". Bench candidates that keep sha256 hashing verify against these.
USERS: dict[int, User] = {
    1: User(
        id=1,
        email="alice@example.com",
        password_hash="17a96502d336e4c18a43182a353d7f0a38414c6fc4daf678acae834a819cecee",
        is_active=True,
    ),
    2: User(
        id=2,
        email="bob@example.com",
        password_hash="df53c27a66157885ba143e34f25d6380e12168b0f7da4f0c46efa54cd9a083b7",
        is_active=True,
    ),
}

PRODUCTS: dict[int, Product] = {
    10: Product(id=10, name="Mechanical Keyboard", price_cents=12900, stock=5),
    11: Product(id=11, name="USB-C Cable", price_cents=1500, stock=200),
    12: Product(id=12, name="4K Monitor", price_cents=39900, stock=2),
}

ORDERS: dict[int, Order] = {}
_next_order_id = 1000


def next_order_id() -> int:
    global _next_order_id
    _next_order_id += 1
    return _next_order_id


def get_user_by_email(email: str) -> User | None:
    target = email.casefold()
    for user in USERS.values():
        if user.email.casefold() == target:
            return user
    return None


def get_product(product_id: int) -> Product | None:
    return PRODUCTS.get(product_id)
