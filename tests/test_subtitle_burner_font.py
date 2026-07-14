from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.core.config import TIKTOK_SUBTITLE_FONT
from app.video_processing.subtitle_burner import _prepare_scale_crop_overlay_vf


def _call_prepare(tmp_path: Path, fonte: str | None):
    srt = tmp_path / "clip.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nOi\n",
        encoding="utf-8",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    with (
        patch("app.video_processing.subtitle_burner.SMART_CROP_ENABLED", False),
        patch("app.core.config.SUBTITLE_KARAOKE", False),
        patch("app.video_processing.subtitle_burner.write_tiktok_ass_from_srt") as mock_ass,
    ):
        mock_ass.return_value = str(tmp_path / "clip.ass")
        _prepare_scale_crop_overlay_vf(
            str(video),
            str(srt),
            "bottom",
            fonte,  # type: ignore[arg-type]
            "#FFFF00",
            "#000000",
            75,
            None,
            "pt",
        )
        return mock_ass.call_args.kwargs["font_name"]


def test_prepare_overrides_arial_with_tiktok_font(tmp_path: Path) -> None:
    assert _call_prepare(tmp_path, "Arial") == TIKTOK_SUBTITLE_FONT


def test_prepare_overrides_empty_fonte_with_tiktok_font(tmp_path: Path) -> None:
    assert _call_prepare(tmp_path, "") == TIKTOK_SUBTITLE_FONT


def test_prepare_keeps_custom_fonte(tmp_path: Path) -> None:
    assert _call_prepare(tmp_path, "CustomFont") == "CustomFont"
