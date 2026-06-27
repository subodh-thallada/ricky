"""Behavior tests for a candidate mock_shop/payments.py.

The candidate REPLACES mock_shop/payments.py inside the real mock-shop package, then
runs against the rest of the package (models, db, checkout). Tests are behavior-level
and implementation-agnostic: they drive the public payments API
(charge_card / refund / Charge / PaymentError) and never reach into a candidate's
private idempotency registry, so a key->Charge dict, a deterministic charge id, or any
other correct dedupe design all pass.

Naming convention shared by every mock-shop fixture:
  test_feature_*     -> the requested feature (idempotency keys for charge_card)
  test_regression_*  -> pre-existing payments behavior that must keep working

State is reset per test by reloading the package modules, so module-level charge and
idempotency state from one test never leaks into the next.
"""
import importlib
import unittest

from mock_shop import db, models
from mock_shop import payments


def _reset_state():
    importlib.reload(models)
    importlib.reload(db)
    importlib.reload(payments)


class PaymentsBehaviorTest(unittest.TestCase):
    def setUp(self):
        _reset_state()

    # ---- feature: idempotency keys for charge_card ----

    def test_feature_same_key_returns_same_charge(self):
        c1 = payments.charge_card(500, idempotency_key="order-1")
        c2 = payments.charge_card(500, idempotency_key="order-1")
        # Same idempotency key must return the same charge -- no double charge.
        self.assertEqual(c1.id, c2.id)

    def test_feature_idempotent_no_duplicate_charge(self):
        c1 = payments.charge_card(500, idempotency_key="order-1")
        c2 = payments.charge_card(500, idempotency_key="order-1")
        # Two calls, one key -> exactly one charge (the same id comes back).
        self.assertEqual(c1.id, c2.id)
        # A different key is an independent charge with a different id.
        other = payments.charge_card(500, idempotency_key="order-2")
        self.assertNotEqual(c1.id, other.id)

    # ---- regression: pre-existing payments behavior must keep working ----

    def test_regression_no_key_charges_are_distinct(self):
        c1 = payments.charge_card(500)
        c2 = payments.charge_card(500)
        self.assertNotEqual(c1.id, c2.id)

    def test_regression_charge_amount_must_be_positive(self):
        with self.assertRaises(payments.PaymentError):
            payments.charge_card(0)
        with self.assertRaises(payments.PaymentError):
            payments.charge_card(-5)

    def test_regression_charge_fields(self):
        charge = payments.charge_card(1234, currency="eur")
        self.assertTrue(charge.captured)
        self.assertEqual(charge.amount_cents, 1234)
        self.assertEqual(charge.currency, "eur")
        self.assertTrue(charge.id.startswith("ch_"))

    def test_regression_refund_accounts(self):
        charge = payments.charge_card(900)
        before = charge.refunded_cents
        refunded = payments.refund(charge.id)
        self.assertEqual(refunded.id, charge.id)
        self.assertEqual(refunded.refunded_cents, before + 900)
        with self.assertRaises(payments.PaymentError):
            payments.refund("nope")


if __name__ == "__main__":
    unittest.main()
