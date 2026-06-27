"""Mock payment gateway -- deliberately flawed candidate.

It accepts the `idempotency_key` parameter (so callers passing one don't hit a
TypeError) but completely ignores it: every call mints a brand-new charge with a
random id. Two calls with the same key therefore produce two different charges -- a
double-charge -- so this candidate FAILS the idempotency feature tests while still
passing every pre-existing regression (positive-amount check, charge fields, refunds).
"""
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


def charge_card(
    amount_cents: int,
    currency: str = "usd",
    idempotency_key: str | None = None,  # accepted but never used -> double charges
) -> Charge:
    if amount_cents <= 0:
        raise PaymentError("amount must be positive")
    # BUG: idempotency_key is ignored, so repeat calls are not deduplicated.
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
