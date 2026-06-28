"""Síntese de voz via Gemini API (TTS nativo — vozes realistas, ex. Achernar)."""

from __future__ import annotations

import base64
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import (
    EDGE_TTS_VOICE_PT,
    FFMPEG_PATH,
    GEMINI_API_KEY,
    GEMINI_HTTP_TIMEOUT_SEC,
    GEMINI_TTS_MODEL,
)

_log = logging.getLogger(__name__)

_GEMINI_TTS_PCM_RATE = 24000
_GEMINI_TTS_PCM_CHANNELS = 1
_GEMINI_TTS_MAX_ATTEMPTS = 3
_GEMINI_TTS_FALLBACK_MODELS = (
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-3.1-flash-tts-preview",
)


def gemini_tts_available() -> bool:
    return bool((GEMINI_API_KEY or "").strip())


def _part_inline_audio(part: dict[str, Any]) -> bytes | None:
    """Extrai PCM de um part com inlineData / inline_data."""
    inline = part.get("inlineData") or part.get("inline_data")
    if not isinstance(inline, dict):
        return None
    data_b64 = inline.get("data")
    if not data_b64:
        return None
    mime = str(inline.get("mimeType") or inline.get("mime_type") or "").lower()
    if mime and not any(tok in mime for tok in ("audio", "pcm", "l16", "wav")):
        return None
    try:
        pcm = base64.b64decode(data_b64)
    except Exception:
        return None
    return pcm if len(pcm) >= 256 else None


def _extract_pcm_bytes(response_json: dict[str, Any]) -> bytes:
    """
    Varre candidatos e parts — o áudio pode não estar em parts[0]
    (ex.: texto + áudio, ou snake_case inline_data).
    """
    candidates = response_json.get("candidates") or []
    if not candidates:
        feedback = response_json.get("promptFeedback") or response_json.get("prompt_feedback")
        raise RuntimeError(
            f"Gemini TTS sem candidatos na resposta. promptFeedback={feedback!r}"
        )

    text_snippets: list[str] = []
    finish_reasons: list[str] = []

    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        reason = cand.get("finishReason") or cand.get("finish_reason")
        if reason:
            finish_reasons.append(str(reason))
        content = cand.get("content") or {}
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if "text" in part and part.get("text"):
                text_snippets.append(str(part["text"])[:120])
            pcm = _part_inline_audio(part)
            if pcm is not None:
                return pcm

    hint = ""
    if text_snippets:
        hint = f" O modelo devolveu texto em vez de áudio (ex.: {text_snippets[0]!r}…)."
    if finish_reasons:
        hint += f" finishReason={finish_reasons}."
    raise RuntimeError(
        "Resposta Gemini TTS sem áudio inline."
        + hint
        + " Use um modelo TTS (ex. gemini-2.5-flash-preview-tts) ou voz Edge na GUI."
    )


def _models_to_try() -> list[str]:
    primary = (GEMINI_TTS_MODEL or "").strip()
    seen: set[str] = set()
    ordered: list[str] = []
    for m in [primary, *_GEMINI_TTS_FALLBACK_MODELS]:
        m = (m or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        ordered.append(m)
    return ordered or ["gemini-2.5-flash-preview-tts"]


def _request_gemini_pcm_once(text: str, voice_name: str, model: str) -> bytes:
    api_key = (GEMINI_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY não configurada. Defina no .env para usar vozes Gemini (Achernar, etc.)."
        )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": (text or "").strip()}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_name},
                },
            },
        },
    }
    timeout = max(30.0, float(GEMINI_HTTP_TIMEOUT_SEC))
    try:
        r = httpx.post(url, params={"key": api_key}, json=payload, timeout=timeout)
    except httpx.TimeoutException as e:
        raise RuntimeError(f"Gemini TTS excedeu {timeout:.0f}s (modelo {model}).") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha de rede ao chamar Gemini TTS: {e}") from e

    if r.status_code != 200:
        detail = (r.text or "")[:500]
        raise RuntimeError(f"Gemini TTS HTTP {r.status_code} ({model}): {detail}")

    try:
        body = r.json()
    except ValueError as e:
        raise RuntimeError(f"Gemini TTS resposta JSON inválida ({model}).") from e

    return _extract_pcm_bytes(body)


def _request_gemini_pcm(text: str, voice_name: str) -> bytes:
    """Chama generateContent com retentativas e modelos TTS alternativos."""
    last_err: Exception | None = None
    models = _models_to_try()

    for attempt in range(_GEMINI_TTS_MAX_ATTEMPTS):
        for model in models:
            try:
                pcm = _request_gemini_pcm_once(text, voice_name, model)
                if attempt > 0 or model != models[0]:
                    _log.info(
                        "Gemini TTS OK após tentativa %s com modelo %s",
                        attempt + 1,
                        model,
                    )
                return pcm
            except RuntimeError as e:
                last_err = e
                msg = str(e).lower()
                retryable = (
                    "sem áudio" in msg
                    or "sem candidatos" in msg
                    or "http 500" in msg
                    or "http 503" in msg
                )
                if retryable:
                    _log.warning("Gemini TTS (%s): %s", model, e)
                else:
                    raise

        if attempt < _GEMINI_TTS_MAX_ATTEMPTS - 1:
            time.sleep(0.6 * (attempt + 1))

    assert last_err is not None
    raise last_err


def _pcm_to_mp3(pcm: bytes, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pcm_path = out_path.with_suffix(".pcm")
    try:
        pcm_path.write_bytes(pcm)
        cmd = [
            FFMPEG_PATH,
            "-f",
            "s16le",
            "-ar",
            str(_GEMINI_TTS_PCM_RATE),
            "-ac",
            str(_GEMINI_TTS_PCM_CHANNELS),
            "-i",
            str(pcm_path),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            "-y",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:400]
            raise RuntimeError(f"FFmpeg não converteu PCM→MP3: {err}")
    finally:
        try:
            pcm_path.unlink(missing_ok=True)
        except OSError:
            pass


def gemini_tts_save_to_path(text: str, out_path: str | Path, voice_name: str) -> None:
    """Gera MP3 com voz pré-construída do Gemini (ex.: Achernar, Leda)."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("empty TTS text")
    voice = (voice_name or "").strip()
    if not voice:
        raise ValueError("empty Gemini voice name")
    out = Path(out_path)
    if out.suffix.lower() != ".mp3":
        out = out.with_suffix(".mp3")
    pcm = _request_gemini_pcm(clean, voice)
    _pcm_to_mp3(pcm, out)
    if not out.is_file() or out.stat().st_size < 128:
        raise RuntimeError(f"Arquivo MP3 Gemini inválido: {out}")


def edge_fallback_voice_for_gemini() -> str:
    """Voz Edge usada quando Gemini TTS falha."""
    return (EDGE_TTS_VOICE_PT or "pt-BR-ThalitaMultilingualNeural").strip()
