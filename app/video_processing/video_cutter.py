"""Vídeo: corta um trecho e aplica speed-up leve.

O corte é feito com FFmpeg e já aplica pequenas alterações (noise/brightness) e
`setpts`/`atempo` para acelerar o conteúdo e reduzir chance de detecção de reupload.
"""

import subprocess
from pathlib import Path

from app.core.config import (
    CLIP_SPEED_UP_PERCENT,
    FFMPEG_PATH,
    clip_gpu_uses_vaapi,
    clip_ffmpeg_threads_args,
    ffmpeg_vaapi_hwdevice_args,
    ffmpeg_vaapi_vf_hwupload_suffix,
    gpu_clip_encoder_ffmpeg_args,
)
from app.core.subprocess_utils import run_cancelable


def cut_video(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    *,
    use_gpu_encoder: bool = False,
) -> str:
    """Corta o trecho [start, end) e salva em `output_path`."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    duration = end - start
    tempo = 1.0 + CLIP_SPEED_UP_PERCENT / 100.0

    # Ordem importa: primeiro pequenos filtros visuais, depois ajuste de timestamps (setpts).
    vf = f"noise=alls=1:allf=t+u,eq=brightness=0.01,setpts=PTS/{tempo}"
    af = f"atempo={tempo}"
    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    venc: list[str] = gpu_clip_encoder_ffmpeg_args() if use_gpu_encoder else cpu_venc

    th = clip_ffmpeg_threads_args(use_gpu_encoder=use_gpu_encoder)
    va_pre = ffmpeg_vaapi_hwdevice_args() if (use_gpu_encoder and clip_gpu_uses_vaapi()) else []
    vf_full = vf + ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu_encoder)
    cmd_base = [
        FFMPEG_PATH,
        *va_pre,
        *th,
        "-fflags",
        "+bitexact",
        "-ss",
        str(start),
        "-i",
        video_path,
        "-t",
        str(duration),
        "-map_metadata",
        "-1",
        "-vf",
        vf_full,
        "-af",
        af,
        "-c:a",
        "aac",
        "-avoid_negative_ts",
        "1",
        "-y",
        output_path,
    ]
    cmd = [*cmd_base[: cmd_base.index("-c:a")], *venc, *cmd_base[cmd_base.index("-c:a") :]]

    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        # Se o encoder GPU falhar (driver/hardware/encoder indisponível), faz fallback em CPU.
        if use_gpu_encoder:
            cmd_cpu = [
                FFMPEG_PATH,
                *th,
                "-fflags",
                "+bitexact",
                "-ss",
                str(start),
                "-i",
                video_path,
                "-t",
                str(duration),
                "-map_metadata",
                "-1",
                "-vf",
                vf,
                "-af",
                af,
                *cpu_venc,
                "-c:a",
                "aac",
                "-avoid_negative_ts",
                "1",
                "-y",
                output_path,
            ]
            try:
                run_cancelable(cmd_cpu, capture_output=True, text=True, check=True)
                return output_path
            except subprocess.CalledProcessError as e2:
                detail = (e2.stderr or e2.stdout or "").strip()
                if detail:
                    raise RuntimeError(f"FFmpeg falhou ao cortar vídeo (fallback CPU): {detail}") from e2
                raise

        detail = (e.stderr or e.stdout or "").strip()
        if detail:
            raise RuntimeError(f"FFmpeg falhou ao cortar vídeo: {detail}") from e
        raise
    return output_path
