from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO


_NOISY_LOGGER_PREFIXES = (
    "absl",
    "mediapipe",
    "tensorflow",
)


class _GuiStreamFormatter(logging.Formatter):
    """Formata logs enviados ao painel da GUI durante o worker."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if record.levelno >= logging.ERROR:
            return f"[ERRO] {msg}"
        if record.levelno >= logging.WARNING:
            return f"[!] {msg}"
        return msg


class _SuppressNoisyLoggers(logging.Filter):
    """Evita encher o terminal com EGL/gRPC do MediaPipe, absl, etc. (modo GUI)."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        return not any(
            name == p or name.startswith(p + ".")
            for p in _NOISY_LOGGER_PREFIXES
        )


def setup_logging(*, level: str | int | None = None, gui_quiet: bool = False) -> None:
    """
    Logging simples para CLI/GUI.
    - Usa stdout (a GUI captura stdout/stderr e mostra no Text)
    - Não duplica handlers se chamado mais de uma vez
    - gui_quiet: filtra loggers ruidosos (absl/mediapipe/tensorflow) no terminal
    """
    root = logging.getLogger()
    if root.handlers:
        return

    raw = (os.getenv("LOG_LEVEL") or "").strip().upper()
    if level is not None:
        lvl = level
    elif raw:
        lvl = raw
    elif gui_quiet:
        lvl = "WARNING"
    else:
        lvl = "INFO"
    if isinstance(lvl, str):
        numeric = getattr(logging, lvl, logging.INFO)
    else:
        numeric = int(lvl)

    h = logging.StreamHandler(stream=sys.stdout)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    h.setFormatter(fmt)
    if gui_quiet:
        h.addFilter(_SuppressNoisyLoggers())
    root.addHandler(h)
    root.setLevel(numeric)


@contextmanager
def gui_pipeline_log_redirect(stream: TextIO) -> Iterator[None]:
    """
    Durante o worker da GUI: envia logs do app para o mesmo destino do print (ex.: fila → painel),
    com filtro de ruído (absl/mediapipe). Restaura handlers ao sair.
    """
    root = logging.getLogger()
    saved = list(root.handlers)
    old_level = root.level
    for h in saved:
        root.removeHandler(h)
    qh = logging.StreamHandler(stream)
    qh.setFormatter(
        _GuiStreamFormatter("%(levelname)s %(name)s - %(message)s\n")
    )
    qh.addFilter(_SuppressNoisyLoggers())
    qh.setLevel(logging.INFO)
    root.addHandler(qh)
    root.setLevel(logging.INFO)
    try:
        yield
    finally:
        root.removeHandler(qh)
        for h in saved:
            root.addHandler(h)
        root.setLevel(old_level)

