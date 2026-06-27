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
_IDEMPOTENCY_KEYS: dict[str, str] = {}


class PaymentError(Exception):
    pass


def charge_card(amount_cents: int, currency: str = "usd", idempotency_key: str | None = None) -> Charge:
    if amount_cents <= 0:
        raise PaymentError("amount must be positive")

    if idempotency_key is not None:
        existing_charge_id = _IDEMPOTENCY_KEYS.get(idempotency_key)
        if existing_charge_id is not None:
            existing_charge = _CHARGES.get(existing_charge_id)
            if existing_charge is not None:
                if existing_charge.amount_cents != amount_cents or existing_charge.currency != currency:
                    raise PaymentError(
                        f"idempotency key {idempotency_key} was used with different parameters"
                    )
                return existing_charge

    charge = Charge(
        id=f"ch_{secrets.token_hex(8)}",
        amount_cents=amount_cents,
        currency=currency,
        captured=True,
    )
    _CHARGES[charge.id] = charge
    if idempotency_key is not None:
        _IDEMPOTENCY_KEYS[idempotency_key] = charge.id

    return charge


def refund(charge_id: str, amount_cents: int | None = None) -> Charge:
    charge = _CHARGES.get(charge_id)
    if charge is None:
        raise PaymentError("charge not found")
    if amount_cents is None:
        amount_cents = charge.amount_cents
    if amount_cents > charge.amount_cents - charge.refunded_cents:
        raise PaymentError("refund amount exceeds available")
    charge.refunded_cents += amount_cents
    return charge

def refund(charge_id: str, amount_cents: int | None = None) -> Charge:
    charge = _CHARGES.get(charge_id)
    if charge is None:
        raise PaymentError("unknown charge")
    amount = amount_cents if amount_cents is not None else charge.amount_cents
    charge.refunded_cents += amount
    return charge
