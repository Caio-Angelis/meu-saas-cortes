"""Áudio: extrai uma faixa mono leve para transcrição.

Este módulo existe para padronizar a extração de áudio (codec, sample rate, canais),
mantendo o pipeline determinístico e fácil de debugar.
"""

import os
import subprocess
from pathlib import Path

from app.core.config import FFMPEG_PATH
from app.core.subprocess_utils import run_cancelable


def extract_audio(video_path: str, output_path: str) -> str:
    """Extrai áudio do vídeo para um arquivo (ex.: .mp3) pronto para transcrição."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    run_cancelable(
        [
            FFMPEG_PATH,
            "-i",
            video_path,
            "-vn",  # sem vídeo
            "-acodec",
            "libmp3lame",
            "-ar",
            "16000",  # 16kHz costuma ser suficiente para fala
            "-ac",
            "1",  # mono
            "-b:a",
            "32k",  # baixo bitrate -> rápido e leve
            output_path,
            "-y",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )
    return output_path