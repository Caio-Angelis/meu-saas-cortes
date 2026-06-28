"""Fila de jobs: RQ + Redis quando disponível; fallback em thread no processo web."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

_log = logging.getLogger("web.queue")

_REDIS_URL = (os.getenv("REDIS_URL") or os.getenv("RQ_REDIS_URL") or "").strip()
_queue_name = (os.getenv("RQ_QUEUE_NAME") or "cortes").strip() or "cortes"


def redis_available() -> bool:
    if not _REDIS_URL:
        return False
    try:
        import redis  # noqa: F401
    except ImportError:
        return False
    return True


def get_redis_connection():
    import redis

    return redis.from_url(_REDIS_URL)


def get_rq_queue():
    from rq import Queue

    return Queue(_queue_name, connection=get_redis_connection())


def enqueue_callable(func: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """
    Enfileira `func` e devolve id do job (RQ) ou id sintético `local-<n>`.
    """
    if redis_available():
        from rq import Queue

        q = get_rq_queue()
        job = q.enqueue(func, *args, **kwargs, job_timeout="6h")
        _log.info("Job RQ enfileirado: %s", job.id)
        return str(job.id)

    job_id = f"local-{threading.get_ident()}-{id(args)}"
    _log.info("Redis indisponível — executando job em thread (%s)", job_id)

    def _run() -> None:
        try:
            func(*args, **kwargs)
        except Exception:
            _log.exception("Job local falhou")

    t = threading.Thread(target=_run, name=f"web-job-{job_id}", daemon=True)
    t.start()
    return job_id


def enqueue_playlist_item(item_id: int) -> str:
    from app.web.tasks import process_playlist_item_task

    return enqueue_callable(process_playlist_item_task, item_id)


def enqueue_playlist_batch(item_ids: list[int]) -> str:
    from app.web.tasks import process_playlist_batch_task

    return enqueue_callable(process_playlist_batch_task, item_ids)
