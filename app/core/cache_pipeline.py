"""Cache persistente usado pelo pipeline (transcrição, momentos, traduções por clipe)."""

from __future__ import annotations

from app.core.cache import cache_path, key_hash, read_json, write_json
from app.core.config import (
    OUTPUT_VIDEO_HEIGHT,
    OUTPUT_VIDEO_WIDTH,
    SMART_CROP_FRAME_SAMPLES,
    SMART_CROP_MIN_CHANGE_INTERVAL_SEC,
    SMART_CROP_SPEAKER_FPS,
)


def crop_plan_cache_opts() -> dict:
    """Opções que alteram o plano de smart crop (devem entrar na chave de cache)."""
    return {
        "output_w": OUTPUT_VIDEO_WIDTH,
        "output_h": OUTPUT_VIDEO_HEIGHT,
        "frame_samples": SMART_CROP_FRAME_SAMPLES,
        "speaker_fps": SMART_CROP_SPEAKER_FPS,
        "min_change_interval_sec": SMART_CROP_MIN_CHANGE_INTERVAL_SEC,
    }


def load_cached_crop_plan(*, video_fp: str, opts: dict) -> dict | None:
    key = key_hash("crop_plan", video_fp, opts)
    p = cache_path("crop_plans", key)
    data = read_json(p)
    return data if isinstance(data, dict) else None


def save_cached_crop_plan(*, video_fp: str, opts: dict, plan: dict) -> None:
    key = key_hash("crop_plan", video_fp, opts)
    p = cache_path("crop_plans", key)
    write_json(p, plan)


def load_cached_segments(video_fp: str, *, transcribe_opts: dict) -> list[dict] | None:
    key = key_hash("segments", video_fp, transcribe_opts)
    p = cache_path("segments", key)
    data = read_json(p)
    return data if isinstance(data, list) else None


def save_cached_segments(video_fp: str, segments: list[dict], *, transcribe_opts: dict) -> None:
    key = key_hash("segments", video_fp, transcribe_opts)
    p = cache_path("segments", key)
    write_json(p, segments)


def load_cached_moments(video_fp: str, *, moments_opts: dict) -> list[dict] | None:
    key = key_hash("moments", video_fp, moments_opts)
    p = cache_path("moments", key)
    data = read_json(p)
    return data if isinstance(data, list) else None


def save_cached_moments(video_fp: str, moments: list[dict], *, moments_opts: dict) -> None:
    key = key_hash("moments", video_fp, moments_opts)
    p = cache_path("moments", key)
    write_json(p, moments)


def load_cached_moment_analysis(video_fp: str, *, moments_opts: dict) -> dict | None:
    """Carrega seleção + candidatos sem invalidar o cache legado de listas simples."""
    key = key_hash("moment_analysis", video_fp, moments_opts)
    p = cache_path("moment_analysis", key)
    data = read_json(p)
    if not isinstance(data, dict):
        return None
    selected = data.get("selected")
    candidates = data.get("candidates")
    if not isinstance(selected, list) or not isinstance(candidates, list):
        return None
    return {"selected": selected, "candidates": candidates}


def save_cached_moment_analysis(
    video_fp: str,
    *,
    selected: list[dict],
    candidates: list[dict],
    moments_opts: dict,
) -> None:
    key = key_hash("moment_analysis", video_fp, moments_opts)
    p = cache_path("moment_analysis", key)
    write_json(p, {"selected": selected, "candidates": candidates})


def _segments_compact(segments: list[dict]) -> list[tuple[float, float, str]]:
    # Arredondar em 3 casas alinha chaves de cache entre módulos que montam os mesmos segmentos.
    return [
        (round(float(s["start"]), 3), round(float(s["end"]), 3), (s.get("text") or "").strip())
        for s in segments
    ]


def load_cached_translated_segments(
    *,
    video_fp: str,
    clip_index: int,
    target: str,
    segments: list[dict],
) -> list[dict] | None:
    compact = _segments_compact(segments)
    key = key_hash("translated_segments", video_fp, target, compact)
    p = cache_path("translations", key)
    data = read_json(p)
    return data if isinstance(data, list) else None


def save_cached_translated_segments(
    *,
    video_fp: str,
    clip_index: int,
    target: str,
    input_segments: list[dict],
    translated: list[dict],
) -> None:
    compact = _segments_compact(input_segments)
    key = key_hash("translated_segments", video_fp, target, compact)
    p = cache_path("translations", key)
    write_json(p, translated)
