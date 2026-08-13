"""Security helper tests that do not invoke the expensive password hash."""

from server.security import LoginThrottle


def test_login_throttle_uses_capped_exponential_account_backoff():
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])

    for _ in range(3):
        assert throttle.begin_attempt("account-a", "source-a") == 0
        throttle.record_failure("account-a", "source-a")
    assert throttle.begin_attempt("account-a", "source-a") == 1

    current_delay = 1
    for expected_delay in (2, 4, 8, 16, 32, 60, 60):
        now[0] += current_delay
        assert throttle.begin_attempt("account-a", "source-a") == 0
        throttle.record_failure("account-a", "source-a")
        assert throttle.begin_attempt("account-a", "source-a") == expected_delay
        current_delay = expected_delay

    now[0] += LoginThrottle.RESET_AFTER_SECONDS
    assert throttle.begin_attempt("account-a", "source-a") == 0
    throttle.cancel_attempt("account-a", "source-a")


def test_login_throttle_reserves_pre_hash_capacity_for_parallel_attempts():
    throttle = LoginThrottle(clock=lambda: 0.0)

    for _ in range(3):
        assert throttle.begin_attempt("account-a", "source-a") == 0
    assert throttle.begin_attempt("account-a", "source-a") == 1

    for _ in range(3):
        throttle.cancel_attempt("account-a", "source-a")
    assert throttle.begin_attempt("account-a", "source-a") == 0


def test_login_throttle_applies_source_budget_across_accounts():
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])

    for index in range(10):
        account = f"account-{index}"
        assert throttle.begin_attempt(account, "source-a") == 0
        throttle.record_failure(account, "source-a")

    assert throttle.source_retry_after("source-a") == 1
    assert throttle.begin_attempt("different-account", "source-a") == 1
