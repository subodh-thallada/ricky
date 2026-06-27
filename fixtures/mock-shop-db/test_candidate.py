"""Behavior tests for a candidate mock_shop/db.py.

The candidate REPLACES mock_shop/db.py inside the real mock-shop package, then
runs against the rest of the package (models, auth, payments, checkout). Tests are
behavior-level and implementation-agnostic: they drive the public db API
(get_user_by_email / get_product / next_order_id) and never reach into a
candidate's private normalization helpers, so .lower(), .casefold(), or any other
correct case-insensitive design all pass.

Naming convention shared by every mock-shop fixture:
  test_feature_*     -> the requested feature (case-insensitive email lookup)
  test_regression_*  -> pre-existing db behavior that must keep working

State is reset per test by reloading the package modules, so the seeded USERS /
PRODUCTS dicts and the module-level order counter never leak between tests.
"""
import importlib
import unittest

from mock_shop import db, models

# alice's seeded password_hash: a real 64-char sha256 of "alice-password".
# Load-bearing: candidates must copy the baseline seed verbatim so the account
# stays loginable, so the test pins both the length and the exact digest.
ALICE_PASSWORD_HASH = "17a96502d336e4c18a43182a353d7f0a38414c6fc4daf678acae834a819cecee"


def _reset_state():
    importlib.reload(models)
    importlib.reload(db)


class DbBehaviorTest(unittest.TestCase):
    def setUp(self):
        _reset_state()

    # ---- feature: case-insensitive email lookup ----

    def test_feature_email_lookup_is_case_insensitive(self):
        user = db.get_user_by_email("ALICE@EXAMPLE.COM")
        self.assertIsNotNone(user, "uppercase email should still resolve to alice")
        self.assertEqual(user.email, "alice@example.com")

    def test_feature_mixed_case_email_lookup(self):
        user = db.get_user_by_email("Bob@Example.Com")
        self.assertIsNotNone(user, "mixed-case email should still resolve to bob")
        self.assertEqual(user.email, "bob@example.com")

    # ---- regression: pre-existing db behavior must keep working ----

    def test_regression_exact_email_lookup(self):
        user = db.get_user_by_email("alice@example.com")
        self.assertIsNotNone(user)
        self.assertEqual(user.id, 1)
        self.assertEqual(user.email, "alice@example.com")

    def test_regression_unknown_email_returns_none(self):
        self.assertIsNone(db.get_user_by_email("nobody@example.com"))

    def test_regression_get_product(self):
        product = db.get_product(10)
        self.assertIsNotNone(product)
        self.assertEqual(product.name, "Mechanical Keyboard")
        self.assertIsNone(db.get_product(999))

    def test_regression_next_order_id_increments(self):
        first = db.next_order_id()
        second = db.next_order_id()
        self.assertGreater(first, 1000)
        self.assertGreater(second, first)

    def test_regression_seed_intact(self):
        self.assertEqual(len(db.USERS), 2, "two users must stay seeded")
        alice = db.get_user_by_email("alice@example.com")
        self.assertIsNotNone(alice)
        self.assertEqual(len(alice.password_hash), 64)
        self.assertEqual(alice.password_hash, ALICE_PASSWORD_HASH)


if __name__ == "__main__":
    unittest.main()
