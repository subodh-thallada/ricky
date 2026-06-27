"""Checkout flow.

Deliberately flawed candidate: it decrements (reserves) stock, then charges, with
NO rollback if the charge fails. After a declined payment the reserved stock stays
gone -- a leaked reservation -- even though no order is created. Proves the fixture
catches a missing stock-rollback implementation (this candidate should FAIL the
feature test while still passing every regression test).
"""
from __future__ import annotations

from . import db, payments
from .models import CartItem, Order, OrderStatus


class CheckoutError(Exception):
    pass


def cart_total_cents(items: list[CartItem]) -> int:
    total = 0
    for item in items:
        product = db.get_product(item.product_id)
        if product is None:
            raise CheckoutError(f"unknown product {item.product_id}")
        total += product.price_cents * item.quantity
    return total


def checkout(user_id: int, items: list[CartItem]) -> Order:
    if not items:
        raise CheckoutError("cart is empty")

    # Reserve stock (no locking, no rollback if payment fails later).
    for item in items:
        product = db.get_product(item.product_id)
        if product is None:
            raise CheckoutError(f"unknown product {item.product_id}")
        if product.stock < item.quantity:
            raise CheckoutError(f"insufficient stock for {product.name}")
        product.stock -= item.quantity

    total = cart_total_cents(items)
    charge = payments.charge_card(total)

    order = Order(
        id=db.next_order_id(),
        user_id=user_id,
        items=list(items),
        status=OrderStatus.PAID,
        total_cents=total,
        payment_id=charge.id,
    )
    db.ORDERS[order.id] = order
    return order
