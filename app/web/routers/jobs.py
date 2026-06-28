"""POST /api/jobs — despacha o pipeline (job avulso ou via fila)."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.web.hub import ProgressHub
from app.web.pipeline_form import pipeline_kwargs_from_form
from app.web.queue_backend import enqueue_playlist_batch, enqueue_playlist_item, redis_available
from app.web.schemas import JobCreatedResponse
from app.web.store import get_store
from app.web.worker import save_uploaded_file
from app.download.ytdlp_download import collect_urls_from_lines

router = APIRouter(tags=["jobs"])


@router.post("/api/jobs", response_model=JobCreatedResponse)
async def create_job(
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
    export_zip: bool = Form(False),
    use_playlist: bool = Form(False),
    files: list[UploadFile] = File(default=[]),
) -> JobCreatedResponse:
    hub = ProgressHub.get()
    if hub.is_running():
        raise HTTPException(status_code=409, detail="Já existe um processamento em andamento.")

    if lang not in ("pt", "en"):
        raise HTTPException(status_code=400, detail="lang deve ser pt ou en.")
    if position not in ("bottom", "top"):
        raise HTTPException(status_code=400, detail="position deve ser bottom ou top.")
    if dub_en and dub_pt:
        raise HTTPException(status_code=400, detail="Escolha apenas uma dublagem: en ou pt.")
    if not (urls or "").strip() and not files:
        raise HTTPException(
            status_code=400,
            detail="Informe ao menos uma URL ou um arquivo de vídeo.",
        )

    local_paths: list[str] = []
    for uf in files:
        if not uf.filename:
            continue
        data = await uf.read()
        if not data:
            continue
        local_paths.append(save_uploaded_file(data, uf.filename))

    pipeline_kwargs = pipeline_kwargs_from_form(
        lang=lang,
        position=position,
        font=font,
        color=color,
        bg_color=bg_color,
        opacity=opacity,
        dub_en=dub_en,
        dub_pt=dub_pt,
        tts_voice=tts_voice,
        export_zip=export_zip,
    )

    url_list = collect_urls_from_lines(urls)
    store = get_store()

    if use_playlist or len(url_list) + len(local_paths) > 1:
        ids: list[int] = []
        if url_list:
            ids.extend(store.add_urls(url_list, pipeline_options=pipeline_kwargs))
        if local_paths:
            ids.extend(store.add_local_paths(local_paths, pipeline_options=pipeline_kwargs))
        if not ids:
            raise HTTPException(status_code=400, detail="Nada para enfileirar.")
        for i in ids:
            store.mark_queued(i, None)
        job_id = enqueue_playlist_batch(ids)
        for i in ids:
            store.mark_queued(i, job_id)
        hub.notify_playlist_update()
        return JobCreatedResponse(
            message=f"{len(ids)} item(ns) na playlist — processamento enfileirado.",
            job_id=job_id,
        )

    # Job único: um registro na playlist + fila
    if url_list:
        item_ids = store.add_urls(url_list[:1], pipeline_options=pipeline_kwargs)
    else:
        item_ids = store.add_local_paths(local_paths[:1], pipeline_options=pipeline_kwargs)
    item_id = item_ids[0]
    job_id = enqueue_playlist_item(item_id)
    store.mark_queued(item_id, job_id)
    hub.notify_playlist_update()
    return JobCreatedResponse(
        message="Job enfileirado." + (" (Redis/RQ)" if redis_available() else " (thread local)"),
        job_id=job_id,
        playlist_item_id=item_id,
    )
