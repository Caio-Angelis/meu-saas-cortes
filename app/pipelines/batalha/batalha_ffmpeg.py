"""
FFmpeg para Batalha 1v1 — vídeo via stdin (rawvideo RGB24) e mixagem TTS + SFX de colisão.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.pipelines.batalha.batalha_frames import BatalhaSimulationBase, FPS, iter_simulation_frames
from app.pipelines.batalha.batalha_pipeline import BATALHA_VIDEO_HEIGHT, BATALHA_VIDEO_WIDTH
from app.core.cancel import is_cancelled, raise_if_cancelled
from app.core.config import FFMPEG_PATH
from app.gui.gui_export import ffprobe_duration_seconds
from app.core.subprocess_utils import run_cancelable

_log = logging.getLogger("batalha_ffmpeg")

DEFAULT_HIT_SFX_ASSET = Path("assets/ball.mp3")
FALLBACK_HIT_SFX_ASSET = Path("assets/ding.mp3")
HIT_CLIP_SEC = 0.12
HIT_VOLUME = 0.78
NARRATION_VOLUME = 1.08
MAX_COLLISION_SFX = 220
VIDEO_CRF = 23


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_hit_sfx_path(hit_sfx: Path | None = None) -> Path | None:
    """Retorna caminho do SFX de impacto (``assets/ball.mp3`` por padrão)."""
    candidates: list[Path] = []
    if hit_sfx is not None:
        candidates.append(hit_sfx)
    candidates.extend((DEFAULT_HIT_SFX_ASSET, FALLBACK_HIT_SFX_ASSET))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
        alt = (_repo_root() / candidate).resolve()
        if alt.is_file():
            return alt
    return None


def hit_sfx_clip_duration_sec(hit_path: Path) -> float:
    """Duração do trecho usado em cada impacto (respeita o MP3 real, com teto)."""
    dur = ffprobe_duration_seconds(str(hit_path))
    if dur is not None and 0.02 < dur <= 0.85:
        return float(dur)
    return HIT_CLIP_SEC


def prepare_hit_sfx_sample(work_dir: Path, *, hit_sfx: Path | None = None) -> Path:
    """
    Garante um MP3 para colisões — usa ``assets/ball.mp3`` (ou fallback) ou gera tom via lavfi.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolve_hit_sfx_path(hit_sfx)
    if resolved is not None:
        return resolved

    out = work_dir / "hit_synthetic.mp3"
    if out.is_file() and out.stat().st_size > 64:
        return out

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=880:sample_rate=44100:duration={HIT_CLIP_SEC:.3f}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={HIT_CLIP_SEC:.3f}",
        "-filter_complex",
        (
            f"[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0,"
            f"afade=t=out:st={max(0.0, HIT_CLIP_SEC - 0.04):.3f}:d=0.04,volume=1.2[out]"
        ),
        "-map",
        "[out]",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "6",
        str(out),
    ]
    run_cancelable(cmd, capture_output=True, text=True, check=True)
    return out


def probe_audio_duration_sec(path: Path, *, label: str = "áudio") -> float:
    if not path.is_file() or path.stat().st_size < 32:
        raise RuntimeError(f"{label} ausente ou vazio: {path}")
    dur = ffprobe_duration_seconds(str(path))
    if dur is None or dur <= 0.01:
        raise RuntimeError(f"Duração inválida de {label}: {path}")
    return float(dur)


def build_collision_sfx_filter(
    collision_times_sec: Sequence[float],
    *,
    hit_input_index: int = 2,
    hit_duration_sec: float = HIT_CLIP_SEC,
    max_hits: int = MAX_COLLISION_SFX,
    volume: float = HIT_VOLUME,
) -> tuple[str, str] | None:
    """
    Monta trecho de ``filter_complex`` com ``adelay`` + ``amix`` para impactos.

    Returns:
        (filter_complex, rótulo de saída) ou None se não houver colisões.
    """
    times = [float(t) for t in collision_times_sec if t >= 0.0][:max_hits]
    if not times:
        return None

    segments: list[str] = []
    labels: list[str] = []
    src = f"[{hit_input_index}:a]"
    for i, t_sec in enumerate(times):
        ms = max(0, int(round(t_sec * 1000)))
        lab = f"bhx{i}"
        segments.append(
            f"{src}atrim=0:{hit_duration_sec:.3f},asetpts=PTS-STARTPTS,"
            f"volume={volume:.2f},adelay={ms}|{ms}[{lab}]"
        )
        labels.append(f"[{lab}]")

    n = len(labels)
    fc = (
        ";".join(segments)
        + ";"
        + "".join(labels)
        + f"amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[sfx]"
    )
    return fc, "[sfx]"


