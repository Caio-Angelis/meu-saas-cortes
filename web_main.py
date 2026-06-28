"""
Interface web local (FastAPI) — alternativa à GUI Tkinter.

Execute na raiz do projeto:
    python web_main.py
    # ou:
    .venv/bin/python web_main.py
"""

from __future__ import annotations

import _venv_reexec

_venv_reexec.ensure_venv(__file__)

from app.core.linux_desktop_bootstrap import apply_linux_desktop_defaults

apply_linux_desktop_defaults()

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
os.chdir(_ROOT)

from app.core.logging_setup import setup_logging
from app.web.app import create_app

setup_logging(gui_quiet=True)

app = create_app()

def _main() -> None:
    import os
    import socket
    import sys

    import uvicorn

    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8765"))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        print(
            f"\nErro: não foi possível usar {host}:{port} ({exc}).\n"
            "Provável causa: outra instância de web_main.py já está rodando.\n"
            "  ss -tlnp | grep 8765\n"
            "  kill <PID>\n"
            "Ou use outra porta: WEB_PORT=8766 .venv/bin/python web_main.py\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    finally:
        sock.close()

    url = f"http://{host}:{port}/"
    print(f"\n  Servidor web local: {url}\n  (Ctrl+C para encerrar)\n", flush=True)

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    _main()
