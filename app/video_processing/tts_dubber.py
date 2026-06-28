"""Dublagem opcional: Edge-TTS + FFmpeg (apenas áudio dublado, sem mix com original)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import subprocess
from pathlib import Path

import edge_tts

from app.core.config import (
    DUB_MAX_TTS_SPEEDUP,
    DUB_SILENCE_CUT_MIN_SEC,
    DUB_SILENCE_DETECT_DB,
    EDGE_TTS_MAX_CONCURRENT,
    EDGE_TTS_REQUEST_TIMEOUT_SEC,
    EDGE_TTS_RETRIES,
    EDGE_TTS_VOICE,
    EDGE_TTS_VOICE_PT,
    FFMPEG_PATH,
    FFPROBE_PATH,
    USE_GPU_CLIP_ENCODE,
    clip_gpu_uses_vaapi,
    clip_ffmpeg_threads_args,
    ffmpeg_vaapi_hwdevice_args,
    ffmpeg_vaapi_vf_hwupload_suffix,
    gpu_clip_encoder_ffmpeg_args,
)
from app.core.subprocess_utils import run_cancelable

_log = logging.getLogger(__name__)


def _ffprobe_stream_codec_name(path: str, *, stream: str = "a:0") -> str | None:
    r = run_cancelable(
        [
            FFPROBE_PATH,
            "-v",
            "error",
            "-select_streams",
            stream,
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        return None
    name = streams[0].get("codec_name")
    return str(name).strip().lower() if name else None


def _ffprobe_duration_sec(path: str) -> float:
    r = run_cancelable(
        [
            FFPROBE_PATH,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(0.0, float((r.stdout or "").strip() or 0.0))


async def _edge_tts_save(text: str, out_path: Path, voice: str) -> None:
    clean = (text or "").strip()
    if not clean:
        raise ValueError("empty TTS text")
    timeout = max(30.0, float(EDGE_TTS_REQUEST_TIMEOUT_SEC))
    last_err: BaseException | None = None
    for attempt in range(EDGE_TTS_RETRIES):
        comm = edge_tts.Communicate(clean, voice)
        try:
            await asyncio.wait_for(comm.save(str(out_path)), timeout=timeout)
            return
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"Edge-TTS excedeu {timeout:.0f}s neste trecho. "
                "Aumente EDGE_TTS_REQUEST_TIMEOUT_SEC no .env ou tente de novo."
            ) from e
        except Exception as e:
            last_err = e
            status = getattr(e, "status", None)
            name = type(e).__name__
            retryable = status == 403 or "403" in str(e) or "Handshake" in name or "handshake" in str(e).lower()
            if not retryable or attempt >= EDGE_TTS_RETRIES - 1:
                raise
            delay = min(48.0, (2**attempt) + random.random())
            _log.warning(
                "Edge-TTS falhou (%s: %s); retentativa %s/%s após %.1fs.",
                name,
                e,
                attempt + 1,
                EDGE_TTS_RETRIES,
                delay,
            )
            await asyncio.sleep(delay)
    if last_err:
        raise last_err


async def edge_tts_save_to_path(text: str, out_path: str | Path, voice: str) -> None:
    """Salva locução Edge-TTS no caminho indicado (extensão define o container, ex. .mp3)."""
    await _edge_tts_save(text, Path(out_path), voice)


def _run_edge_tts_parallel(jobs: list[tuple[str, Path]], voice: str) -> None:
    sem = asyncio.Semaphore(EDGE_TTS_MAX_CONCURRENT)

    async def _one(tx: str, op: Path) -> None:
        async with sem:
            await _edge_tts_save(tx, op, voice)

    async def _all() -> None:
        await asyncio.gather(*(_one(tx, op) for tx, op in jobs))

    asyncio.run(_all())


def _atempo_chain_filters(speedup: float) -> str:
    """
    speedup > 1 acelera o áudio (encurtando duração). FFmpeg exige atempo em ~[0.5, 2] por estágio.
    """
    if speedup <= 1.0001:
        return "anull"
    parts: list[str] = []
    x = float(speedup)
    while x > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        x /= 2.0
    while x < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        x /= 0.5
    if abs(x - 1.0) > 0.0005:
        parts.append(f"atempo={x:.6f}")
    return ",".join(parts) if parts else "anull"


def _encode_tts_fitted_to_slot(
    input_path: str,
    output_path: str,
    max_sec: float,
    *,
    max_speedup: float | None = None,
) -> None:
    """
    Encaixa o TTS no slot da transcrição. Se a fala sintetizada for mais longa que o slot
    (comum em EN vs. timing do Whisper), acelera com atempo em vez de cortar no meio com atrim.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cap = max(0.05, float(max_sec))
    max_sp = float(max_speedup if max_speedup is not None else DUB_MAX_TTS_SPEEDUP)
    max_sp = max(1.0, min(max_sp, 100.0))

    dur = _ffprobe_duration_sec(input_path)
    if dur <= 0:
        dur = 0.05

    if dur <= cap + 0.03:
        run_cancelable(
            [
                FFMPEG_PATH,
                "-i",
                input_path,
                "-ar",
                "44100",
                "-ac",
                "1",
                "-c:a",
                "aac",
                "-y",
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return

    ratio = dur / cap
    use_speedup = min(ratio, max_sp)
    af_parts: list[str] = []
    chain = _atempo_chain_filters(use_speedup)
    if chain != "anull":
        af_parts.append(chain)
    after_dur = dur / use_speedup
    if after_dur > cap + 0.04:
        af_parts.append(f"atrim=0:{cap:.6f}")
    if not af_parts:
        run_cancelable(
            [
                FFMPEG_PATH,
                "-i",
                input_path,
                "-ar",
                "44100",
                "-ac",
                "1",
                "-c:a",
                "aac",
                "-y",
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return

    run_cancelable(
        [
            FFMPEG_PATH,
            "-i",
            input_path,
            "-af",
            ",".join(af_parts),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "aac",
            "-y",
            output_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def _silence_intervals_from_audio(
    audio_path: str, *, noise_db: float, min_silence_detect_sec: float
) -> list[tuple[float, float]]:
    """Intervalos [start, end) onde silencedetect marcou silêncio."""
    r = run_cancelable(
        [
            FFMPEG_PATH,
            "-i",
            audio_path,
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence_detect_sec}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    stderr = r.stderr or ""
    pairs: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        m_s = re.search(r"silence_start:\s*([\d.]+)", line)
        if m_s:
            pending_start = float(m_s.group(1))
            continue
        m_e = re.search(r"silence_end:\s*([\d.]+)", line)
        if m_e and pending_start is not None:
            end = float(m_e.group(1))
            pairs.append((pending_start, end))
            pending_start = None
    return pairs


def _keep_intervals_after_dropping_long_silence(
    total_sec: float,
    silence_intervals: list[tuple[float, float]],
    min_silence_to_remove: float,
) -> list[tuple[float, float]]:
    to_remove: list[tuple[float, float]] = []
    for s, e in silence_intervals:
        if e - s >= min_silence_to_remove:
            to_remove.append((max(0.0, s), min(total_sec, e)))
    to_remove.sort(key=lambda x: x[0])
    merged: list[tuple[float, float]] = []
    for s, e in to_remove:
        if not merged or s > merged[-1][1]:
            merged.append((s, e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))

    keeps: list[tuple[float, float]] = []
    cur = 0.0
    for s, e in merged:
        if s > cur:
            keeps.append((cur, s))
        cur = max(cur, e)
    if cur < total_sec:
        keeps.append((cur, total_sec))
    return [(a, b) for a, b in keeps if b - a > 0.05]


def remove_long_silence_from_video(
    input_path: str,
    output_path: str,
    *,
    min_silence_sec: float | None = None,
    noise_db: float | None = None,
    use_gpu_encoder: bool | None = None,
) -> str:
    """
    Remove do vídeo trechos onde o áudio está em silêncio contínuo >= min_silence_sec.
    Vídeo e áudio são cortados juntos (legendas queimadas acompanham).
    """
    min_silence_sec = (
        float(min_silence_sec)
        if min_silence_sec is not None
        else DUB_SILENCE_CUT_MIN_SEC
    )
    noise_db = float(noise_db) if noise_db is not None else DUB_SILENCE_DETECT_DB
    try_gpu = USE_GPU_CLIP_ENCODE if use_gpu_encoder is None else bool(use_gpu_encoder)

    total = _ffprobe_duration_sec(input_path)
    if total <= 0.1:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        run_cancelable(
            [FFMPEG_PATH, "-i", input_path, "-c", "copy", "-y", output_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return output_path

    # d baixo = detecta silêncios mais curtos; depois filtramos por duração mínima
    intervals = _silence_intervals_from_audio(
        input_path,
        noise_db=noise_db,
        min_silence_detect_sec=0.25,
    )
    keeps = _keep_intervals_after_dropping_long_silence(
        total, intervals, min_silence_to_remove=min_silence_sec
    )

    if not keeps:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        run_cancelable(
            [FFMPEG_PATH, "-i", input_path, "-c", "copy", "-y", output_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return output_path

    if (
        len(keeps) == 1
        and keeps[0][0] < 0.05
        and keeps[0][1] >= total - 0.15
    ):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        run_cancelable(
            [FFMPEG_PATH, "-i", input_path, "-c", "copy", "-y", output_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return output_path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    attempts = [True, False] if try_gpu else [False]

    def _single_trim(a: float, b: float) -> None:
        vf = f"trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS"
        af = f"atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS"
        last_err: subprocess.CalledProcessError | None = None
        for gpu in attempts:
            th = clip_ffmpeg_threads_args(use_gpu_encoder=gpu)
            venc = gpu_clip_encoder_ffmpeg_args() if gpu else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
            va_pre = ffmpeg_vaapi_hwdevice_args() if (gpu and clip_gpu_uses_vaapi()) else []
            vf_use = vf + ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=gpu)
            cmd = [
                FFMPEG_PATH,
                *va_pre,
                *th,
                "-i",
                input_path,
                "-vf",
                vf_use,
                "-af",
                af,
                *venc,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-y",
                output_path,
            ]
            try:
                run_cancelable(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
                return
            except subprocess.CalledProcessError as e:
                last_err = e
        if last_err is not None:
            raise last_err

    def _concat_trim(fc: str) -> None:
        last_err: subprocess.CalledProcessError | None = None
        for gpu in attempts:
            th = clip_ffmpeg_threads_args(use_gpu_encoder=gpu)
            venc = gpu_clip_encoder_ffmpeg_args() if gpu else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
            va_pre = ffmpeg_vaapi_hwdevice_args() if (gpu and clip_gpu_uses_vaapi()) else []
            fc_use = fc
            v_map = "[outv]"
            if gpu and clip_gpu_uses_vaapi():
                fc_use = (
                    f"{fc};[outv]format=nv12,hwupload=derive_device=va:extra_hw_frames=64[outv_va]"
                )
                v_map = "[outv_va]"
            cmd = [
                FFMPEG_PATH,
                *va_pre,
                *th,
                "-i",
                input_path,
                "-filter_complex",
                fc_use,
                "-map",
                v_map,
                "-map",
                "[outa]",
                *venc,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-y",
                output_path,
            ]
            try:
                run_cancelable(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
                return
            except subprocess.CalledProcessError as e:
                last_err = e
        if last_err is not None:
            raise last_err

    if len(keeps) == 1:
        a, b = keeps[0]
        _single_trim(a, b)
        return output_path

    parts: list[str] = []
    for i, (a, b) in enumerate(keeps):
        parts.append(
            f"[0:v]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[a{i}]"
        )
    n = len(keeps)
    interleaved = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{interleaved}concat=n={n}:v=1:a=1[outv][outa]")
    fc = ";".join(parts)

    _concat_trim(fc)
    return output_path


def build_dub_audio(
    segments_en: list[dict],
    clip_start: float,
    clip_end: float,
    playback_speed: float,
    output_path: str,
    voice: str | None = None,
    temp_tag: str = "dub",
) -> str:
    """Monta faixa TTS alinhada aos timestamps da transcrição (início de cada fala = original)."""
    voice = voice or EDGE_TTS_VOICE
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    work: list[tuple[float, str, float]] = []
    for seg in segments_en:
        t = (seg.get("text") or "").strip()
        if not t:
            continue
        t0 = (seg["start"] - clip_start) / playback_speed
        t1 = (seg["end"] - clip_start) / playback_speed
        slot = max(0.05, t1 - t0)
        work.append((t0, t, slot))

    if not work:
        run_cancelable(
            [
                FFMPEG_PATH,
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "0.5",
                "-ar",
                "44100",
                "-ac",
                "1",
                "-c:a",
                "aac",
                "-y",
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return output_path

    work.sort(key=lambda x: x[0])
    clip_dur = (clip_end - clip_start) / playback_speed

    temp_dir = Path(output_path).parent / f"_dub_{os.getpid()}_{temp_tag}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fitted: list[tuple[int, str]] = []

    try:
        n_work = len(work)
        tts_jobs: list[tuple[str, Path]] = []
        max_alloweds: list[float] = []
        raw_paths: list[Path] = []
        for i, (t0, text, slot) in enumerate(work):
            if i + 1 < n_work:
                t0_next = work[i + 1][0]
                max_allowed = min(slot, max(0.05, t0_next - t0))
            else:
                max_allowed = min(slot, max(0.05, clip_dur - t0))
            raw = temp_dir / f"raw_{i}.mp3"
            raw_paths.append(raw)
            tts_jobs.append((text, raw))
            max_alloweds.append(max_allowed)
        _log.info("Edge-TTS: sintetizando %s trecho(s) com voz %s…", len(tts_jobs), voice)
        _run_edge_tts_parallel(tts_jobs, voice)
        for i, (t0, _text, _slot) in enumerate(work):
            raw = raw_paths[i]
            fit = temp_dir / f"fit_{i}.m4a"
            _encode_tts_fitted_to_slot(str(raw), str(fit), max_alloweds[i])
            delay_ms = max(0, int(round(float(t0) * 1000)))
            fitted.append((delay_ms, str(fit)))

        if len(fitted) == 1:
            delay_ms, fp = fitted[0]
            fc = f"[0:a]adelay={delay_ms}[aout]"
            run_cancelable(
                [
                    FFMPEG_PATH,
                    "-i",
                    fp,
                    "-filter_complex",
                    fc,
                    "-map",
                    "[aout]",
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    "-c:a",
                    "aac",
                    "-y",
                    output_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        else:
            inputs: list[str] = []
            for _delay_ms, fp in fitted:
                inputs.extend(["-i", fp])
            parts: list[str] = []
            mix_ins: list[str] = []
            for idx, (delay_ms, _) in enumerate(fitted):
                lab = f"a{idx}"
                parts.append(f"[{idx}:a]adelay={delay_ms}[{lab}]")
                mix_ins.append(f"[{lab}]")
            n = len(fitted)
            parts.append(
                f"{''.join(mix_ins)}amix=inputs={n}:duration=longest:normalize=0[aout]"
            )
            fc = ";".join(parts)
            run_cancelable(
                [
                    FFMPEG_PATH,
                    *inputs,
                    "-filter_complex",
                    fc,
                    "-map",
                    "[aout]",
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    "-c:a",
                    "aac",
                    "-y",
                    output_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
    finally:
        for p in temp_dir.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    return output_path


def build_english_dub_audio(
    segments_en: list[dict],
    clip_start: float,
    clip_end: float,
    playback_speed: float,
    output_path: str,
    voice: str | None = None,
    temp_tag: str = "dub",
) -> str:
    """Compat: dublagem em inglês."""
    return build_dub_audio(
        segments_en,
        clip_start,
        clip_end,
        playback_speed,
        output_path,
        voice=voice or EDGE_TTS_VOICE,
        temp_tag=temp_tag,
    )


def build_portuguese_dub_audio(
    segments_pt: list[dict],
    clip_start: float,
    clip_end: float,
    playback_speed: float,
    output_path: str,
    voice: str | None = None,
    temp_tag: str = "dub",
) -> str:
    """Dublagem em português (pt-BR) por padrão."""
    return build_dub_audio(
        segments_pt,
        clip_start,
        clip_end,
        playback_speed,
        output_path,
        voice=voice or EDGE_TTS_VOICE_PT,
        temp_tag=temp_tag,
    )


def mux_video_with_new_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> str:
    """Só o áudio dublado; vídeo termina junto com o áudio mais curto."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    vdur = _ffprobe_duration_sec(video_path)
    adur = _ffprobe_duration_sec(audio_path)
    out_dur = min(vdur, adur) if vdur > 0 and adur > 0 else max(vdur, adur)
    v_aud = _ffprobe_stream_codec_name(video_path)
    a_aud = _ffprobe_stream_codec_name(audio_path)
    use_aac_copy = v_aud == "aac" and a_aud == "aac"
    audio_args: list[str] = (
        ["-c:a", "copy"] if use_aac_copy else ["-c:a", "aac", "-b:a", "192k"]
    )
    run_cancelable(
        [
            FFMPEG_PATH,
            "-i",
            video_path,
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            *audio_args,
            "-t",
            str(out_dur),
            "-y",
            output_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return output_path
