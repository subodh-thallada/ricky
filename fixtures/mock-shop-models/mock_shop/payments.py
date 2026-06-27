"""Mock payment gateway. Pretends to talk to a Stripe-like provider."""
from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass
class Charge:
    id: str
    amount_cents: int
    currency: str
    captured: bool
    refunded_cents: int = 0


_CHARGES: dict[str, Charge] = {}


class PaymentError(Exception):
    pass


def charge_card(amount_cents: int, currency: str = "usd") -> Charge:
    if amount_cents <= 0:
        raise PaymentError("amount must be positive")
    # No idempotency key, no retry, no real network call.
    charge = Charge(
        id=f"ch_{secrets.token_hex(8)}",
        amount_cents=amount_cents,
        currency=currency,
        captured=True,
    )
    _CHARGES[charge.id] = charge
    return charge


def refund(charge_id: str, amount_cents: int | None = None) -> Charge:
    charge = _CHARGES.get(charge_id)
    if charge is None:
        raise PaymentError("unknown charge")
    amount = amount_cents if amount_cents is not None else charge.amount_cents
    charge.refunded_cents += amount
    return charge
