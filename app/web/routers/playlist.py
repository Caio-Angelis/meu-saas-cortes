"""API da playlist — fila persistida e workflow pendente/publicado/descartado."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.download.ytdlp_download import collect_urls_from_lines
from app.web.hub import ProgressHub
from app.web.pipeline_form import pipeline_kwargs_from_form
from app.web.queue_backend import enqueue_playlist_batch, redis_available
from app.web.schemas import PlaylistItemResponse, PlaylistListResponse, PlaylistProcessResponse
from app.web.store import WORKFLOW_DISCARDED, WORKFLOW_PUBLISHED, get_store
from app.web.worker import save_uploaded_file

router = APIRouter(tags=["playlist"])


class WorkflowPatch(BaseModel):
    status: str = Field(..., description="publicado | descartado | pendente")


@router.get("/api/playlist/active")
async def playlist_active_progress() -> dict:
    """Progresso agregado (útil quando o worker RQ roda em outro processo)."""
    items = await asyncio.to_thread(lambda: get_store().list_items(limit=100))
    active = [i for i in items if i.pipeline_status in ("queued", "running")]
    if not active:
        return {"active": False, "progress": 0.0, "item_id": None, "message": ""}
    best = max(active, key=lambda i: (i.pipeline_status == "running", i.progress))
    return {
        "active": True,
        "progress": best.progress,
        "item_id": best.id,
        "message": best.title or best.source,
        "count": len(active),
    }


@router.get("/api/playlist", response_model=PlaylistListResponse)
async def list_playlist(workflow: str | None = None, limit: int = 100) -> PlaylistListResponse:
    items = await asyncio.to_thread(
        lambda: get_store().list_items(workflow=workflow, limit=limit)
    )
    return PlaylistListResponse(
        items=[PlaylistItemResponse.from_item(i) for i in items],
        redis=redis_available(),
    )


@router.post("/api/playlist", response_model=PlaylistListResponse)
async def add_to_playlist(
    urls: str = Form(""),
    lang: str = Form("pt"),
    position: str = Form("bottom"),
    font: str = Form("Arial"),
    color: str = Form("#FFFF00"),
    bg_color: str = Form("#000000"),
    opacity: int = Form(75),
    dub_en: bool = Form(False),
    dub_pt: bool = Form(False),
    tts_voice: str = Form(""),
    hook_text: str = Form(""),
    outro_text: str = Form(""),
    clip_start: str = Form(""),
    clip_end: str = Form(""),
    files: list[UploadFile] = File(default=[]),
) -> PlaylistListResponse:
    if lang not in ("pt", "en"):
        raise HTTPException(status_code=400, detail="lang deve ser pt ou en.")
    if dub_en and dub_pt:
        raise HTTPException(status_code=400, detail="Escolha apenas uma dublagem.")

    url_list = collect_urls_from_lines(urls)
    local_paths: list[str] = []
    for uf in files:
        if not uf.filename:
            continue
        data = await uf.read()
        if data:
            local_paths.append(save_uploaded_file(data, uf.filename))

    if not url_list and not local_paths:
        raise HTTPException(status_code=400, detail="Informe URLs ou arquivos para a playlist.")

    try:
        opts = pipeline_kwargs_from_form(
            lang=lang,
            position=position,
            font=font,
            color=color,
            bg_color=bg_color,
            opacity=opacity,
            dub_en=dub_en,
            dub_pt=dub_pt,
            tts_voice=tts_voice,
            hook_text=hook_text,
            outro_text=outro_text,
            clip_start=clip_start,
            clip_end=clip_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store = get_store()
    created: list[int] = []
    if url_list:
        created.extend(await asyncio.to_thread(store.add_urls, url_list, pipeline_options=opts))
    if local_paths:
        created.extend(
            await asyncio.to_thread(store.add_local_paths, local_paths, pipeline_options=opts)
        )

    items = [store.get(i) for i in created]
    ProgressHub.get().notify_playlist_update()
    return PlaylistListResponse(
        items=[PlaylistItemResponse.from_item(i) for i in items if i],
        redis=redis_available(),
        message=f"{len(created)} item(ns) adicionado(s) à playlist.",
    )


@router.post("/api/playlist/process", response_model=PlaylistProcessResponse)
async def process_playlist(
    item_ids: str = Form(""),
) -> PlaylistProcessResponse:
    """Enfileira itens pendentes (todos ou ids separados por vírgula)."""
    if ProgressHub.get().is_running():
        raise HTTPException(status_code=409, detail="Já existe um processamento em andamento no hub.")

    ids: list[int] | None = None
    raw = (item_ids or "").strip()
    if raw:
        try:
            ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="item_ids inválido.") from exc

    store = get_store()
    pending = await asyncio.to_thread(store.list_processable, item_ids=ids)
    if not pending:
        raise HTTPException(status_code=404, detail="Nenhum item pendente para processar.")

    pending_ids = [item.id for item in pending]
    for iid in pending_ids:
        await asyncio.to_thread(store.mark_queued, iid, None)

    job_id = await asyncio.to_thread(enqueue_playlist_batch, pending_ids)
    for iid in pending_ids:
        await asyncio.to_thread(store.mark_queued, iid, job_id)

    ProgressHub.get().notify_playlist_update()
    return PlaylistProcessResponse(
        enqueued=len(pending_ids),
        job_ids=[job_id],
        message=f"{len(pending_ids)} item(ns) enfileirado(s) (1 job em sequência).",
        redis=redis_available(),
    )


@router.patch("/api/playlist/{item_id}", response_model=PlaylistItemResponse)
async def patch_workflow(item_id: int, body: WorkflowPatch) -> PlaylistItemResponse:
    status = body.status.strip().lower()
    if status not in (WORKFLOW_PUBLISHED, WORKFLOW_DISCARDED, "pendente"):
        raise HTTPException(
            status_code=400,
            detail="status deve ser pendente, publicado ou descartado.",
        )

    def _update():
        return get_store().set_workflow(item_id, status)  # type: ignore[arg-type]

    try:
        item = await asyncio.to_thread(_update)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    ProgressHub.get().notify_playlist_update()
    return PlaylistItemResponse.from_item(item)


@router.delete("/api/playlist/{item_id}")
async def delete_playlist_item(item_id: int) -> dict:
    ok = await asyncio.to_thread(get_store().delete_item, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    ProgressHub.get().notify_playlist_update()
    return {"ok": True}
