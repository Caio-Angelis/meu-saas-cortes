"""Geração de MP3 avulso via Edge-TTS ou Gemini TTS (aba Text-to-Speech da GUI)."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app.core.config import FFMPEG_PATH, OUTPUT_DIR, TEMP_DIR
from app.tts.tts_engine import synthesize_speech_to_path
from app.tts.tts_voices import resolve_voice

TTS_OUTPUT_DIR: Path = OUTPUT_DIR / "tts"
TTS_PREVIEW_DIR: Path = TEMP_DIR / "tts_preview"

PREVIEW_MAX_CHARS: int = 200
DEFAULT_PREVIEW_PHRASE: str = "Olá! Esta é uma amostra da voz selecionada."


def normalize_tts_text(text: str) -> str:
    """Texto não vazio para síntese."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("Informe um texto para sintetizar.")
    return clean


def preview_sample_text(text: str) -> str:
    """
    Trecho curto para pré-ouvir a voz sem gerar o MP3 completo.
    Usa o início do texto do utilizador; se for longo, corta em limite de palavras.
    """
    try:
        clean = normalize_tts_text(text)
    except ValueError:
        return DEFAULT_PREVIEW_PHRASE
    if len(clean) <= PREVIEW_MAX_CHARS:
        return clean
    cut = clean[:PREVIEW_MAX_CHARS]
    last_space = cut.rfind(" ")
    if last_space > 40:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


def slug_output_stem(text: str, *, max_len: int = 48) -> str:
    """Slug seguro para nome de arquivo a partir do texto."""
    base = re.sub(r"\s+", " ", (text or "").strip())[:120]
    slug = re.sub(r"[^\w\-]+", "_", base, flags=re.UNICODE).strip("_")
    if not slug:
        slug = "locucao"
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug or "locucao"


def build_output_path(text: str) -> Path:
    """Caminho único em `resultados/tts/` para um novo MP3."""
    TTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return TTS_OUTPUT_DIR / f"{ts}_{slug_output_stem(text)}.mp3"


def _validate_mp3(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo MP3 não foi criado: {path}")
    if path.stat().st_size < 128:
        raise RuntimeError(f"Arquivo MP3 vazio ou inválido: {path}")


async def synthesize_tts_mp3_async(text: str, voice: str, out_path: Path) -> Path:
    """Síntese → MP3 no caminho indicado (Edge ou Gemini)."""
    clean = normalize_tts_text(text)
    voice_id = resolve_voice(voice).voice_id
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await synthesize_speech_to_path(clean, out_path, voice_id)
    _validate_mp3(out_path)
    return out_path


def synthesize_tts_mp3(
    text: str,
    voice: str,
    *,
    out_path: Path | str | None = None,
) -> str:
    """Wrapper síncrono para a GUI e scripts."""
    target = Path(out_path) if out_path is not None else build_output_path(text)
    asyncio.run(synthesize_tts_mp3_async(text, voice, target))
    return str(target.resolve())


async def synthesize_tts_preview_async(text: str, voice: str) -> Path:
    """Gera MP3 temporário com amostra curta da voz."""
    sample = preview_sample_text(text)
    opt = resolve_voice(voice)
    TTS_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "_", opt.voice_id)
    out = TTS_PREVIEW_DIR / f"preview_{safe}.mp3"
    if out.is_file():
        try:
            out.unlink()
        except OSError:
            pass
    await synthesize_speech_to_path(sample, out, opt.voice_id)
    _validate_mp3(out)
    return out


def synthesize_tts_preview(text: str, voice: str) -> str:
    """Wrapper síncrono; devolve caminho do MP3 de pré-visualização."""
    path = asyncio.run(synthesize_tts_preview_async(text, voice))
    return str(path.resolve())


def _resolve_ffplay() -> str | None:
    ffplay = shutil.which("ffplay")
    if ffplay:
        return ffplay
    ffmpeg = Path(FFMPEG_PATH)
    candidate = ffmpeg.with_name("ffplay")
    if candidate.is_file():
        return str(candidate)
    return None


def play_audio_file(path: str | Path) -> subprocess.Popen[bytes] | None:
    """
    Reproduz MP3 sem bloquear a UI.
    Preferência: ffplay -nodisp -autoexit; senão abre com o leitor padrão do SO.
    """
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Áudio não encontrado: {p}")

    ffplay = _resolve_ffplay()
    if ffplay:
        return subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(p)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if sys.platform == "win32":
        os_cmd = ["start", "/wait", "", str(p)]
        return subprocess.Popen(os_cmd, shell=True)
    if sys.platform == "darwin":
        return subprocess.Popen(["open", str(p)])
    return subprocess.Popen(["xdg-open", str(p)])
