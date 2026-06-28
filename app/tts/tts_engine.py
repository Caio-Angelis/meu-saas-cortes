"""Despacho unificado TTS local / Edge-TTS / Gemini TTS."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.tts.gemini_tts import edge_fallback_voice_for_gemini, gemini_tts_save_to_path
from app.tts.local_tts import local_tts_available, local_tts_save_to_path
from app.tts.tts_voices import resolve_voice
from app.video_processing.tts_dubber import edge_tts_save_to_path

_log = logging.getLogger(__name__)


async def synthesize_speech_to_path(
    text: str,
    out_path: str | Path,
    voice: str,
    *,
    allow_edge_fallback: bool = True,
) -> None:
    """
    Grava locução no caminho indicado (.mp3) conforme o provedor da voz.

    Provedores: ``local`` (Kokoro GPU/CPU), ``gemini``, ``edge``.
    Se Gemini falhar (ex. resposta sem áudio), usa Edge-TTS com voz pt-BR padrão
    quando ``allow_edge_fallback=True``.
    """
    opt = resolve_voice(voice)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    if opt.provider == "local":
        await asyncio.to_thread(local_tts_save_to_path, text, out, opt.engine_voice)
        return

    if opt.provider == "gemini":
        try:
            await asyncio.to_thread(gemini_tts_save_to_path, text, out, opt.engine_voice)
            return
        except Exception as e:
            if not allow_edge_fallback:
                raise
            fallback = edge_fallback_voice_for_gemini()
            _log.warning(
                "Gemini TTS falhou (%s); a usar Edge-TTS (%s).",
                e,
                fallback,
            )
            await edge_tts_save_to_path(text, out, fallback)
            return

    await edge_tts_save_to_path(text, out, opt.engine_voice)
