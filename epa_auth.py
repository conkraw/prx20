"""Shared-password access gate for the EPA Tracker.

Secrets contain a salted PBKDF2 hash, never the plaintext password. Throttling
is process-wide and survives new browser sessions, but resets on app restart.
The inactivity timeout is checked on every app interaction.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

_ITERATIONS = 600_000
_MAX_ITERATIONS = 2_000_000
_MAX_PASSWORD_BYTES = 4096
_AUTH_KEY = "_epa_authentication"


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)


def _parse_hash(encoded: str) -> tuple[int, bytes, bytes]:
    try:
        scheme, count, salt_text, digest_text = encoded.split("$")
        iterations = int(count)
        salt, digest = _b64decode(salt_text), _b64decode(digest_text)
    except (ValueError, AttributeError, UnicodeError, binascii.Error) as exc:
        raise ValueError("Invalid password hash") from exc
    if scheme != "pbkdf2_sha256" or not _ITERATIONS <= iterations <= _MAX_ITERATIONS:
        raise ValueError("Unsupported password hash")
    if not 16 <= len(salt) <= 64 or len(digest) != 32:
        raise ValueError("Invalid password hash")
    return iterations, salt, digest


def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    """Hash a password with a random salt. Requires at least 12 characters."""
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("Use a password or passphrase with at least 12 characters.")
    raw = password.encode("utf-8")
    if len(raw) > _MAX_PASSWORD_BYTES:
        raise ValueError("The password is too long.")
    if not _ITERATIONS <= iterations <= _MAX_ITERATIONS:
        raise ValueError("Invalid PBKDF2 iteration count.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw, salt, iterations)
    return "$".join((
        "pbkdf2_sha256", str(iterations),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ))


def verify_password(password: str, encoded: str) -> bool:
    try:
        iterations, salt, expected = _parse_hash(encoded)
        if not isinstance(password, str):
            return False
        raw = password.encode("utf-8")
        if len(raw) > _MAX_PASSWORD_BYTES:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", raw, salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


class _LoginThrottle:
    def __init__(self, limit: int = 8, window: int = 600, lockout: int = 300):
        self.limit, self.window, self.lockout = limit, window, lockout
        self._lock = threading.Lock()
        self._attempts: dict[str, deque[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def reserve(self, key: str, now: float) -> int:
        """Reserve one attempt atomically. Return remaining lockout seconds."""
        with self._lock:
            until = self._blocked_until.get(key, 0.0)
            if until > now:
                return max(1, int(until - now + 0.999))
            attempts = self._attempts.setdefault(key, deque())
            if until:
                attempts.clear()
                self._blocked_until.pop(key, None)
            while attempts and attempts[0] <= now - self.window:
                attempts.popleft()
            if len(attempts) >= self.limit:
                self._blocked_until[key] = now + self.lockout
                return self.lockout
            attempts.append(now)
            return 0

    def remaining(self, key: str, now: float) -> int:
        with self._lock:
            until = self._blocked_until.get(key, 0.0)
            return max(0, int(until - now + 0.999))

    def success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
            self._blocked_until.pop(key, None)


_THROTTLE = _LoginThrottle()


def _fingerprint(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def logout() -> None:
    """Clear resident data, uploaded files, and authentication from this session."""
    import streamlit as st
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def require_login(app_config: Mapping[str, Any]) -> None:
    """Stop Streamlit unless the session has authenticated with the saved hash."""
    import streamlit as st

    encoded = app_config.get("password_hash", "")
    try:
        _parse_hash(encoded)
        minutes = int(app_config.get("session_minutes", 30))
        if not 1 <= minutes <= 480:
            raise ValueError("Invalid session length")
    except (ValueError, TypeError, AttributeError):
        logout()
        st.error("Password access is not configured. Add a generated password_hash and a valid session_minutes value to Streamlit Secrets.")
        st.stop()

    fingerprint = _fingerprint(encoded)
    now = time.monotonic()
    auth = st.session_state.get(_AUTH_KEY)
    if isinstance(auth, dict):
        last_seen = auth.get("last_activity", 0)
        if auth.get("fingerprint") == fingerprint and isinstance(last_seen, (int, float)) and 0 <= now - last_seen <= minutes * 60:
            auth["last_activity"] = now
            return
        logout()
        st.info("Your session ended. Sign in again to continue.")

    gate = st.empty()
    with gate.container():
        _, center, _ = st.columns([1, 1.5, 1])
        with center:
            st.markdown('<div class="epa-login"><div class="epa-kicker">RESIDENCY EDUCATION</div>'
                        '<h1>EPA Tracker</h1><p>Your resident assessment workspace.</p></div>',
                        unsafe_allow_html=True)
            remaining = _THROTTLE.remaining(fingerprint, now)
            if remaining:
                st.warning(f"Too many sign-in attempts. Try again in {remaining} seconds.")
            with st.form("epa_login_form", clear_on_submit=True):
                password = st.text_input("Password", type="password", key="_epa_password_entry", max_chars=1024)
                submitted = st.form_submit_button("Sign in", type="primary", disabled=bool(remaining), width="stretch")
            st.caption("Your evaluations are loaded only after you sign in.")
            if submitted:
                blocked = _THROTTLE.reserve(fingerprint, time.monotonic())
                if blocked:
                    st.error(f"Too many sign-in attempts. Try again in {blocked} seconds.")
                else:
                    with st.spinner("Checking password..."):
                        accepted = verify_password(password, encoded)
                    if accepted:
                        _THROTTLE.success(fingerprint)
                        st.session_state[_AUTH_KEY] = {
                            "fingerprint": fingerprint,
                            "last_activity": time.monotonic(),
                        }
                        # Delete the submitted secret and the login UI before
                        # the archive request starts. Do not keep a faded form.
                        st.session_state.pop("_epa_password_entry", None)
                        gate.empty()
                        return
                    st.error("The password was not accepted.")
    st.stop()
