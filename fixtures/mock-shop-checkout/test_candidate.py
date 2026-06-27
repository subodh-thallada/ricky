"""Behavior tests for a candidate mock_shop/checkout.py.

The candidate REPLACES mock_shop/checkout.py inside the real mock-shop package,
then runs against the rest of the package (db, models, payments, auth). Tests are
behavior-level and implementation-agnostic: they drive the public checkout API
(cart_total_cents / checkout) and assert on observable side effects -- stock
levels, db.ORDERS, the returned Order -- never on a candidate's private
reservation/rollback helpers, so reserve-then-rollback, validate-then-charge, or
any other correct design all pass.

Naming convention shared by every mock-shop fixture:
  test_feature_*     -> the requested feature (roll back reserved stock when payment fails)
  test_regression_*  -> pre-existing checkout behavior that must keep working

State is reset per test by reloading the package modules in dependency order, so
seeded stock, charges, and orders from one test never leak into the next.
"""
import importlib
import unittest
from unittest import mock

from mock_shop import db, models, payments
from mock_shop import checkout


def _reset_state():
    importlib.reload(models)
    importlib.reload(db)
    importlib.reload(payments)
    importlib.reload(checkout)


class CheckoutBehaviorTest(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def _cart(self, *pairs):
        return [models.CartItem(product_id=pid, quantity=qty) for pid, qty in pairs]

    def _decline_charge(self):
        # Patch the module-qualified gateway so any candidate that calls
        # payments.charge_card(...) sees a declined payment.
        return mock.patch(
            "mock_shop.payments.charge_card",
            side_effect=payments.PaymentError("declined"),
        )

    # ---- regression: pre-existing checkout behavior must keep working ----

    def test_regression_cart_total_cents(self):
        items = self._cart((10, 2), (11, 3))
        expected = db.PRODUCTS[10].price_cents * 2 + db.PRODUCTS[11].price_cents * 3
        self.assertEqual(checkout.cart_total_cents(items), expected)

    def test_regression_empty_cart_raises(self):
        with self.assertRaises(checkout.CheckoutError):
            checkout.checkout(1, [])

    def test_regression_unknown_product_raises(self):
        with self.assertRaises(checkout.CheckoutError):
            checkout.checkout(1, self._cart((999, 1)))

    def test_regression_insufficient_stock_raises(self):
        # product 12 is seeded with stock=2, so quantity 3 must be rejected.
        with self.assertRaises(checkout.CheckoutError):
            checkout.checkout(1, self._cart((12, 3)))

    def test_regression_happy_path_order(self):
        qty = 2
        price = db.PRODUCTS[10].price_cents
        stock_before = db.PRODUCTS[10].stock

        order = checkout.checkout(1, self._cart((10, qty)))

        self.assertEqual(order.status, models.OrderStatus.PAID)
        self.assertTrue(order.payment_id, "a successful order should carry a payment id")
        self.assertEqual(order.total_cents, price * qty)
        self.assertEqual(db.PRODUCTS[10].stock, stock_before - qty)
        self.assertIn(order.id, db.ORDERS)
        self.assertIs(db.ORDERS[order.id], order)

    # ---- feature: roll back reserved stock when payment fails ----

    def test_feature_stock_restored_when_charge_fails(self):
        items = self._cart((10, 2), (11, 5))
        stock_before = {pid: product.stock for pid, product in db.PRODUCTS.items()}

        with self._decline_charge():
            with self.assertRaises(Exception):
                checkout.checkout(1, items)

        for pid, product in db.PRODUCTS.items():
            self.assertEqual(
                product.stock,
                stock_before[pid],
                f"stock for product {pid} must be restored after a failed charge",
            )
        self.assertEqual(len(db.ORDERS), 0, "no order should be created when payment fails")

    def test_feature_no_order_on_payment_failure(self):
        items = self._cart((12, 1))

        with self._decline_charge():
            with self.assertRaises(Exception):
                checkout.checkout(1, items)

        self.assertEqual(len(db.ORDERS), 0, "no order row should be created on payment failure")
        paid = [o for o in db.ORDERS.values() if o.status == models.OrderStatus.PAID]
        self.assertEqual(paid, [], "no PAID order should exist after a failed charge")


if __name__ == "__main__":
    unittest.main()
