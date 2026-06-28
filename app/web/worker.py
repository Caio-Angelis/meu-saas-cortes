"""Execução do pipeline (download + run_pipeline) — thread local ou worker RQ."""

from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.config import OUTPUT_DIR, TEMP_DIR
from app.gui.gui_export import export_cortes_zip
from app.core.logging_setup import gui_pipeline_log_redirect
from app.pipelines.cortes.pipeline import run_pipeline
from app.web.hub import ProgressHub
from app.web.store import get_store
from app.download.ytdlp_download import (
    VideoSourceAttribution,
    collect_urls_from_lines,
    download_video,
)

_log = logging.getLogger("web.worker")


def _media_site_hint(url: str) -> str:
    u = url.strip().lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "tiktok.com" in u:
        return "TikTok"
    if "instagram.com" in u:
        return "Instagram"
    if "twitch.tv" in u:
        return "Twitch"
    return "site"


def _download_urls(
    urls: list[str],
    hub: ProgressHub,
    *,
    item_id: int | None = None,
) -> tuple[list[str], dict[str, VideoSourceAttribution]]:
    if not urls:
        return [], {}
    store = get_store() if item_id is not None else None
    n_u = len(urls)
    max_workers = max(1, min(3, n_u))
    hub.publish_log(f"A baixar {n_u} URL(s) (até {max_workers} em paralelo)…")
    source_by_path: dict[str, VideoSourceAttribution] = {}

    def _dl(idx_url: tuple[int, str]) -> tuple[int, str]:
        idx, u = idx_url
        site = _media_site_hint(u)
        hub.publish_log(f"[{idx + 1}/{n_u}] Download de {site}…")
        result = download_video(u, TEMP_DIR)
        path = result.path
        if result.attribution:
            hub.publish_log(f"[{idx + 1}/{n_u}] Canal: {result.attribution.channel}")
        hub.publish_log(f"[{idx + 1}/{n_u}] Concluído → {path}")
        return idx, path, result.attribution

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        ordered = sorted(pool.map(_dl, enumerate(urls)), key=lambda t: t[0])
    paths: list[str] = []
    for _i, path, attribution in ordered:
        paths.append(path)
        if attribution:
            source_by_path[str(Path(path).resolve())] = attribution
    if store and item_id is not None and paths:
        store.mark_running(item_id, Path(paths[0]).name)
    return paths, source_by_path


def _run_pipeline_core(
    *,
    videos: list[str],
    pipeline_kwargs: dict[str, Any],
    export_zip: bool,
    hub: ProgressHub,
    item_id: int | None = None,
    source_by_path: dict[str, VideoSourceAttribution] | None = None,
) -> list[str]:
    store = get_store() if item_id is not None else None

    def _on_progress(frac: float) -> None:
        hub.publish_progress(frac)
        if store and item_id is not None:
            store.update_progress(item_id, frac)

    hub.publish_log(
        f"Pipeline: {len(videos)} vídeo(s) — transcrição, momentos virais e clipes."
    )
    if source_by_path:
        pipeline_kwargs = {**pipeline_kwargs, "source_by_path": source_by_path}
    results = run_pipeline(video_path=videos, progress=_on_progress, **pipeline_kwargs)

    zip_note = ""
    if export_zip and results:
        zp = export_cortes_zip(results, OUTPUT_DIR)
        zip_note = f" Zip: {zp.name}."
        _log.info("Zip exportado: %s", zp)

    hub.mark_done(results, message=f"{len(results)} clipe(s) gerado(s).{zip_note}")
    if store and item_id is not None:
        store.mark_pipeline_done(item_id, results)
    return results


def run_playlist_item(item_id: int) -> None:
    """Processa um item persistido na playlist."""
    store = get_store()
    item = store.get(item_id)
    if not item:
        return

    hub = ProgressHub.get()
    hub.set_active_item(item_id, item.source)
    hub.mark_running(f"Playlist #{item_id}: preparando…")
    store.mark_running(item_id)

    class _LogBridge:
        def write(self, s: str) -> None:
            if s and s.strip():
                hub.publish_log(s.strip())

        def flush(self) -> None:
            pass

    pipeline_kwargs = store.get_pipeline_options(item_id)
    export_zip = bool(pipeline_kwargs.pop("export_zip", False))

    try:
        videos: list[str] = []
        source_by_path: dict[str, VideoSourceAttribution] | None = None
        if item.source_type == "file":
            p = Path(item.source)
            if not p.is_file():
                raise FileNotFoundError(f"Arquivo não encontrado: {item.source}")
            videos.append(str(p.resolve()))
        else:
            paths, source_by_path = _download_urls([item.source], hub, item_id=item_id)
            videos.extend(paths)

        if not videos:
            raise ValueError("Nenhum vídeo obtido para processar.")

        with gui_pipeline_log_redirect(_LogBridge()):
            _run_pipeline_core(
                videos=videos,
                pipeline_kwargs=pipeline_kwargs,
                export_zip=export_zip,
                hub=hub,
                item_id=item_id,
                source_by_path=source_by_path,
            )
    except Exception as exc:
        _log.exception("Playlist item %s falhou", item_id)
        err = str(exc) or type(exc).__name__
        store.mark_pipeline_error(item_id, err)
        hub.mark_error(err)
        traceback.print_exc()
    finally:
        hub.clear_active_item()
        hub.notify_playlist_update()


def run_job(
    *,
    local_paths: list[str],
    urls_text: str,
    pipeline_kwargs: dict[str, Any],
    export_zip: bool,
    playlist_item_id: int | None = None,
) -> None:
    """Job avulso (formulário) — bloqueante."""
    hub = ProgressHub.get()
    hub.mark_running("Preparando entrada…")
    videos: list[str] = list(local_paths)
    store = get_store() if playlist_item_id else None
    if store and playlist_item_id:
        store.mark_running(playlist_item_id)

    class _LogBridge:
        def write(self, s: str) -> None:
            if s and s.strip():
                hub.publish_log(s.strip())

        def flush(self) -> None:
            pass

    log_bridge = _LogBridge()

    source_by_path: dict[str, VideoSourceAttribution] | None = None
    try:
        urls = collect_urls_from_lines(urls_text)
        if urls:
            paths, source_by_path = _download_urls(urls, hub, item_id=playlist_item_id)
            videos.extend(paths)

        if not videos:
            hub.mark_error("Nenhum vídeo local nem URL válida informada.")
            if store and playlist_item_id:
                store.mark_pipeline_error(playlist_item_id, "Entrada vazia.")
            return

        with gui_pipeline_log_redirect(log_bridge):
            _run_pipeline_core(
                videos=videos,
                pipeline_kwargs=pipeline_kwargs,
                export_zip=export_zip,
                hub=hub,
                item_id=playlist_item_id,
                source_by_path=source_by_path,
            )
    except Exception as exc:
        _log.exception("Job falhou")
        err = str(exc) or type(exc).__name__
        hub.mark_error(err)
        if store and playlist_item_id:
            store.mark_pipeline_error(playlist_item_id, err)
        traceback.print_exc()


def save_uploaded_file(content: bytes, original_name: str) -> str:
    """Grava upload em TEMP_DIR e devolve caminho absoluto."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(original_name or "upload").stem or "upload"
    suffix = Path(original_name or "").suffix or ".mp4"
    dest = TEMP_DIR / f"web_{stem}_{uuid.uuid4().hex[:10]}{suffix}"
    dest.write_bytes(content)
    return str(dest.resolve())
