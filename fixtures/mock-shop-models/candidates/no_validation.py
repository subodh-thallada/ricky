"""Domain models for the mock shop.

Plain dataclasses with no domain-integrity validation: negative Product price or
stock and non-positive CartItem quantity are silently accepted. Public shape
(OrderStatus enum + User/Product/CartItem/Order dataclasses, same field names,
types, and defaults) matches the rest of the package.
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


@dataclass
class CartItem:
    product_id: int
    quantity: int


@dataclass
class Order:
    id: int
    user_id: int
    items: list[CartItem] = field(default_factory=list)
    status: OrderStatus = OrderStatus.PENDING
    total_cents: int = 0
    payment_id: str | None = None
