from __future__ import annotations

import threading

_cancel_event = threading.Event()


def reset_cancel() -> None:
    _cancel_event.clear()


def request_cancel() -> None:
    _cancel_event.set()


def is_cancelled() -> bool:
    return _cancel_event.is_set()


def raise_if_cancelled(message: str = "Operação cancelada pelo usuário.") -> None:
    if is_cancelled():
        raise RuntimeError(message)

