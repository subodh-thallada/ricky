"""Domain models for the mock shop.

Adds domain-integrity validation in __post_init__: a Product cannot have a
negative price or negative stock, and a CartItem must have a positive quantity.
The public shape (OrderStatus enum + User/Product/CartItem/Order dataclasses,
with the same field names, types, and defaults) is unchanged so the rest of the
mock-shop package keeps importing and constructing these types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
        if self.price_cents < 0:
            raise ValueError("price_cents must not be negative")
        if self.stock < 0:
            raise ValueError("stock must not be negative")


@dataclass
class CartItem:
    product_id: int
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass
class Order:
    id: int
    user_id: int
    items: list[CartItem] = field(default_factory=list)
    status: OrderStatus = OrderStatus.PENDING
    total_cents: int = 0
    payment_id: str | None = None
