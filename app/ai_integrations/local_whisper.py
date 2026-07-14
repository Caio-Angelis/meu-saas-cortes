"""Transcrição local via faster-whisper (CTranslate2) na GPU."""
from __future__ import annotations

import logging
import threading

from app.core.config import LOCAL_WHISPER_COMPUTE, LOCAL_WHISPER_MODEL

_log = logging.getLogger("local_whisper")
_model = None
_lock = threading.Lock()


def local_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _log.info("Carregando faster-whisper (%s, %s) na GPU…",
                      LOCAL_WHISPER_MODEL, LOCAL_WHISPER_COMPUTE)
            _model = WhisperModel(
                LOCAL_WHISPER_MODEL, device="cuda", compute_type=LOCAL_WHISPER_COMPUTE,
            )
        return _model


def transcribe_local(audio_path: str, language: str | None = None) -> list[dict]:
    """Retorna [{start, end, text, words:[{start,end,word}]}], mesma forma do Groq + words."""
    model = _get_model()
    segments, _info = model.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )
    out: list[dict] = []
    for s in segments:
        words = []
        for w in (s.words or []):
            words.append({"start": float(w.start), "end": float(w.end),
                          "word": str(w.word)})
        out.append({
            "start": float(s.start),
            "end": float(s.end),
            "text": str(s.text).strip(),
            "words": words,
        })
    return out