def build_batalha_audio_filter_complex(
    collision_times_sec: Sequence[float],
    *,
    intro_input_index: int = 1,
    hit_input_index: int = 2,
    mid_narration_input_index: int | None = None,
    mid_narration_delay_sec: float | None = None,
    victory_input_index: int | None = None,
    victory_start_sec: float | None = None,
    narration_volume: float = NARRATION_VOLUME,
    hit_volume: float = HIT_VOLUME,
    hit_duration_sec: float = HIT_CLIP_SEC,
) -> tuple[str, str]:
    """
    Mixagem: TTS intro → TTS meio (após intro) → TTS vitória (opcional) + SFX de colisão → ``[aout]``.
    """
    sfx = build_collision_sfx_filter(
        collision_times_sec,
        hit_input_index=hit_input_index,
        hit_duration_sec=hit_duration_sec,
        volume=hit_volume,
    )
    segments: list[str] = []
    mix_inputs: list[str] = []

    segments.append(
        f"[{intro_input_index}:a]volume={narration_volume:.2f}[intro]"
    )
    mix_inputs.append("[intro]")

    if (
        mid_narration_input_index is not None
        and mid_narration_delay_sec is not None
        and mid_narration_delay_sec >= 0.0
    ):
        ms = max(0, int(round(mid_narration_delay_sec * 1000)))
        segments.append(
            f"[{mid_narration_input_index}:a]adelay={ms}|{ms},"
            f"volume={narration_volume:.2f},apad=pad_dur=2[mid]"
        )
        mix_inputs.append("[mid]")

    if sfx is not None:
        sfx_fc, sfx_label = sfx
        segments.append(sfx_fc)
        mix_inputs.append(sfx_label)

    if (
        victory_input_index is not None
        and victory_start_sec is not None
        and victory_start_sec >= 0.0
    ):
        ms = max(0, int(round(victory_start_sec * 1000)))
        segments.append(
            f"[{victory_input_index}:a]adelay={ms}|{ms},"
            f"volume={narration_volume:.2f},apad=pad_dur=2[victory]"
        )
        mix_inputs.append("[victory]")

    if len(mix_inputs) == 1:
        return f"{segments[0]};{mix_inputs[0]}anull[aout]", "[aout]"

    fc = (
        ";".join(segments)
        + ";"
        + "".join(mix_inputs)
        + f"amix=inputs={len(mix_inputs)}:duration=longest:"
        "dropout_transition=2:normalize=0[aout]"
    )
    return fc, "[aout]"


