"""Behavior tests for a candidate mock_shop/models.py.

The candidate REPLACES mock_shop/models.py inside the real mock-shop package,
then runs against the rest of the package (db, auth, payments, checkout), which
all import these domain types. Tests are behavior-level and
implementation-agnostic: they construct the public dataclasses and assert on the
domain invariants, never on how validation is wired (a __post_init__ check, a
shared guard helper, or a private _validate method all pass).

Naming convention shared by every mock-shop fixture:
  test_feature_*     -> the requested feature (domain field validation)
  test_regression_*  -> pre-existing model behavior that must keep working

State is reset per test by reloading the package modules (models first, then db
so db rebinds against the freshly loaded model classes), so the candidate's
classes and the seeded data never go stale between tests.
"""
import importlib
import unittest

from mock_shop import db, models


def _reset_state():
    importlib.reload(models)
    importlib.reload(db)


class ModelsBehaviorTest(unittest.TestCase):
    def setUp(self):
        _reset_state()

    # ---- feature: domain field validation ----

    def test_feature_product_rejects_negative_price(self):
        with self.assertRaises(ValueError):
            models.Product(id=1, name="x", price_cents=-1, stock=5)

    def test_feature_product_rejects_negative_stock(self):
        with self.assertRaises(ValueError):
            models.Product(id=1, name="x", price_cents=100, stock=-1)

    def test_feature_cartitem_rejects_nonpositive_quantity(self):
        with self.assertRaises(ValueError):
            models.CartItem(product_id=10, quantity=0)
        with self.assertRaises(ValueError):
            models.CartItem(product_id=10, quantity=-2)

    # ---- regression: pre-existing model behavior must keep working ----

    def test_regression_valid_objects_construct(self):
        product = models.Product(id=10, name="Keyboard", price_cents=12900, stock=5)
        self.assertEqual(product.price_cents, 12900)
        self.assertEqual(product.stock, 5)

        user = models.User(id=1, email="alice@example.com", password_hash="h")
        self.assertEqual(user.email, "alice@example.com")

        item = models.CartItem(product_id=10, quantity=1)
        self.assertEqual(item.quantity, 1)

        order = models.Order(id=1000, user_id=1)
        self.assertEqual(order.status, models.OrderStatus.PENDING)
        self.assertEqual(order.total_cents, 0)
        self.assertEqual(order.items, [])

    def test_regression_orderstatus_values(self):
        self.assertEqual(models.OrderStatus.PAID, "paid")
        self.assertEqual(models.OrderStatus.PENDING, "pending")

    def test_regression_user_defaults(self):
        user = models.User(id=9, email="x@y.z", password_hash="h")
        self.assertTrue(user.is_active)
        self.assertEqual(user.failed_logins, 0)

    def test_regression_package_still_imports(self):
        importlib.reload(db)
        self.assertEqual(db.USERS[1].email, "alice@example.com")


if __name__ == "__main__":
    unittest.main()
