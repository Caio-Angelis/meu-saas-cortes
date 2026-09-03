"""Tarefas executadas pelo worker RQ (ou thread local)."""

from __future__ import annotations

import logging

from app.web.store import get_store
from app.web.worker import run_playlist_item

_log = logging.getLogger("web.tasks")


def process_playlist_item_task(item_id: int) -> None:
    """Processa um item da playlist (download + run_pipeline)."""
    store = get_store()
    item = store.get(item_id)
    if not item:
        _log.warning("Item %s não encontrado", item_id)
        return
    # A API marca o item como queued antes de disparar a thread/RQ. Aceitar
    # esse estado evita que o worker perca o job numa corrida de inicialização.
    if (
        item.workflow_status != "pendente"
        or item.pipeline_status not in ("idle", "error", "queued")
    ):
        _log.info(
            "Item %s ignorado (status %s / %s)",
            item_id,
            item.workflow_status,
            item.pipeline_status,
        )
        return
    run_playlist_item(item_id)


def process_playlist_batch_task(item_ids: list[int]) -> None:
    """Processa vários itens em sequência (fila «processar playlist»)."""
    for iid in item_ids:
        process_playlist_item_task(iid)
