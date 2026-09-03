"""
Bot Telegram para acionar os geradores no PC local.

Requisitos: `TELEGRAM_BOT_TOKEN` e `TELEGRAM_ALLOWED_USER_ID` no `.env` (via `app.core.config`).

Execute na raiz do projeto:
    python telegram_bot.py
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import _venv_reexec

_venv_reexec.ensure_venv(__file__)

from app.core.linux_desktop_bootstrap import apply_linux_desktop_defaults

apply_linux_desktop_defaults()

import asyncio
import logging
import os
import queue
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parent
os.chdir(_ROOT)

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.core.config import (
    DOWNLOAD_MAX_WORKERS,
    EDGE_TTS_VOICE_PT,
    TELEGRAM_ALLOWED_USER_ID,
    TELEGRAM_BOT_TOKEN,
    TEMP_DIR,
)
from app.core.logging_setup import setup_logging
from app.pipelines.batalha.batalha_pipeline import (
    BATALHA_MODOS,
    normalize_batalha_modo,
    run_batalha_pipeline,
)
from app.pipelines.cortes.pipeline import run_pipeline
from app.pipelines.historia.historia_pipeline import run_historia_pipeline
import app.pipelines.quiz.quiz_pipeline as quiz_pipeline_mod
from app.pipelines.quiz.quiz_pipeline import (
    DEFAULT_QUESTION_COUNT,
    DEFAULT_TIMER_SEC,
    QuizPipelineResult,
    run_quiz_pipeline,
)
from app.download.ytdlp_download import (
    VideoSourceAttribution,
    collect_urls_from_lines,
    download_video,
    normalize_media_url,
    resolve_ytdlp_executable,
    search_youtube_top_by_views,
)
from app.tts.tts_standalone import synthesize_tts_mp3
from app.tts.tts_voices import default_voice_id
_log = logging.getLogger(__name__)

TELEGRAM_MAX_VIDEO_BYTES = 50 * 1024 * 1024
TELEGRAM_CAPTION_MAX_LEN = 1024
TELEGRAM_STATUS_MAX_LEN = 3900
_LOG_POLL_INTERVAL_SEC = 2.0
_HEARTBEAT_INTERVAL_SEC = 75.0

_HELP_TEXT = """\
🤖 Bot — Geradores de vídeo

Comandos:

/start ou /help — esta mensagem

/cortes
Gera clipes virais a partir de URL(s) ou arquivo(s) local(is).
Envie o comando e, na mesma mensagem (linhas abaixo), URLs ou caminhos:

/cortes
https://www.youtube.com/watch?v=...

/cortes
/home/caio/videos/podcast.mp4

Vários links ou arquivos: um por linha.
Ao terminar, cada MP4 é enviado com a legenda TikTok (.txt) recomendada.

/tema <assunto>
Busca no YouTube o vídeo longo mais visto sobre o assunto e gera cortes.
Ex.: /tema curiosidades sobre o espaço

/quiz <tema> [quantidade] [timer_sec]
Gera um vídeo quiz vertical.
Ex.: /quiz Geografia 2 3

Padrões quiz: {default_count} perguntas, timer {default_timer} s.

/batalha [tamanho|territorio|plinko] <tema>
Gera um duelo 1v1. Ex.: /batalha plinko Batman vs Superman

/historia <texto>
Gera um vídeo narrado com cenas IA. Requer o ComfyUI aberto.

/tts <texto>
Converte o texto em MP3 usando a voz padrão configurada.

