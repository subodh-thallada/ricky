"""In-memory data store for the mock shop. Not production; demo only."""
from __future__ import annotations

from .models import Order, Product, User

# Seed users (password_hash is a plain sha256 of the password — intentionally weak).
USERS: dict[int, User] = {
    1: User(id=1, email="alice@example.com", password_hash="5e88489...", is_active=True),
    2: User(id=2, email="bob@example.com", password_hash="6cf6157...", is_active=True),
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
    for user in USERS.values():
        if user.email == email:
            return user
    return None


def get_product(product_id: int) -> Product | None:
    return PRODUCTS.get(product_id)
