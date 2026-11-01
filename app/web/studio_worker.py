"""Workers web para os fluxos que antes eram acionados apenas pela GUI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.logging_setup import gui_pipeline_log_redirect
from app.pipelines.batalha.batalha_pipeline import run_batalha_pipeline_from_payload
from app.pipelines.historia.historia_pipeline import run_historia_pipeline
from app.pipelines.quiz.quiz_pipeline import run_quiz_pipeline
from app.publishing.youtube_uploader import upload_video_to_youtube
from app.publishing.youtube_workflow import YouTubeWorkflowStore, execute_youtube_workflow
from app.tts.tts_standalone import synthesize_tts_mp3
from app.web.hub import ProgressHub
from app.web.media import output_media_urls

_log = logging.getLogger("web.studio_worker")


class _LogBridge:
    """Destino compatível com ``print``, logging e ``Queue.put`` dos pipelines."""

    def __init__(self, hub: ProgressHub) -> None:
        self._hub = hub

    def _publish(self, value: object) -> None:
        text = str(value or "").strip()
        if not text:
            return
        for line in text.splitlines():
            clean = line.strip()
            if clean:
                self._hub.publish_log(clean)

    def write(self, value: str) -> None:
        self._publish(value)

    def put(self, value: object) -> None:
        self._publish(value)

    def flush(self) -> None:
        pass


def _progress(hub: ProgressHub, frac: float, message: str | None = None) -> None:
    hub.publish_progress(max(0.0, min(1.0, float(frac))), message)


def _youtube_job(
    payload: dict[str, Any],
    hub: ProgressHub,
    log_bridge: _LogBridge,
) -> tuple[list[str], str]:
    selected = list(payload.get("selected") or [])
    credentials_path = Path(str(payload.get("credentials_path") or "")).expanduser()
    token_path = Path(str(payload.get("token_path") or "token.json")).expanduser()
    made_for_kids = bool(payload.get("made_for_kids"))
    if not selected:
        raise ValueError("Nenhum par MP4/TXT foi selecionado.")
    if not credentials_path.is_file():
        raise FileNotFoundError(f"Credencial OAuth não encontrada: {credentials_path}")

    links: list[str] = []
    total = len(selected)
    for index, item in enumerate(selected):
        video_path = Path(str(item["video_path"]))
        caption_path = Path(str(item["caption_path"]))
        publish_at = str(item["publish_at"])
        name = video_path.name
        _progress(hub, index / total, f"YouTube · enviando {index + 1}/{total} · {name}")

        def report(fraction: float, *, current: int = index, job_name: str = name) -> None:
            overall = (current + max(0.0, min(1.0, fraction))) / total
            _progress(
                hub,
                overall,
                f"YouTube · enviando {current + 1}/{total} · {job_name}",
            )

        result = upload_video_to_youtube(
            video_path,
            caption_path,
            client_secrets_path=credentials_path,
            token_path=token_path,
            privacy_status="private",
            made_for_kids=made_for_kids,
            publish_at=publish_at,
            progress=report,
        )
        links.append(result.url)
        log_bridge.put(f"Agendado: {name} → {publish_at}")

    return [], f"{len(links)} vídeo(s) agendado(s) no YouTube.\n" + "\n".join(links)


def run_studio_job(job_id: str, payload: dict[str, Any]) -> None:
    """Roteia e executa um job de workspace em background."""
    hub = ProgressHub.get()
    job_type = str(payload.get("job_type") or "").strip().lower()
    labels = {
        "quiz": "Máquina de quizzes",
        "batalha": "Batalha 1v1",
        "historia": "História",
        "tts": "Voz / TTS",
        "youtube": "YouTube",
        "youtube_workflow": "YouTube · publicação individual",
    }
    label = labels.get(job_type, "Studio")
    bridge = _LogBridge(hub)
    outputs: list[str] = []
    final_message = f"{label} concluído."

    hub.mark_running(f"{label} · preparando…")
    try:
        with gui_pipeline_log_redirect(bridge):
            if job_type == "quiz":
                _progress(hub, 0.03, "Quiz · preparando perguntas…")
                result = run_quiz_pipeline(
                    {
                        "job_type": "quiz",
                        "theme": payload.get("theme"),
                        "count": payload.get("count", 5),
                        "timer_sec": payload.get("timer_sec", 5),
                        "tts_voice": payload.get("tts_voice"),
                        "difficulty": payload.get("difficulty", "variado"),
                        "cor_fundo": payload.get("cor_fundo", "#1A1A1A"),
                    },
                    log_queue=bridge,  # type: ignore[arg-type]
                    cancel_event=None,
                )
                outputs = [str(result.video_path)]
            elif job_type == "batalha":
                _progress(hub, 0.02, "Batalha · montando o duelo…")
                result = run_batalha_pipeline_from_payload(
                    {
                        "job_type": "batalha",
                        "theme": payload.get("theme"),
                        "modo": payload.get("modo", "tamanho"),
                        "tts_voice": payload.get("tts_voice"),
                    },
                    log_queue=bridge,  # type: ignore[arg-type]
                    cancel_event=None,
                    progress=lambda value: _progress(hub, value),
                )
                outputs = [str(result.video_path)]
            elif job_type == "historia":
                _progress(hub, 0.04, "História · dividindo em cenas…")
                result = run_historia_pipeline(
                    str(payload.get("story_text") or ""),
                    voice=str(payload.get("tts_voice") or ""),
                )
                outputs = [str(result.video_path)]
            elif job_type == "tts":
                _progress(hub, 0.08, "Voz · sintetizando locução…")
                outputs = [
                    synthesize_tts_mp3(
                        str(payload.get("text") or ""),
                        str(payload.get("tts_voice") or ""),
                    )
                ]
            elif job_type == "youtube":
                outputs, final_message = _youtube_job(payload, hub, bridge)
            elif job_type == "youtube_workflow":
                workflow_id = str(payload.get("workflow_id") or "").strip()
                if not workflow_id:
                    raise ValueError("Identificador de fluxo YouTube ausente.")
                store = YouTubeWorkflowStore()
                result = execute_youtube_workflow(
                    workflow_id,
                    store=store,
                    progress=lambda value, message: _progress(hub, value, message),
                )
                links = [video["url"] for video in result.get("videos", []) if video.get("url")]
                outputs = []
                final_message = (
                    "Uploads concluídos; confirme o vídeo relacionado no YouTube Studio."
                    if result.get("status") == "awaiting_studio"
                    else "Publicação YouTube concluída."
                )
                if links:
                    final_message += "\n" + "\n".join(links)
            else:
                raise ValueError(f"Tipo de job desconhecido: {job_type or 'vazio'}")

        _progress(hub, 1.0, final_message)
        if job_type != "youtube":
            final_message = f"{label} concluído."
        hub.mark_done(
            outputs,
            message=final_message,
            output_urls=output_media_urls(outputs),
        )
    except Exception as exc:
        _log.exception("Studio job %s falhou", job_id)
        message = str(exc).strip() or f"{type(exc).__name__}: falha no processamento."
        hub.mark_error(message)
