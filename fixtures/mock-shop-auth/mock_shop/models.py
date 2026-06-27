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
