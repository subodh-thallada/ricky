"""Mock payment gateway with idempotent charges.

Adds an optional `idempotency_key` to `charge_card`: the first charge for a key is
created and remembered in a key->Charge registry; any later call with the SAME key
returns that same Charge instead of charging the card again. Calls with no key (or a
brand-new key) are independent, exactly like the baseline. Public API is otherwise
unchanged so the rest of the mock-shop package keeps working.
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
# idempotency_key -> the Charge minted for that key (dedupes double-charges)
_IDEMPOTENT_CHARGES: dict[str, Charge] = {}


class PaymentError(Exception):
    pass


def charge_card(
    amount_cents: int,
    currency: str = "usd",
    idempotency_key: str | None = None,
) -> Charge:
    if amount_cents <= 0:
        raise PaymentError("amount must be positive")

    if idempotency_key is not None and idempotency_key in _IDEMPOTENT_CHARGES:
        # Same key already charged: return the original Charge, do not double-charge.
        return _IDEMPOTENT_CHARGES[idempotency_key]

    charge = Charge(
        id=f"ch_{secrets.token_hex(8)}",
        amount_cents=amount_cents,
        currency=currency,
        captured=True,
    )
    _CHARGES[charge.id] = charge
    if idempotency_key is not None:
        _IDEMPOTENT_CHARGES[idempotency_key] = charge
    return charge


def refund(charge_id: str, amount_cents: int | None = None) -> Charge:
    charge = _CHARGES.get(charge_id)
    if charge is None:
        raise PaymentError("unknown charge")
    amount = amount_cents if amount_cents is not None else charge.amount_cents
    charge.refunded_cents += amount
    return charge