def encode_simulation_to_silent_mp4(
    sim: BatalhaSimulationBase,
    out_path: Path,
    *,
    fps: float = FPS,
    width: int = BATALHA_VIDEO_WIDTH,
    height: int = BATALHA_VIDEO_HEIGHT,
    max_duration_sec: float | None = None,
    cancel_check: Any | None = None,
    progress_callback: Any | None = None,
) -> tuple[Path, list[float], float, int]:
    """
    Envia frames RGB24 ao FFmpeg via stdin e grava MP4 sem áudio.

    Returns:
        (caminho, collision_times_sec, duration_sec, frame_count)
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from app.pipelines.batalha.batalha_frames import MAX_SIM_DURATION_SEC

    cap = max_duration_sec if max_duration_sec is not None else MAX_SIM_DURATION_SEC

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-nostdin",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        str(VIDEO_CRF),
        str(out_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    frame_count = 0
    stderr_data = b""

    try:
        assert proc.stdin is not None
        for frame_bytes in iter_simulation_frames(
            sim,
            fps=fps,
            max_duration_sec=cap,
            cancel_check=cancel_check,
        ):
            raise_if_cancelled(cancel_check)
            proc.stdin.write(frame_bytes)
            frame_count += 1
            if progress_callback is not None and frame_count % 15 == 0:
                try:
                    progress_callback(frame_count)
                except Exception:
                    pass
        proc.stdin.close()
        proc.stdin = None

        while True:
            if is_cancelled():
                proc.kill()
                raise RuntimeError("Cancelado pelo usuário.")
            try:
                _, stderr_data = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
    except Exception:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
        proc.kill()
        proc.communicate(timeout=5)
        raise
    finally:
        if proc.poll() is None:
            proc.kill()

    if proc.returncode != 0:
        err = (stderr_data or b"").decode(errors="replace").strip()
        raise RuntimeError(
            f"FFmpeg falhou ao codificar vídeo da batalha (exit {proc.returncode}): "
            f"{err or 'sem stderr'}"
        )

    if not out_path.is_file() or out_path.stat().st_size < 4096:
        raise RuntimeError(f"FFmpeg não gerou vídeo válido: {out_path}")

    dur_v = ffprobe_duration_seconds(str(out_path))
    duration = float(dur_v) if dur_v and dur_v > 0 else frame_count / fps

    return (
        out_path,
        list(sim.collision_times_sec),
        duration,
        frame_count,
    )


def mux_batalha_video_with_audio(
    video_path: Path,
    intro_path: Path,
    collision_times_sec: Sequence[float],
    out_path: Path,
    *,
    work_dir: Path | None = None,
    hit_sfx: Path | None = None,
    mid_narration_path: Path | None = None,
    victory_narration_path: Path | None = None,
    victory_start_sec: float | None = None,
    cancel_check: Any | None = None,
) -> Path:
    """Combina vídeo mudo + TTS intro/meio/vitória + SFX de colisão."""
    raise_if_cancelled(cancel_check)
    video_path = Path(video_path).resolve()
    intro_path = Path(intro_path).resolve()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wd = work_dir or out_path.parent
    hit_path = prepare_hit_sfx_sample(wd, hit_sfx=hit_sfx)
    hit_clip_sec = hit_sfx_clip_duration_sec(hit_path)

    intro_duration_sec = probe_audio_duration_sec(intro_path, label="intro")

    mid_input_index: int | None = None
    mid_delay_sec: float | None = None
    if mid_narration_path is not None and mid_narration_path.is_file():
        mid_input_index = 3
        mid_delay_sec = intro_duration_sec

    victory_input_index: int | None = None
    if victory_narration_path is not None and victory_narration_path.is_file():
        victory_input_index = 4 if mid_input_index is not None else 3

    sfx_fc, _ = build_batalha_audio_filter_complex(
        collision_times_sec,
        intro_input_index=1,
        hit_input_index=2,
        mid_narration_input_index=mid_input_index,
        mid_narration_delay_sec=mid_delay_sec,
        victory_input_index=victory_input_index,
        victory_start_sec=victory_start_sec,
        hit_duration_sec=hit_clip_sec,
    )

    cmd: list[str] = [
        FFMPEG_PATH,
        "-y",
        "-nostdin",
        "-i",
        str(video_path),
        "-i",
        str(intro_path),
        "-i",
        str(hit_path),
    ]
    if mid_input_index is not None:
        cmd.extend(["-i", str(mid_narration_path)])
    if victory_input_index is not None:
        cmd.extend(["-i", str(victory_narration_path)])

    if sfx_fc:
        cmd.extend(
            [
                "-filter_complex",
                sfx_fc,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
            ]
        )
    else:
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])

    cmd.extend(
        [
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )

    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(
            f"FFmpeg falhou ao mixar áudio da batalha: {detail or e}"
        ) from e

    if not out_path.is_file() or out_path.stat().st_size < 4096:
        raise RuntimeError(f"Vídeo final da batalha inválido: {out_path}")

    _log.info("Batalha MP4 final: %s", out_path)
    return out_path


def assemble_batalha_video_ffmpeg(
    sim: BatalhaSimulationBase,
    intro_path: Path,
    output_path: Path,
    *,
    work_dir: Path,
    hit_sfx: Path | None = None,
    mid_narration_path: Path | None = None,
    victory_narration_path: Path | None = None,
    cancel_check: Any | None = None,
    progress_callback: Any | None = None,
) -> Path:
    """Codifica simulação (stdin) e mixa intro + narração do meio + impactos (+ vitória Plinko)."""
    silent = Path(work_dir) / "video_silent.mp4"
    video_path, collision_times, _dur, _n = encode_simulation_to_silent_mp4(
        sim,
        silent,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )
    raise_if_cancelled(cancel_check)
    victory_start: float | None = None
    if victory_narration_path is not None:
        victory_start = getattr(sim, "plinko_victory_screen_start_sec", None)
    return mux_batalha_video_with_audio(
        video_path,
        intro_path,
        collision_times,
        output_path,
        work_dir=work_dir,
        hit_sfx=hit_sfx,
        mid_narration_path=mid_narration_path,
        victory_narration_path=victory_narration_path,
        victory_start_sec=victory_start,
        cancel_check=cancel_check,
    )
