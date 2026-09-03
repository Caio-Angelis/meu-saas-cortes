"""Particionamento de vídeos longos em blocos completos de 20 minutos."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.cancel import raise_if_cancelled
from app.core.clip_output_naming import sanitize_clip_output_stem
from app.core.config import FFMPEG_PATH, FFPROBE_PATH
from app.core.subprocess_utils import run_cancelable

CHUNK_DURATION_SEC = 20 * 60
_DURATION_EPSILON_SEC = 0.05


@dataclass(frozen=True)
class VideoSplitResult:
    """Resultado do particionamento de uma fonte."""

    paths: tuple[str, ...]
    source_duration_sec: float
    discarded_remainder_sec: float
    was_split: bool


def probe_video_duration_seconds(video_path: str) -> float:
    """Lê a duração da fonte com ffprobe."""
    result = run_cancelable(
        [
            FFPROBE_PATH,
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            video_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Não foi possível ler a duração do vídeo: {video_path}") from exc
    if duration <= 0:
        raise RuntimeError(f"Duração inválida para o vídeo: {video_path}")
    return duration


def _full_chunk_count(duration_sec: float, chunk_duration_sec: float) -> int:
    """Quantidade de blocos completos, sem transformar o resto em um novo bloco."""
    if duration_sec <= chunk_duration_sec + _DURATION_EPSILON_SEC:
        return 1
    return max(1, int((duration_sec + _DURATION_EPSILON_SEC) // chunk_duration_sec))


def _chunk_output_path(output_dir: Path, source_path: str, index: int) -> Path:
    stem = sanitize_clip_output_stem(Path(source_path).stem, max_len=120)
    return output_dir / f"{stem}__parte_{index:02d}.mp4"


def _split_one_chunk(
    source_path: str,
    output_path: Path,
    *,
    start_sec: float,
    duration_sec: float,
) -> None:
    """Copia um bloco sem recodificar, mantendo o custo de preparação baixo."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        FFMPEG_PATH,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        source_path,
        "-t",
        f"{duration_sec:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-reset_timestamps",
        "1",
        str(output_path),
    ]
    try:
        run_cancelable(command, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail[-800:]}" if detail else "."
        raise RuntimeError(f"FFmpeg falhou ao dividir o vídeo{suffix}") from exc
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"FFmpeg não gerou o bloco esperado: {output_path}")


def split_video_into_chunks(
    video_path: str,
    output_dir: str | Path,
    *,
    chunk_duration_sec: float = CHUNK_DURATION_SEC,
) -> VideoSplitResult:
    """
    Divide a fonte em blocos completos de `chunk_duration_sec`.

    Fontes de até 20 minutos seguem intactas. Para fontes maiores, somente os blocos
    completos são gerados; o restante é descartado por decisão de produto.
    """
    source = str(Path(video_path).resolve())
    if not Path(source).is_file():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")
    chunk_duration = float(chunk_duration_sec)
    if chunk_duration <= 0:
        raise ValueError("chunk_duration_sec deve ser positivo.")

    duration = probe_video_duration_seconds(source)
    if duration <= chunk_duration + _DURATION_EPSILON_SEC:
        return VideoSplitResult(
            paths=(str(video_path),),
            source_duration_sec=duration,
            discarded_remainder_sec=0.0,
            was_split=False,
        )

    n_chunks = _full_chunk_count(duration, chunk_duration)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for index in range(n_chunks):
            raise_if_cancelled()
            output_path = _chunk_output_path(root, source, index + 1)
            created.append(output_path)
            _split_one_chunk(
                source,
                output_path,
                start_sec=index * chunk_duration,
                duration_sec=chunk_duration,
            )
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise

    discarded = max(0.0, duration - n_chunks * chunk_duration)
    return VideoSplitResult(
        paths=tuple(str(path.resolve()) for path in created),
        source_duration_sec=duration,
        discarded_remainder_sec=discarded,
        was_split=True,
    )


def cleanup_split_directory(path: str | Path) -> None:
    """Remove os blocos gerados para uma execução, sem tocar na fonte original."""
    shutil.rmtree(Path(path), ignore_errors=True)
