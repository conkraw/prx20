"""Password verification and cross-session throttling tests."""

import pytest
pytest.importorskip("streamlit", reason="Streamlit runtime is not installed in this test environment")

import unittest
import time

from streamlit.testing.v1 import AppTest

from epa_auth import _LoginThrottle, hash_password, verify_password


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "synthetic correct horse"
        cls.encoded = hash_password(cls.password)

    def test_password_hash_verifies_exact_password_only(self):
        self.assertTrue(verify_password(self.password, self.encoded))
        self.assertFalse(verify_password(self.password + "!", self.encoded))
        self.assertFalse(verify_password("", self.encoded))

    def test_random_salts_make_hashes_different(self):
        self.assertNotEqual(hash_password(self.password), self.encoded)
        self.assertNotIn(self.password, self.encoded)

    def test_hashes_and_password_limits_fail_closed(self):
        for bad_hash in ("", "plainpassword", "pbkdf2_sha256$1$AA==$AA==", None, "pbkdf2_sha256$999999999999999$a$b"):
            with self.subTest(bad_hash=bad_hash):
                self.assertFalse(verify_password(self.password, bad_hash))
        self.assertFalse(verify_password(None, self.encoded))
        self.assertFalse(verify_password("a" * 5000, self.encoded))
        with self.assertRaises(ValueError):
            hash_password("too short")
        with self.assertRaises(ValueError):
            hash_password(self.password, iterations=1)

    def test_throttle_applies_across_sessions_with_same_password_hash(self):
        limiter = _LoginThrottle(limit=2, window=60, lockout=30)
        self.assertEqual(limiter.reserve("same_app", 100), 0)
        self.assertEqual(limiter.reserve("same_app", 101), 0)
        self.assertEqual(limiter.reserve("same_app", 102), 30)
        self.assertEqual(limiter.remaining("same_app", 110), 22)
        self.assertEqual(limiter.reserve("same_app", 120), 12)
        self.assertEqual(limiter.reserve("same_app", 133), 0)

    def test_success_resets_failed_attempts(self):
        limiter = _LoginThrottle(limit=1)
        limiter.reserve("app", 100)
        limiter.success("app")
        self.assertEqual(limiter.reserve("app", 101), 0)

    def test_old_attempts_expire(self):
        limiter = _LoginThrottle(limit=1, window=10)
        limiter.reserve("app", 100)
        self.assertEqual(limiter.reserve("app", 111), 0)

    def test_streamlit_gate_timeout_and_logout_clear_data(self):
        script = f'''
import streamlit as st
from epa_auth import require_login, logout
require_login({{"password_hash": {self.encoded!r}, "session_minutes": 30}})
st.write("PRIVATE_TEST_MARKER")
if st.button("Sign out"):
    logout()
    st.rerun()
'''
        app = AppTest.from_string(script).run()
        self.assertFalse(app.exception)
        self.assertFalse(any("PRIVATE_TEST_MARKER" in x.value for x in app.markdown))
        app.text_input[0].set_value("wrong password")
        app.button[0].click().run()
        self.assertFalse(any("PRIVATE_TEST_MARKER" in x.value for x in app.markdown))
        app.text_input[0].set_value(self.password)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("PRIVATE_TEST_MARKER" in x.value for x in app.markdown))
        app.session_state["synthetic_sensitive_data"] = "example"
        auth = dict(app.session_state["_epa_authentication"])
        auth["last_activity"] = time.monotonic() - 1900
        app.session_state["_epa_authentication"] = auth
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(any("PRIVATE_TEST_MARKER" in x.value for x in app.markdown))
        self.assertNotIn("synthetic_sensitive_data", app.session_state)
        app.text_input[0].set_value(self.password)
        app.button[0].click().run()
        app.session_state["synthetic_sensitive_data"] = "example"
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertNotIn("synthetic_sensitive_data", app.session_state)
        self.assertFalse(any("PRIVATE_TEST_MARKER" in x.value for x in app.markdown))

    def test_streamlit_gate_fails_closed_without_valid_hash(self):
        app = AppTest.from_string('''
import streamlit as st
from epa_auth import require_login
require_login({})
st.write("PRIVATE_TEST_MARKER")
''').run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertFalse(any("PRIVATE_TEST_MARKER" in x.value for x in app.markdown))


if __name__ == "__main__":
    unittest.main()
