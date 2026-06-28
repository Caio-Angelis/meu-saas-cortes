from __future__ import annotations

import subprocess
from collections.abc import Sequence

from app.core.cancel import is_cancelled


def _terminate_hard(proc: subprocess.Popen) -> None:
    """Encerra subprocesso ao cancelar: terminate curtíssimo, depois kill se precisar."""
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=1.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_cancelable(
    cmd: Sequence[str],
    *,
    capture_output: bool = False,
    text: bool = True,
    check: bool = True,
    stdout=None,
    stderr=None,
    poll_interval_sec: float = 0.2,
) -> subprocess.CompletedProcess:
    """
    Executa comando com cancelamento cooperativo.
    Usa wait(timeout) em loop para latência de cancelamento mais previsível; ao final,
    drena PIPE com communicate (evita deadlock quando capture_output está ativo).
    """
    interval = max(0.05, poll_interval_sec)
    p = subprocess.Popen(
        list(cmd),
        stdout=(subprocess.PIPE if capture_output else stdout),
        stderr=(subprocess.PIPE if capture_output else stderr),
        text=text,
    )
    out = ""
    err = ""

    try:
        while True:
            if is_cancelled():
                _terminate_hard(p)
                raise RuntimeError("Cancelado pelo usuário.")
            try:
                p.wait(timeout=interval)
                break
            except subprocess.TimeoutExpired:
                continue
        if capture_output:
            o, e = p.communicate(timeout=30.0)
            out = o or ""
            err = e or ""
        rc = int(p.returncode if p.returncode is not None else 0)
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, cmd, output=out, stderr=err)
        return subprocess.CompletedProcess(cmd, rc, out, err)
    finally:
        try:
            if p.poll() is None and is_cancelled():
                p.kill()
        except Exception:
            pass
