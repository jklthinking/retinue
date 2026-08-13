"""Password hashing and token generation. Standard library only."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


@dataclass
class _FailureState:
    failures: int = 0
    pending: int = 0
    retry_at: float = 0.0
    last_failure_at: float = 0.0


class LoginThrottle:
    """Bounded, process-local progressive backoff for interactive logins."""

    ACCOUNT_FAILURE_LIMIT = 3
    SOURCE_FAILURE_LIMIT = 10
    BASE_DELAY_SECONDS = 1
    MAX_DELAY_SECONDS = 60
    RESET_AFTER_SECONDS = 15 * 60
    MAX_TRACKED_KEYS = 10_000

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._account_failures: dict[bytes, _FailureState] = {}
        self._source_failures: dict[bytes, _FailureState] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()

    def begin_attempt(self, account: str, source: str) -> int:
        """Reserve pre-hash capacity, or return the current retry delay."""
        now = self._clock()
        with self._lock:
            account_state = self._state_for(
                self._account_failures, self._key(account), now
            )
            source_state = self._state_for(
                self._source_failures, self._key(source), now
            )
            if account_state is None or source_state is None:
                return self.BASE_DELAY_SECONDS
            retry_at = max(
                account_state.retry_at,
                source_state.retry_at,
            )
            if retry_at > now:
                return math.ceil(retry_at - now)
            if self._pending_budget_used(
                account_state, self.ACCOUNT_FAILURE_LIMIT
            ) or self._pending_budget_used(source_state, self.SOURCE_FAILURE_LIMIT):
                return self.BASE_DELAY_SECONDS
            account_state.pending += 1
            source_state.pending += 1
            return 0

    def record_failure(self, account: str, source: str) -> None:
        now = self._clock()
        with self._lock:
            self._finish_pending(self._account_failures, self._key(account))
            self._finish_pending(self._source_failures, self._key(source))
            self._record(
                self._account_failures,
                self._key(account),
                self.ACCOUNT_FAILURE_LIMIT,
                now,
            )
            self._record(
                self._source_failures,
                self._key(source),
                self.SOURCE_FAILURE_LIMIT,
                now,
            )

    def record_success(self, account: str, source: str) -> None:
        with self._lock:
            self._account_failures.pop(self._key(account), None)
            self._source_failures.pop(self._key(source), None)

    def cancel_attempt(self, account: str, source: str) -> None:
        with self._lock:
            self._finish_pending(self._account_failures, self._key(account))
            self._finish_pending(self._source_failures, self._key(source))

    def source_retry_after(self, source: str) -> int:
        now = self._clock()
        with self._lock:
            state = self._active_state(self._source_failures, self._key(source), now)
            if state is None:
                return 0
            if state.retry_at > now:
                return math.ceil(state.retry_at - now)
            if self._pending_budget_used(state, self.SOURCE_FAILURE_LIMIT):
                return self.BASE_DELAY_SECONDS
            return 0

    @staticmethod
    def _pending_budget_used(state: _FailureState, failure_limit: int) -> bool:
        available = (
            1 if state.failures >= failure_limit else failure_limit - state.failures
        )
        return state.pending >= available

    def _active_state(
        self, states: dict[bytes, _FailureState], key: bytes, now: float
    ) -> _FailureState | None:
        state = states.get(key)
        if (
            state
            and not state.pending
            and state.failures
            and now - state.last_failure_at >= self.RESET_AFTER_SECONDS
        ):
            states.pop(key, None)
            return None
        return state

    def _state_for(
        self, states: dict[bytes, _FailureState], key: bytes, now: float
    ) -> _FailureState | None:
        state = self._active_state(states, key, now)
        if state is not None:
            return state
        if len(states) >= self.MAX_TRACKED_KEYS:
            removable = [
                candidate for candidate, value in states.items() if not value.pending
            ]
            if not removable:
                return None
            oldest_key = min(
                removable, key=lambda candidate: states[candidate].last_failure_at
            )
            states.pop(oldest_key)
        state = _FailureState()
        states[key] = state
        return state

    @staticmethod
    def _finish_pending(states: dict[bytes, _FailureState], key: bytes) -> None:
        state = states.get(key)
        if state is None:
            return
        state.pending = max(0, state.pending - 1)
        if not state.pending and not state.failures:
            states.pop(key)

    def _record(
        self,
        states: dict[bytes, _FailureState],
        key: bytes,
        failure_limit: int,
        now: float,
    ) -> None:
        state = self._active_state(states, key, now)
        if state is None:
            state = self._state_for(states, key, now)
            if state is None:
                return
        state.failures += 1
        state.last_failure_at = now
        if state.failures >= failure_limit:
            exponent = state.failures - failure_limit
            delay = min(self.MAX_DELAY_SECONDS, self.BASE_DELAY_SECONDS * 2**exponent)
            state.retry_at = now + delay


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def new_token(prefix: str = "rtn") -> str:
    """Opaque bearer token; only its hash is stored server-side."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
