"""Cancelamento cooperativo global."""

from __future__ import annotations

import pytest

from app.core.cancel import is_cancelled, raise_if_cancelled, request_cancel, reset_cancel


@pytest.fixture(autouse=True)
def _clear_cancel():
    reset_cancel()
    yield
    reset_cancel()


def test_request_and_is_cancelled() -> None:
    assert is_cancelled() is False
    request_cancel()
    assert is_cancelled() is True


def test_raise_if_cancelled() -> None:
    request_cancel()
    with pytest.raises(RuntimeError, match="foo"):
        raise_if_cancelled("foo")
