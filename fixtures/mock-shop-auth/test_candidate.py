"""Behavior tests for a candidate mock_shop/auth.py.

The candidate REPLACES mock_shop/auth.py inside the real mock-shop package, then
runs against the rest of the package (db, models, payments, checkout). Tests are
behavior-level and implementation-agnostic: they drive the public auth API
(signup / login / current_user / logout / verify_password) and never reach into a
candidate's private rate-limit helpers, so sliding-window, token-bucket, counter,
or any other correct lockout design all pass.

Naming convention shared by every mock-shop fixture:
  test_feature_*     -> the requested feature (login rate limiting + lockout)
  test_regression_*  -> pre-existing auth behavior that must keep working

State is reset per test by reloading the package modules, so module-level session
and lockout state from one test never leaks into the next.
"""
import importlib
import unittest

from mock_shop import db, models
from mock_shop import auth

# Enough consecutive failures to trip any reasonable lockout threshold.
FAILED_ATTEMPTS = 12


def _reset_state():
    importlib.reload(models)
    importlib.reload(db)
    importlib.reload(auth)


class AuthBehaviorTest(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def _signup(self, email="rl-user@example.com", password="correct-horse-battery"):
        auth.signup(email, password)
        return email, password

    def _hammer_failed_logins(self, email, attempts=FAILED_ATTEMPTS):
        for _ in range(attempts):
            try:
                auth.login(email, "definitely-wrong-password")
            except Exception:
                pass

    # ---- regression: pre-existing auth behavior must keep working ----

    def test_regression_login_success_returns_token(self):
        email, password = self._signup()
        token = auth.login(email, password)
        self.assertTrue(token, "login should return a truthy session token")
        user = auth.current_user(token)
        self.assertIsNotNone(user, "token should resolve to a user")
        self.assertEqual(user.email, email)

    def test_regression_login_wrong_password_rejected(self):
        email, password = self._signup()
        with self.assertRaises(Exception):
            auth.login(email, "not-" + password)

    def test_regression_signup_password_hashing_intact(self):
        email, password = self._signup("hash@example.com", "pw-abc-123456")
        user = db.get_user_by_email(email)
        self.assertIsNotNone(user)
        self.assertTrue(auth.verify_password(password, user.password_hash))
        self.assertFalse(auth.verify_password("wrong", user.password_hash))

    def test_regression_logout_invalidates_session(self):
        email, password = self._signup()
        token = auth.login(email, password)
        auth.logout(token)
        self.assertIsNone(auth.current_user(token))

    # ---- feature: login rate limiting + account lockout ----

    def test_feature_lockout_blocks_even_correct_password(self):
        email, password = self._signup()
        self._hammer_failed_logins(email)
        # After too many failures the account must be locked: the *correct*
        # password is refused (no usable token issued) during the lockout.
        with self.assertRaises(Exception):
            auth.login(email, password)

    def test_feature_lockout_scoped_per_account(self):
        victim, _ = self._signup("victim@example.com", "victim-pw-123")
        bystander, bystander_pw = self._signup("bystander@example.com", "bystander-pw-123")
        self._hammer_failed_logins(victim)
        # A different account that never failed must still be able to log in:
        # lockout is per-account, not a global kill switch.
        token = auth.login(bystander, bystander_pw)
        self.assertTrue(token)


if __name__ == "__main__":
    unittest.main()
