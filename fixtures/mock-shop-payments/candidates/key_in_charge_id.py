"""Mock payment gateway with idempotent charges.

Alternative correct design: when an `idempotency_key` is supplied, the charge id is
derived deterministically from the key (`ch_<key>`) and stored in `_CHARGES`. A repeat
call with the same key recomputes the same id, finds the already-stored Charge, and
returns it -- so the card is never charged twice. Without a key, charges get a random
id and are independent, exactly like the baseline. Public API is otherwise unchanged
so the rest of the mock-shop package keeps working.
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
    idempotency_key: str | None = None,
) -> Charge:
    if amount_cents <= 0:
        raise PaymentError("amount must be positive")

    if idempotency_key is not None:
        # Deterministic id from the key -> the same key maps to the same stored Charge.
        charge_id = f"ch_{idempotency_key}"
        existing = _CHARGES.get(charge_id)
        if existing is not None:
            return existing
    else:
        charge_id = f"ch_{secrets.token_hex(8)}"

    charge = Charge(
        id=charge_id,
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
