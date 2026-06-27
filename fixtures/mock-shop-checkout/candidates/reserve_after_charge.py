"""Checkout flow that reserves stock only after the charge succeeds.

Validates availability up front WITHOUT touching stock (raising CheckoutError on
an unknown product or insufficient stock), then charges the card, and only after a
confirmed charge does it decrement stock and create the order. Because stock is
never touched until payment is captured, a failed charge can never leave a
dangling reservation or a phantom order. Public API is unchanged.
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

    # Validate availability WITHOUT mutating stock, so a later payment failure
    # cannot leave anything reserved.
    for item in items:
        product = db.get_product(item.product_id)
        if product is None:
            raise CheckoutError(f"unknown product {item.product_id}")
        if product.stock < item.quantity:
            raise CheckoutError(f"insufficient stock for {product.name}")

    total = cart_total_cents(items)

    # Charge first. If this raises, no stock was decremented and no order exists.
    charge = payments.charge_card(total)

    # Payment confirmed: now it is safe to commit the stock changes + order.
    for item in items:
        product = db.get_product(item.product_id)
        product.stock -= item.quantity

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
