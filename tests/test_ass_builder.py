from __future__ import annotations

from pathlib import Path

from app.subtitle.ass_builder import write_tiktok_ass_from_srt


def test_write_tiktok_ass_from_srt_basic(tmp_path: Path) -> None:
    srt = tmp_path / "a.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,500\nOi mundo\n\n2\n00:00:03,000 --> 00:00:04,000\nLinha 2\n",
        encoding="utf-8",
    )
    ass = tmp_path / "a.ass"
    out = write_tiktok_ass_from_srt(
        str(srt),
        str(ass),
        play_res_x=1080,
        play_res_y=1920,
        font_name="Arial",
        font_size=40,
        primary_ass="&H00FFFFFF",
        back_ass="&H80000000",
        margin_l=10,
        margin_r=10,
        margin_v=20,
        alignment=2,
    )
    assert out == str(ass)
    content = ass.read_text(encoding="utf-8-sig")
    assert "[Script Info]" in content
    assert "Dialogue:" in content
    assert "Oi mundo" in content

