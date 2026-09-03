"""Motor de movimento da GUI do Cortes Lab.

Animações leves (transições de página, indicador lateral, progresso) agendadas
com ``after()`` do próprio Tkinter — sem dependências externas. Cada tween é
registrado em um canal: disparar um novo tween no mesmo canal cancela o
anterior, o que torna seguro alternar de workspace em sequência.
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable, Sequence

__all__ = [
    "Motion",
    "ease_out_back",
    "ease_out_cubic",
    "ease_out_quint",
    "lerp",
    "lerp_color",
    "sample_gradient",
]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_out_quint(t: float) -> float:
    return 1.0 - (1.0 - t) ** 5


def ease_out_back(t: float) -> float:
    """Saída com leve overshoot — usada no indicador lateral (efeito mola)."""
    c1 = 1.70158
    c3 = c1 + 1.0
    u = t - 1.0
    return 1.0 + c3 * u**3 + c1 * u**2


def lerp_color(color_a: str, color_b: str, t: float) -> str:
    """Interpola duas cores hex (#RRGGBB) no intervalo 0–1."""
    a = int(color_a.lstrip("#"), 16)
    b = int(color_b.lstrip("#"), 16)
    channels = []
    for shift in (16, 8, 0):
        ca = (a >> shift) & 255
        cb = (b >> shift) & 255
        channels.append(round(lerp(ca, cb, t)))
    return f"#{(channels[0] << 16) | (channels[1] << 8) | channels[2]:06X}"


def sample_gradient(colors: Sequence[str], t: float) -> str:
    """Amostra um gradiente multi-stop em t (0–1)."""
    if len(colors) == 1:
        return colors[0]
    t = max(0.0, min(1.0, t))
    span = 1.0 / (len(colors) - 1)
    idx = min(int(t / span), len(colors) - 2)
    local = (t - idx * span) / span
    return lerp_color(colors[idx], colors[idx + 1], local)


class Motion:
    """Agenda tweens por canal no event loop do Tk."""

    def __init__(self, root: tk.Misc, *, frame_ms: int = 16) -> None:
        self._root = root
        self._frame_ms = frame_ms
        self._tokens: dict[str, int] = {}

    def cancel(self, channel: str) -> None:
        self._tokens.pop(channel, None)

    def tween(
        self,
        channel: str,
        duration_ms: int,
        update: Callable[[float], None],
        *,
        delay_ms: int = 0,
        ease: Callable[[float], float] = ease_out_cubic,
        done: Callable[[], None] | None = None,
    ) -> None:
        """Anima ``update(t_eased)`` por ``duration_ms``; cancela tween anterior do canal."""
        token = time.monotonic_ns()
        self._tokens[channel] = token
        if delay_ms > 0:
            self._root.after(
                delay_ms,
                lambda: self._begin(channel, token, duration_ms, update, ease, done),
            )
        else:
            self._begin(channel, token, duration_ms, update, ease, done)

    def _begin(
        self,
        channel: str,
        token: int,
        duration_ms: int,
        update: Callable[[float], None],
        ease: Callable[[float], float],
        done: Callable[[], None] | None,
    ) -> None:
        if self._tokens.get(channel) != token:
            return
        start = time.monotonic()
        duration_s = max(duration_ms, 1) / 1000.0

        def step() -> None:
            if self._tokens.get(channel) != token:
                return
            t = min(1.0, (time.monotonic() - start) / duration_s)
            try:
                update(ease(t))
            except tk.TclError:
                self._tokens.pop(channel, None)
                return
            if t >= 1.0:
                self._tokens.pop(channel, None)
                if done is not None:
                    done()
                return
            self._root.after(self._frame_ms, step)

        step()