⚠️ Apenas o usuário autorizado neste PC pode usar o bot.
Os renders podem levar vários minutos — mantenha o terminal aberto.
""".format(
    default_count=DEFAULT_QUESTION_COUNT,
    default_timer=int(DEFAULT_TIMER_SEC),
)

_BLOCK_TEXT = "⛔ Acesso negado. Este bot é privado."
_BUSY_TEXT = "⏳ Já há um job em processamento. Aguarde a conclusão antes de iniciar outro."

_job_lock = asyncio.Lock()


def _allowed_user_id() -> int:
    return TELEGRAM_ALLOWED_USER_ID


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    allowed = _allowed_user_id()
    if allowed <= 0:
        return False
    return user.id == allowed


async def _reply_unauthorized(update: Update) -> None:
    if update.message:
        await update.message.reply_text(_BLOCK_TEXT)


def _read_caption_txt(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        _log.warning("Não foi possível ler legenda %s: %s", path, e)
        return ""


def _caption_for_mp4(mp4: Path) -> str:
    return _read_caption_txt(mp4.with_suffix(".txt"))


def _command_body(text: str, command: str) -> str:
    return re.sub(
        rf"^/{re.escape(command)}(?:@\w+)?(?:\s+|$)",
        "",
        (text or "").strip(),
        count=1,
        flags=re.IGNORECASE,
    ).strip()


def _parse_quiz_args(text: str) -> tuple[str, int, float] | str:
    body = _command_body(text, "quiz")
    if not body:
        return "Informe o tema. Ex.: /quiz Geografia 5 5"

    parts = body.split()
    timer_sec = float(DEFAULT_TIMER_SEC)
    count = DEFAULT_QUESTION_COUNT

    if len(parts) >= 2:
        try:
            timer_sec = float(parts[-1])
            count = int(parts[-2])
            theme_parts = parts[:-2]
        except ValueError:
            try:
                count = int(parts[-1])
                theme_parts = parts[:-1]
            except ValueError:
                theme_parts = parts
        else:
            if not theme_parts:
                return "Informe o tema antes dos números. Ex.: /quiz Geografia 5 5"
    else:
        theme_parts = parts

    theme = " ".join(theme_parts).strip()
    if not theme:
        return "O tema não pode ficar vazio."

    count = max(1, min(10, int(count)))
    timer_sec = max(3.0, min(10.0, float(timer_sec)))
    return theme, count, timer_sec


def _parse_cortes_args(text: str) -> tuple[list[str], list[str]] | str:
    body = _command_body(text, "cortes")

    if not body:
        return (
            "Informe URL(s) ou caminho(s) de vídeo após /cortes "
            "(um por linha).\n\n"
            "Ex.:\n/cortes\nhttps://www.youtube.com/watch?v=...\n\n"
            "Ou:\n/cortes\n/home/caio/video.mp4"
        )

    urls = collect_urls_from_lines(body)
    locals_: list[str] = []
    seen: set[str] = set()

    for line in body.splitlines():
        line = line.strip()
        if not line or normalize_media_url(line):
            continue
        p = Path(line).expanduser()
        if p.is_file():
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                locals_.append(key)

    for token in body.split():
        if normalize_media_url(token):
            continue
        p = Path(token).expanduser()
        if p.is_file():
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                locals_.append(key)

    if not urls and not locals_:
        return (
            "Nenhuma URL válida nem arquivo encontrado.\n"
            "Use links http(s) ou caminhos absolutos de arquivos neste PC."
        )

    if urls and resolve_ytdlp_executable() is None:
        return (
            "yt-dlp não encontrado. Instale na venv:\n"
            ".venv/bin/python -m pip install yt-dlp\n"
            "Ou defina YTDLP_PATH no .env."
        )

    missing = [p for p in locals_ if not Path(p).is_file()]
    if missing:
        return f"Arquivo não encontrado: {missing[0]}"

    return locals_, urls


def _parse_batalha_args(text: str) -> tuple[str, str] | str:
    body = _command_body(text, "batalha")
    if not body:
        return "Informe o tema. Ex.: /batalha plinko Batman vs Superman"

    first, *rest = body.split(maxsplit=1)
    modo = normalize_batalha_modo(first)
    if first.lower() in (*BATALHA_MODOS, "agar", "size", "territory", "race", "corrida"):
        if not rest:
            return "Informe o tema depois do modo. Ex.: /batalha plinko Batman vs Superman"
        return rest[0], modo
    return body, normalize_batalha_modo(None)


@contextmanager
def _telegram_quiz_tts_serial():
    """Menos paralelismo no Edge-TTS — evita 403 e filas longas no bot remoto."""
    old = quiz_pipeline_mod.EDGE_TTS_MAX_CONCURRENT
    quiz_pipeline_mod.EDGE_TTS_MAX_CONCURRENT = 1
    try:
        yield
    finally:
        quiz_pipeline_mod.EDGE_TTS_MAX_CONCURRENT = old


def _run_quiz_in_thread(
    payload: dict[str, Any],
    log_queue: queue.Queue[Any],
) -> QuizPipelineResult:
    with _telegram_quiz_tts_serial():
        return run_quiz_pipeline(payload, log_queue=log_queue, cancel_event=None)


def _strip_log_line(chunk: Any) -> str | None:
    if not isinstance(chunk, str):
        return None
    line = re.sub(r"\s+", " ", chunk.strip())
    return line or None


def _status_from_logs(header: str, lines: list[str], *, progress_pct: int | None = None) -> str:
    tail = lines[-4:] if lines else []
    body = "\n".join(tail) if tail else "A processar…"
    if progress_pct is not None:
        body = f"Progresso: {progress_pct}%\n{body}"
    text = f"{header}\n\n{body}"
    if len(text) > TELEGRAM_STATUS_MAX_LEN:
        text = text[-TELEGRAM_STATUS_MAX_LEN:]
    return text


async def _poll_log_queue_to_status(
    log_queue: queue.Queue[Any],
    status_message: Any,
    *,
    header: str,
    stop_event: asyncio.Event,
    progress_holder: list[int | None] | None = None,
) -> None:
    """Atualiza a mensagem de status com logs do pipeline (quiz/cortes)."""
    lines: list[str] = []
    last_edit = 0.0
    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()

    while not stop_event.is_set():
        drained = False
        try:
            while True:
                chunk = log_queue.get_nowait()
                line = _strip_log_line(chunk)
                if line:
                    lines.append(line)
                    if len(lines) > 12:
                        lines = lines[-12:]
                drained = True
        except queue.Empty:
            pass

        now = loop.time()
        pct = progress_holder[0] if progress_holder else None
        should_edit = drained and (now - last_edit >= _LOG_POLL_INTERVAL_SEC)
        should_heartbeat = now - last_heartbeat >= _HEARTBEAT_INTERVAL_SEC

        if should_edit or should_heartbeat:
            try:
                await status_message.edit_text(
                    _status_from_logs(header, lines, progress_pct=pct)
                )
                last_edit = now
                if should_heartbeat:
                    last_heartbeat = now
            except Exception as e:
                _log.debug("Não foi possível editar status Telegram: %s", e)

        await asyncio.sleep(0.5)


def _run_cortes_in_thread(
    local_paths: list[str],
    urls: list[str],
    log_queue: queue.Queue[Any],
    progress_cb: Any | None = None,
    search_theme: str = "",
) -> list[str]:
    """Espelha `gui._run_cortes_job_payload` (download + run_pipeline)."""
    videos: list[str] = list(local_paths)
    source_by_path: dict[str, VideoSourceAttribution] = {}

    if search_theme:
        log_queue.put_nowait(f"Buscando «{search_theme}» no YouTube…")
        hit = search_youtube_top_by_views(search_theme)
        urls = [hit.url]
        log_queue.put_nowait(
            f"Escolhido: {hit.title} · {hit.view_count:,} views · {hit.duration_sec / 60:.0f} min"
        )

    if urls:
        n_u = len(urls)
        max_workers = max(1, min(DOWNLOAD_MAX_WORKERS, n_u))
        _log.info("A baixar %s URL(s) com yt-dlp (workers=%s)", n_u, max_workers)

        def _dl(idx_url: tuple[int, str]) -> tuple[int, str, object]:
            idx, u = idx_url
            result = download_video(u, TEMP_DIR)
            return idx, u, result

        with ThreadPoolExecutor(max_workers=max_workers) as dl_pool:
            ordered = sorted(dl_pool.map(_dl, enumerate(urls)), key=lambda t: t[0])
        for _i, _u, result in ordered:
            path = result.path
            videos.append(path)
            if result.attribution:
                source_by_path[str(Path(path).resolve())] = result.attribution

    if not videos:
        return []

    pipeline_kw: dict[str, Any] = {
        "target_language": "pt",
        "posicao": "bottom",
        "fonte": "Arial",
        "cor_letra": "#FFFF00",
        "cor_fundo": "#000000",
        "opacidade": 75,
        "dub_to": None,
        "tts_voice": None,
    }
    if urls:
        pipeline_kw["source_by_path"] = source_by_path

    def _on_progress(frac: float) -> None:
        if progress_cb is not None:
            progress_cb(frac)
            return
        pct = int(max(0.0, min(100, round(frac * 100))))
        try:
            log_queue.put_nowait(f"Cortes: {pct}% concluído")
        except queue.Full:
            pass

    return run_pipeline(video_path=videos, progress=_on_progress, **pipeline_kw)


async def _report_error(
    status_message: Any,
    message: Any,
    label: str,
    exc: BaseException,
) -> None:
    err_text = (
        f"❌ Erro em {label}:\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"{traceback.format_exc()[-3500:]}"
    )
    if len(err_text) > 4000:
        err_text = err_text[:4000] + "\n… (traceback truncado)"
    try:
        await status_message.edit_text(err_text)
    except Exception:
        await message.reply_text(
            f"❌ Erro em {label}: {type(exc).__name__}: {exc}\n\n"
            f"{traceback.format_exc()[-2000:]}"
        )


async def _send_videos_with_captions(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    video_paths: list[Path],
    *,
    status_message: Any | None = None,
    job_label: str = "job",
) -> tuple[int, int]:
    """
    Envia cada MP4 com legenda do .txt ao lado.
    Retorna (enviados, ignorados_por_tamanho_ou_erro).
    """
    if not video_paths:
        if status_message:
            await status_message.edit_text(f"⚠️ {job_label}: nenhum vídeo gerado.")
        return 0, 0

    sent = 0
    skipped = 0
    total = len(video_paths)

    if status_message:
        await status_message.edit_text(
            f"✅ {job_label} concluído. Enviando {total} vídeo(s) com legenda…"
        )

    for i, mp4 in enumerate(video_paths, start=1):
        if not mp4.is_file():
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ [{i}/{total}] Arquivo não encontrado:\n{mp4}",
            )
            skipped += 1
            continue

        caption_text = _caption_for_mp4(mp4)
        tg_caption = caption_text[:TELEGRAM_CAPTION_MAX_LEN] if caption_text else None
        size = mp4.stat().st_size

        if size > TELEGRAM_MAX_VIDEO_BYTES:
            skipped += 1
            msg = (
                f"📁 [{i}/{total}] {mp4.name} — {size / (1024 * 1024):.1f} MB "
                f"(acima de 50 MB no Telegram)\n"
                f"Caminho: {mp4.resolve()}"
            )
            await context.bot.send_message(chat_id=chat_id, text=msg)
            if caption_text:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📝 Legenda TikTok ({mp4.name}):\n\n"
                    f"{caption_text[:TELEGRAM_CAPTION_MAX_LEN]}",
                )
            continue

        try:
            with mp4.open("rb") as video_file:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=tg_caption,
                    supports_streaming=True,
                )
            sent += 1
        except Exception as e:
            skipped += 1
            _log.exception("Falha ao enviar %s", mp4)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ [{i}/{total}] {mp4.name} gerado, envio falhou: "
                    f"{type(e).__name__}: {e}\n{mp4.resolve()}"
                ),
            )
            if caption_text:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📝 Legenda TikTok:\n\n{caption_text[:TELEGRAM_CAPTION_MAX_LEN]}",
                )

        if i < total:
            await asyncio.sleep(0.4)

    if status_message:
        await status_message.edit_text(
            f"🏁 {job_label}: {sent} vídeo(s) enviado(s), {skipped} aviso(s)/falha(s)."
        )

    return sent, skipped


async def _run_single_output_job(
    message: Any,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    label: str,
    header: str,
    worker: Callable[[queue.Queue[Any]], str | Path],
    audio: bool = False,
) -> None:
    if _job_lock.locked():
        await message.reply_text(_BUSY_TEXT)
        return

    async with _job_lock:
        status = await message.reply_text(header)
        log_queue: queue.Queue[Any] = queue.Queue()
        stop_poll = asyncio.Event()
        poll_task = asyncio.create_task(
            _poll_log_queue_to_status(log_queue, status, header=header, stop_event=stop_poll)
        )
        try:
            output = Path(await asyncio.to_thread(worker, log_queue))
        except Exception as e:
            _log.exception("Falha em %s via Telegram", label)
            await _report_error(status, message, label, e)
            return
        finally:
            stop_poll.set()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        if not audio:
            await _send_videos_with_captions(
                context, message.chat_id, [output], status_message=status, job_label=label
            )
            return

        if not output.is_file() or output.stat().st_size > TELEGRAM_MAX_VIDEO_BYTES:
            await status.edit_text(f"📁 {label} concluído: {output.resolve()}")
            return
        await status.edit_text(f"✅ {label} concluído. Enviando MP3…")
        try:
            with output.open("rb") as audio_file:
                await context.bot.send_audio(chat_id=message.chat_id, audio=audio_file)
        except Exception as e:
            await status.edit_text(
                f"⚠️ {label} concluído, mas o envio falhou: {e}\n{output.resolve()}"
            )
        else:
            await status.edit_text(f"🏁 {label}: MP3 enviado.")


async def _require_authorized_message(
    update: Update,
) -> Any | None:
    if not _is_authorized(update):
        await _reply_unauthorized(update)
        return None
    return update.message


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _require_authorized_message(update)
    if message:
        await message.reply_text(_HELP_TEXT)


async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _require_authorized_message(update)
    if message is None:
        return

    parsed = _parse_quiz_args(message.text or "")
    if isinstance(parsed, str):
        await message.reply_text(parsed)
        return

    theme, count, timer_sec = parsed

    if _job_lock.locked():
        await message.reply_text(_BUSY_TEXT)
        return

    async with _job_lock:
        header = (
            f"⏳ Quiz «{theme}» — {count} perguntas, timer {timer_sec}s\n"
            "Pode levar 1–3 min. Atualizo esta mensagem com o progresso."
        )
        status = await message.reply_text(header)
        log_queue: queue.Queue[Any] = queue.Queue()
        stop_poll = asyncio.Event()
        poll_task = asyncio.create_task(
            _poll_log_queue_to_status(
                log_queue,
                status,
                header=header,
                stop_event=stop_poll,
            )
        )
        payload = {
            "job_type": "quiz",
            "theme": theme,
            "count": count,
            "timer_sec": timer_sec,
            "tts_voice": EDGE_TTS_VOICE_PT,
        }

        try:
            result = await asyncio.to_thread(
                _run_quiz_in_thread,
                payload,
                log_queue,
            )
        except Exception as e:
            _log.exception("Falha no quiz via Telegram (tema=%r)", theme)
            await _report_error(status, message, f"quiz «{theme}»", e)
            return
        finally:
            stop_poll.set()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        await _send_videos_with_captions(
            context,
            message.chat_id,
            [Path(result.video_path)],
            status_message=status,
            job_label=f"Quiz «{theme}»",
        )


async def cmd_cortes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _require_authorized_message(update)
    if message is None:
        return

    text = message.text or ""
    is_theme = bool(re.match(r"^/tema(?:@\w+)?(?:\s+|$)", text.strip(), re.IGNORECASE))
    search_theme = _command_body(text, "tema") if is_theme else ""
    if is_theme:
        if not search_theme:
            await message.reply_text("Informe o tema. Ex.: /tema curiosidades sobre o espaço")
            return
        local_paths, urls = [], []
    else:
        parsed = _parse_cortes_args(text)
        if isinstance(parsed, str):
            await message.reply_text(parsed)
            return
        local_paths, urls = parsed

    if _job_lock.locked():
        await message.reply_text(_BUSY_TEXT)
        return

    n_local = len(local_paths)
    n_url = len(urls)
    async with _job_lock:
        source = f"tema «{search_theme}»" if search_theme else f"{n_local} arquivo(s), {n_url} URL(s)"
        header = (
            f"⏳ Cortes virais — {source}\n"
            "Download + transcrição + clipes podem levar vários minutos. Progresso abaixo."
        )
        status = await message.reply_text(header)
        log_queue: queue.Queue[Any] = queue.Queue()
        progress_pct: list[int | None] = [None]
        stop_poll = asyncio.Event()
        poll_task = asyncio.create_task(
            _poll_log_queue_to_status(
                log_queue,
                status,
                header=header,
                stop_event=stop_poll,
                progress_holder=progress_pct,
            )
        )

        def _cortes_progress(frac: float) -> None:
            progress_pct[0] = int(max(0.0, min(100, round(frac * 100))))
            try:
                log_queue.put_nowait(f"Cortes: {progress_pct[0]}%")
            except queue.Full:
                pass

        try:
            outputs = await asyncio.to_thread(
                _run_cortes_in_thread,
                local_paths,
                urls,
                log_queue,
                _cortes_progress,
                search_theme,
            )
        except Exception as e:
            _log.exception("Falha nos cortes via Telegram")
            await _report_error(status, message, "cortes virais", e)
            return
        finally:
            stop_poll.set()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        mp4s = [Path(p) for p in outputs if str(p).lower().endswith(".mp4")]
        if not mp4s:
            mp4s = [Path(p) for p in outputs]

        await _send_videos_with_captions(
            context,
            message.chat_id,
            mp4s,
            status_message=status,
            job_label="Cortes virais",
        )


async def cmd_batalha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _require_authorized_message(update)
    if message is None:
        return
    parsed = _parse_batalha_args(message.text or "")
    if isinstance(parsed, str):
        await message.reply_text(parsed)
        return
    theme, modo = parsed

    def worker(log_queue: queue.Queue[Any]) -> Path:
        return run_batalha_pipeline(
            theme,
            modo=modo,
            tts_voice=default_voice_id(),
            log_queue=log_queue,
        ).video_path

    await _run_single_output_job(
        message,
        context,
        label=f"Batalha «{theme}»",
        header=f"⏳ Batalha «{theme}» — modo {modo}. Pode levar vários minutos.",
        worker=worker,
    )


async def cmd_historia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _require_authorized_message(update)
    if message is None:
        return
    story = _command_body(message.text or "", "historia")
    if not story:
        await message.reply_text(
            "Informe o texto. Ex.: /historia Era uma vez uma cidade sem memórias..."
        )
        return

    def worker(log_queue: queue.Queue[Any]) -> Path:
        log_queue.put_nowait("Groq → cenas → TTS + ComfyUI → FFmpeg…")
        return run_historia_pipeline(story, voice=default_voice_id()).video_path

    await _run_single_output_job(
        message,
        context,
        label="História",
        header="⏳ História — gerando cenas e narração. Mantenha o ComfyUI aberto.",
        worker=worker,
    )


async def cmd_tts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _require_authorized_message(update)
    if message is None:
        return
    content = _command_body(message.text or "", "tts")
    if not content:
        await message.reply_text("Informe o texto. Ex.: /tts Texto da locução")
        return

    def worker(log_queue: queue.Queue[Any]) -> str:
        log_queue.put_nowait("Sintetizando locução com a voz padrão…")
        return synthesize_tts_mp3(content, default_voice_id())

    await _run_single_output_job(
        message,
        context,
        label="Text-to-Speech",
        header="⏳ Text-to-Speech — gerando MP3…",
        worker=worker,
        audio=True,
    )


async def on_unauthorized_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_authorized(update):
        return
    await _reply_unauthorized(update)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    _log.error("Exceção no handler do Telegram", exc_info=context.error)
    err = context.error
    if update is None or not isinstance(update, Update):
        return
    message = update.effective_message
    if message is None or not _is_authorized(update):
        return
    summary = f"{type(err).__name__}: {err}" if err else "erro desconhecido"
    try:
        await message.reply_text(f"❌ Erro interno do bot: {summary}")
    except Exception:
        _log.exception("Não foi possível notificar o usuário sobre o erro")


def _validate_config() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Defina TELEGRAM_BOT_TOKEN no .env (token do @BotFather).")
    if _allowed_user_id() <= 0:
        raise SystemExit(
            "Defina TELEGRAM_ALLOWED_USER_ID no .env (seu ID numérico do Telegram)."
        )


async def _register_commands(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("cortes", "Gerar cortes de URLs ou arquivos locais"),
            BotCommand("tema", "Buscar um tema no YouTube e gerar cortes"),
            BotCommand("quiz", "Gerar vídeo quiz"),
            BotCommand("batalha", "Gerar batalha 1v1"),
            BotCommand("historia", "Gerar história narrada com cenas IA"),
            BotCommand("tts", "Converter texto em MP3"),
            BotCommand("help", "Mostrar exemplos de todos os comandos"),
        ]
    )


def main() -> None:
    setup_logging(gui_quiet=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _validate_config()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_register_commands).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler(["cortes", "tema"], cmd_cortes))
    app.add_handler(CommandHandler("batalha", cmd_batalha))
    app.add_handler(CommandHandler("historia", cmd_historia))
    app.add_handler(CommandHandler("tts", cmd_tts))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_unauthorized_message),
    )
    app.add_error_handler(global_error_handler)

    _log.info(
        "Bot Telegram a iniciar (usuário autorizado: %s)",
        _allowed_user_id(),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
