from __future__ import annotations

from app.pipelines.cortes.pipeline import _segments_for_clip


def test_segments_for_clip_includes_partial_overlap() -> None:
    segs = [
        {"start": 0.0, "end": 4.0, "text": "um dois tres quatro"},
        {"start": 4.0, "end": 6.0, "text": "cinco seis"},
        {"start": 6.0, "end": 9.0, "text": "sete oito nove"},
    ]
    out = _segments_for_clip(segs, clip_start=2.0, clip_end=7.0)
    assert len(out) >= 2
    assert out[0]["start"] == 2.0
    assert out[-1]["end"] == 7.0
    assert all((o["text"] or "").strip() for o in out)

