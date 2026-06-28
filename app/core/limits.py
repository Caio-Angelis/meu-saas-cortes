from __future__ import annotations

import os
import random
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_sleep_sec: float = 1.2
    max_sleep_sec: float = 30.0
    jitter_frac: float = 0.25
    backoff: float = 2.0

    def sleep_for_attempt(self, attempt_idx: int) -> float:
        # attempt_idx: 0..(attempts-2) para os sleeps intermediários
        s = min(self.max_sleep_sec, self.base_sleep_sec * (self.backoff**attempt_idx))
        j = s * self.jitter_frac
        return max(0.0, s + random.uniform(-j, j))


class ConcurrencyLimiter:
    def __init__(self, max_in_flight: int) -> None:
        self._sem = threading.Semaphore(max(1, int(max_in_flight)))

    @contextmanager
    def acquire(self):
        self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()


# Defaults conservadores: evita “tempestade” em clip paralelo.
GROQ_MAX_IN_FLIGHT = _env_int("GROQ_MAX_IN_FLIGHT", 2)
TRANSLATE_MAX_IN_FLIGHT = _env_int("TRANSLATE_MAX_IN_FLIGHT", 2)

GROQ_RETRY_ATTEMPTS = _env_int("GROQ_RETRY_ATTEMPTS", 3)
GROQ_RETRY_BASE_SLEEP = _env_float("GROQ_RETRY_BASE_SLEEP", 1.5)
GROQ_RETRY_MAX_SLEEP = _env_float("GROQ_RETRY_MAX_SLEEP", 30.0)

TRANSLATE_RETRY_ATTEMPTS = _env_int("TRANSLATE_RETRY_ATTEMPTS", 2)
TRANSLATE_RETRY_BASE_SLEEP = _env_float("TRANSLATE_RETRY_BASE_SLEEP", 0.8)
TRANSLATE_RETRY_MAX_SLEEP = _env_float("TRANSLATE_RETRY_MAX_SLEEP", 10.0)

groq_limiter = ConcurrencyLimiter(GROQ_MAX_IN_FLIGHT)
translate_limiter = ConcurrencyLimiter(TRANSLATE_MAX_IN_FLIGHT)

groq_retry_policy = RetryPolicy(
    attempts=max(1, GROQ_RETRY_ATTEMPTS),
    base_sleep_sec=max(0.1, GROQ_RETRY_BASE_SLEEP),
    max_sleep_sec=max(0.2, GROQ_RETRY_MAX_SLEEP),
    jitter_frac=0.25,
    backoff=2.0,
)

translate_retry_policy = RetryPolicy(
    attempts=max(1, TRANSLATE_RETRY_ATTEMPTS),
    base_sleep_sec=max(0.1, TRANSLATE_RETRY_BASE_SLEEP),
    max_sleep_sec=max(0.2, TRANSLATE_RETRY_MAX_SLEEP),
    jitter_frac=0.2,
    backoff=2.0,
)


def with_retries(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    should_retry: Callable[[Exception], bool],
    on_retry: Callable[[int, float, Exception], None] | None = None,
) -> T:
    last: Exception | None = None
    attempts = max(1, int(policy.attempts))
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt + 1 >= attempts or not should_retry(e):
                raise
            sleep_s = policy.sleep_for_attempt(attempt)
            if on_retry is not None:
                on_retry(attempt + 1, sleep_s, e)
            time.sleep(sleep_s)
    raise last  # pragma: no cover

