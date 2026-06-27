"""Domain models for the mock shop.

Same domain-integrity guarantees as the reference, expressed differently: a
single module-level ``_require(cond, msg)`` guard is reused by each dataclass's
__post_init__ to reject negative Product price/stock and non-positive CartItem
quantity. Public shape (OrderStatus enum + User/Product/CartItem/Order
dataclasses, same field names, types, and defaults) is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


def _require(cond: bool, msg: str) -> None:
    """Raise ValueError(msg) when a domain invariant is violated."""
    if not cond:
        raise ValueError(msg)


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


@dataclass
class User:
    id: int
    email: str
    password_hash: str
    is_active: bool = True
    failed_logins: int = 0


@dataclass
class Product:
    id: int
    name: str
    price_cents: int
    stock: int

    def __post_init__(self) -> None:
        _require(self.price_cents >= 0, "price_cents must not be negative")
        _require(self.stock >= 0, "stock must not be negative")


@dataclass
class CartItem:
    product_id: int
    quantity: int

    def __post_init__(self) -> None:
        _require(self.quantity > 0, "quantity must be positive")


@dataclass
class Order:
    id: int
    user_id: int
    items: list[CartItem] = field(default_factory=list)
    status: OrderStatus = OrderStatus.PENDING
    total_cents: int = 0
    payment_id: str | None = None
