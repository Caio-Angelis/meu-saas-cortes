"""TTS local via Kokoro (GPU/CPU) — pt-BR sem Edge/Gemini."""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

import numpy as np

from app.core.config import FFMPEG_PATH, LOCAL_TTS_DEVICE, LOCAL_TTS_SPEED, LOCAL_TTS_VOICE_PT

_log = logging.getLogger(__name__)

_lock = threading.Lock()
_pipeline = None
_pipeline_device: str | None = None

KOKORO_SAMPLE_RATE = 24_000

# Vozes pt-BR (hexgrad/Kokoro-82M)
LOCAL_TTS_VOICES: tuple[tuple[str, str, bool], ...] = (
    ("pf_dora", "★ Dora — feminina suave pt-BR (Kokoro local)", True),
    ("pf_sara", "Sara — feminina clara pt-BR (Kokoro local)", False),
    ("pm_alex", "Alex — masculina pt-BR (Kokoro local)", False),
    ("pm_santa", "Santa — masculina grave pt-BR (Kokoro local)", False),
)


def local_tts_available() -> bool:
    """True se `kokoro` e `torch` estiverem instalados."""
    try:
        import torch  # noqa: F401
        from kokoro import KPipeline  # noqa: F401

        return True
    except ImportError:
        return False


def _resolve_device() -> str:
    dev = (LOCAL_TTS_DEVICE or "auto").strip().lower()
    if dev == "auto":
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    if dev not in ("cuda", "cpu"):
        raise ValueError(f"LOCAL_TTS_DEVICE inválido: {dev!r} (use auto, cuda ou cpu)")
    return dev


def _get_pipeline():
    global _pipeline, _pipeline_device
    device = _resolve_device()
    with _lock:
        if _pipeline is None or _pipeline_device != device:
            from kokoro import KPipeline

            _log.info("Carregando Kokoro TTS (pt-BR, device=%s)…", device)
            _pipeline = KPipeline(
                lang_code="p",
                repo_id="hexgrad/Kokoro-82M",
                device=device,
            )
            _pipeline_device = device
        return _pipeline


def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    proc = subprocess.run(
        [
            FFMPEG_PATH,
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"FFmpeg falhou ao converter WAV→MP3: {err[:500]}")


def local_tts_save_to_path(
    text: str,
    out_path: str | Path,
    voice: str | None = None,
) -> None:
    """Sintetiza texto em MP3 (ou WAV se a extensão for .wav)."""
    import soundfile as sf

    clean = (text or "").strip()
    if not clean:
        raise ValueError("Texto vazio para TTS local")

    voice_id = (voice or LOCAL_TTS_VOICE_PT or "pf_dora").strip()
    pipeline = _get_pipeline()
    speed = max(0.5, min(2.0, float(LOCAL_TTS_SPEED)))

    chunks: list[np.ndarray] = []
    for _graphemes, _phonemes, audio in pipeline(clean, voice=voice_id, speed=speed):
        if audio is None:
            continue
        chunks.append(np.asarray(audio, dtype=np.float32))

    if not chunks:
        raise RuntimeError("Kokoro não gerou áudio")

    audio_out = np.concatenate(chunks)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    if out.suffix.lower() == ".mp3":
        wav_tmp = out.with_suffix(".wav")
        sf.write(str(wav_tmp), audio_out, KOKORO_SAMPLE_RATE)
        try:
            _wav_to_mp3(wav_tmp, out)
        finally:
            wav_tmp.unlink(missing_ok=True)
    else:
        sf.write(str(out), audio_out, KOKORO_SAMPLE_RATE)

    if not out.is_file() or out.stat().st_size < 128:
        raise RuntimeError(f"TTS local gerou arquivo inválido: {out}")
