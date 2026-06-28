"""Geração de arquivo SRT a partir de segmentos."""

from __future__ import annotations

from pathlib import Path

from app.subtitle.srt_generator import generate_srt


def test_generate_srt_basic(tmp_path: Path) -> None:
    out = tmp_path / "out.srt"
    generate_srt(
        [{"start": 0.0, "end": 2.0, "text": "Primeira linha longa o suficiente"}],
        str(out),
    )
    text = out.read_text(encoding="utf-8")
    assert "1\n" in text
    assert "00:00:00,000 --> 00:00:02,000" in text
    assert "Primeira linha" in text


def test_generate_srt_offset_and_playback_speed(tmp_path: Path) -> None:
    out = tmp_path / "out.srt"
    generate_srt(
        [{"start": 10.0, "end": 12.0, "text": "x"}],
        str(out),
        offset=10.0,
        playback_speed=2.0,
    )
    text = out.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,000" in text


def test_generate_srt_skips_empty_and_non_positive() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "o.srt"
        generate_srt(
            [
                {"start": 0.0, "end": 1.0, "text": "  "},
                {"start": 2.0, "end": 2.0, "text": "nope"},
                {"start": 3.0, "end": 5.0, "text": "ok"},
            ],
            str(out),
        )
        body = out.read_text(encoding="utf-8")
        assert body.count("\n\n") >= 1
        assert "ok" in body
        assert body.strip().startswith("1\n")
