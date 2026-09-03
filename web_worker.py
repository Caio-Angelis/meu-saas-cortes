"""
Worker RQ — processa a fila Redis da interface web.

Requer Redis (ex.: `docker run -d -p 6379:6379 redis:7-alpine`)
e REDIS_URL no ambiente (ver .env.example).

Na raiz do projeto:
    .venv/bin/python web_worker.py
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import _venv_reexec

_venv_reexec.ensure_venv(__file__)

from app.core.linux_desktop_bootstrap import apply_linux_desktop_defaults

apply_linux_desktop_defaults()

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
os.chdir(_ROOT)

from app.core.logging_setup import setup_logging

setup_logging(gui_quiet=True)


def main() -> None:
    from rq import Connection, Worker

    from app.web.queue_backend import get_redis_connection, get_rq_queue, redis_available

    if not redis_available():
        print(
            "\nErro: REDIS_URL não definido ou pacote redis indisponível.\n"
            "Defina REDIS_URL=redis://127.0.0.1:6379/0 no .env e instale: pip install redis rq\n"
            "Sem Redis, os jobs rodam em thread dentro do web_main.py.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    q = get_rq_queue()
    conn = get_redis_connection()
    print(f"\n  Worker RQ ouvindo fila «{q.name}» — {conn}\n  (Ctrl+C para encerrar)\n", flush=True)

    with Connection(conn):
        worker = Worker([q], connection=conn)
        worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
