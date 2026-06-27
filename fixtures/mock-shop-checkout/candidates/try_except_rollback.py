"""Checkout flow with stock rollback on payment failure.

Reserves (decrements) stock up front, remembering exactly how much was taken from
each product, then charges inside a try/except. If the charge raises for any
reason the reservation is rolled back -- every decremented product's stock is
restored -- before the error propagates, so a failed payment never leaks a
reservation and never creates an order. Public API is unchanged so the rest of
the mock-shop package keeps working.
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

    # Reserve stock up front, recording (product_id, quantity) for each
    # decrement so we can put it back if payment fails later.
    reserved: list[tuple[int, int]] = []
    for item in items:
        product = db.get_product(item.product_id)
        if product is None:
            raise CheckoutError(f"unknown product {item.product_id}")
        if product.stock < item.quantity:
            raise CheckoutError(f"insufficient stock for {product.name}")
        product.stock -= item.quantity
        reserved.append((item.product_id, item.quantity))

    total = cart_total_cents(items)
    try:
        charge = payments.charge_card(total)
    except Exception:
        # Payment failed: restore every reserved unit, then re-raise so the
        # caller still sees the failure (no order is created).
        for product_id, quantity in reserved:
            product = db.get_product(product_id)
            if product is not None:
                product.stock += quantity
        raise

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
