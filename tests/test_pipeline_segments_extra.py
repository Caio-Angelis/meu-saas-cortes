from __future__ import annotations

from app.pipelines.cortes.pipeline import _segments_for_clip


def test_segments_for_clip_no_overlap_returns_empty() -> None:
    segs = [{"start": 0.0, "end": 1.0, "text": "a"}]
    assert _segments_for_clip(segs, 5.0, 10.0) == []


def test_segments_for_clip_empty_input() -> None:
    assert _segments_for_clip([], 0.0, 10.0) == []
