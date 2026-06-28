"""Retry e limitador de concorrência."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.limits import (
    ConcurrencyLimiter,
    RetryPolicy,
    with_retries,
)


def test_retry_policy_sleep_bounded() -> None:
    pol = RetryPolicy(attempts=5, base_sleep_sec=100.0, max_sleep_sec=5.0, jitter_frac=0.0, backoff=2.0)
    assert pol.sleep_for_attempt(0) <= 5.0
    assert pol.sleep_for_attempt(3) <= 5.0


def test_with_retries_returns_on_first_success() -> None:
    assert with_retries(lambda: 7, policy=RetryPolicy(attempts=3), should_retry=lambda e: True) == 7


def test_with_retries_no_retry_on_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def boom():
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        with_retries(
            boom,
            policy=RetryPolicy(attempts=3, base_sleep_sec=0.0, max_sleep_sec=0.0, jitter_frac=0.0),
            should_retry=lambda e: False,
        )


def test_with_retries_succeeds_after_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)
    n = {"c": 0}

    def fn():
        n["c"] += 1
        if n["c"] < 3:
            raise ConnectionError("retry")
        return "ok"

    assert (
        with_retries(
            fn,
            policy=RetryPolicy(attempts=5, base_sleep_sec=0.01, max_sleep_sec=0.01, jitter_frac=0.0),
            should_retry=lambda e: isinstance(e, ConnectionError),
        )
        == "ok"
    )
    assert n["c"] == 3


def test_concurrency_limiter_max_two_in_flight() -> None:
    lim = ConcurrencyLimiter(2)
    active = {"n": 0}
    peak = {"v": 0}
    lock = threading.Lock()

    def task(_):
        with lim.acquire():
            with lock:
                active["n"] += 1
                peak["v"] = max(peak["v"], active["n"])
            time.sleep(0.02)
            with lock:
                active["n"] -= 1
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(task, range(8)))
    assert peak["v"] <= 2
