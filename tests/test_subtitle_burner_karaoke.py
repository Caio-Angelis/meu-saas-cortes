from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.core.config import SUBTITLE_KARAOKE_HIGHLIGHT
from app.video_processing.subtitle_burner import _prepare_scale_crop_overlay_vf


def _prepare(tmp_path: Path) -> None:
    srt = tmp_path / "clip.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nOi mundo\n",
        encoding="utf-8",
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    _prepare_scale_crop_overlay_vf(
        str(video),
        str(srt),
        "bottom",
        "Arial",
        "#FFFF00",
        "#000000",
        75,
        None,
        "pt",
    )


def test_prepare_uses_karaoke_when_enabled(tmp_path: Path) -> None:
    with (
        patch("app.video_processing.subtitle_burner.SMART_CROP_ENABLED", False),
        patch("app.core.config.SUBTITLE_KARAOKE", True),
        patch("app.video_processing.subtitle_burner.write_tiktok_ass_karaoke_from_srt") as mock_kara,
        patch("app.video_processing.subtitle_burner.write_tiktok_ass_from_srt") as mock_plain,
    ):
        mock_kara.return_value = str(tmp_path / "clip.ass")
        _prepare(tmp_path)
        assert mock_kara.called
        assert not mock_plain.called
        assert mock_kara.call_args.kwargs["highlight_hex"] == SUBTITLE_KARAOKE_HIGHLIGHT


def test_prepare_uses_plain_ass_when_karaoke_disabled(tmp_path: Path) -> None:
    with (
        patch("app.video_processing.subtitle_burner.SMART_CROP_ENABLED", False),
        patch("app.core.config.SUBTITLE_KARAOKE", False),
        patch("app.video_processing.subtitle_burner.write_tiktok_ass_karaoke_from_srt") as mock_kara,
        patch("app.video_processing.subtitle_burner.write_tiktok_ass_from_srt") as mock_plain,
    ):
        mock_plain.return_value = str(tmp_path / "clip.ass")
        _prepare(tmp_path)
        assert mock_plain.called
        assert not mock_kara.called
