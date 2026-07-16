"""Unit tests for smart-crop split (vstack) filtergraph — no real faces/FFmpeg encode."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.video_processing.subtitle_burner import (
    _build_split_vstack_graph,
    _prepare_scale_crop_overlay_vf,
)


def test_build_split_vstack_graph_contains_vstack_not_x_expr() -> None:
    g = _build_split_vstack_graph(
        (200.0, 300.0),
        (900.0, 320.0),
        src_w=1280,
        src_h=720,
        out_w=1080,
        out_h=1920,
    )
    assert "vstack=inputs=2[vsplit]" in g
    assert "[top]" in g and "[bot]" in g
    assert "x_expr" not in g
    assert g.count("[0:v]") == 2
    # crops clamp to source (720 height < half=960)
    assert "crop=1080:720:" in g or "crop=720:720:" in g


def test_build_split_vstack_clamps_to_source_bounds() -> None:
    g = _build_split_vstack_graph(
        (50.0, 50.0),
        (1200.0, 700.0),
        src_w=1280,
        src_h=720,
        out_w=1080,
        out_h=1920,
    )
    # left near edge → x=0; right near edge → x <= src_w - cw
    assert ":0:0," in g or ":0:0" in g
    assert "x_expr" not in g


def _write_minimal_srt(path: Path) -> None:
    path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nOi\n",
        encoding="utf-8",
    )


def test_prepare_split_returns_filter_complex(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.video_processing.subtitle_burner as sb

    monkeypatch.setattr(sb, "SMART_CROP_ENABLED", True)
    monkeypatch.setattr(sb, "fingerprint_file", lambda _p: "fp-split")
    plan = {
        "mode": "split",
        "left": (200.0, 300.0),
        "right": (900.0, 320.0),
        "src_w": 1280,
        "src_h": 720,
    }
    monkeypatch.setattr(sb, "load_cached_crop_plan", lambda **_kw: plan)

    srt = tmp_path / "clip.srt"
    _write_minimal_srt(srt)
    fake_video = tmp_path / "clip.mp4"
    fake_video.write_bytes(b"not-a-real-video")

    vf, ass_path, hook_file, cta_file, is_fc = _prepare_scale_crop_overlay_vf(
        str(fake_video),
        str(srt),
        "bottom",
        "Arial",
        "#FFFF00",
        "#000000",
        75,
        None,
        "pt",
    )
    assert is_fc is True
    assert "vstack=inputs=2[vsplit]" in vf
    assert vf.endswith("[vout]")
    assert "x_expr" not in vf
    assert not vf.startswith("scale=")
    assert "[vsplit]subtitles=" in vf or "[vsplit]subtitles='" in vf
    assert Path(ass_path).exists()
    assert cta_file.exists()
    assert hook_file is None


def test_prepare_static_returns_vf_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.video_processing.subtitle_burner as sb

    monkeypatch.setattr(sb, "SMART_CROP_ENABLED", True)
    monkeypatch.setattr(sb, "fingerprint_file", lambda _p: "fp-static")
    monkeypatch.setattr(
        sb,
        "load_cached_crop_plan",
        lambda **_kw: {"mode": "static", "x": 12, "y": 34},
    )

    srt = tmp_path / "s.srt"
    _write_minimal_srt(srt)
    vid = tmp_path / "s.mp4"
    vid.write_bytes(b"x")

    vf, _ass, _hook, _cta, is_fc = _prepare_scale_crop_overlay_vf(
        str(vid),
        str(srt),
        "bottom",
        "Arial",
        "#FFFF00",
        "#000000",
        75,
        None,
        "pt",
    )
    assert is_fc is False
    assert vf.startswith("scale=")
    assert "vstack" not in vf
    assert "crop=1080:1920:12:34" in vf or ":12:34" in vf


def test_prepare_dynamic_returns_vf_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.video_processing.subtitle_burner as sb

    monkeypatch.setattr(sb, "SMART_CROP_ENABLED", True)
    monkeypatch.setattr(sb, "fingerprint_file", lambda _p: "fp-dyn")
    monkeypatch.setattr(
        sb,
        "load_cached_crop_plan",
        lambda **_kw: {
            "mode": "dynamic",
            "x_expr": "if(lt(t\\,1)\\,10\\,20)",
            "y_expr": "30",
            "fallback_x": 10,
            "fallback_y": 30,
        },
    )

    srt = tmp_path / "d.srt"
    _write_minimal_srt(srt)
    vid = tmp_path / "d.mp4"
    vid.write_bytes(b"x")

    vf, _ass, _hook, _cta, is_fc = _prepare_scale_crop_overlay_vf(
        str(vid),
        str(srt),
        "bottom",
        "Arial",
        "#FFFF00",
        "#000000",
        75,
        None,
        "pt",
    )
    assert is_fc is False
    assert vf.startswith("scale=")
    assert "vstack" not in vf
    assert "if(lt(t" in vf
