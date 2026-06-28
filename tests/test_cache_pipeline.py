"""Cache do pipeline (segmentos, opções de crop)."""

from __future__ import annotations

import pytest

from app.core.cache_pipeline import crop_plan_cache_opts, load_cached_segments, save_cached_segments


def test_crop_plan_cache_opts_keys() -> None:
    opts = crop_plan_cache_opts()
    for k in ("output_w", "output_h", "frame_samples", "speaker_fps", "min_change_interval_sec"):
        assert k in opts


def test_segments_cache_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    fp = "fakefp"
    transcribe_opts = {"model": "x"}
    data = [{"start": 0.0, "end": 1.0, "text": "a"}]
    save_cached_segments(fp, data, transcribe_opts=transcribe_opts)
    loaded = load_cached_segments(fp, transcribe_opts=transcribe_opts)
    assert loaded == data
