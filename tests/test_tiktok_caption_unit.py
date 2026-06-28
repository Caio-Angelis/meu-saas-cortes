"""Legenda de post TikTok: parse JSON, hashtags e arquivo .txt (sem Groq)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai_integrations import tiktok_caption as tc


def test_extract_json_object_strips_fence() -> None:
    raw = '```\n{"caption":"Oi","hashtags":["#a"]}\n```'
    inner = tc._extract_json_object(raw)
    assert json.loads(inner)["caption"] == "Oi"


def test_extract_json_object_missing_raises() -> None:
    with pytest.raises(ValueError, match="No JSON object"):
        tc._extract_json_object("[]")


def test_normalize_hashtags_adds_hash_and_caps() -> None:
    assert tc._normalize_hashtags(["foo", "#bar", "", 123, "#"]) == ["#foo", "#bar"]


def test_normalize_hashtags_filters_generic_viral_tags() -> None:
    assert tc._normalize_hashtags(["#futebol", "#plotwist", "#cinema"]) == ["#futebol", "#cinema"]


def test_content_hashtags_from_transcript() -> None:
    tags = tc._content_hashtags_from_transcript(
        "O Messi marcou um gol incrível no campeonato"
    )
    assert tags
    assert all(t.startswith("#") for t in tags)
    assert not any("plotwist" in t for t in tags)


def test_finalize_tiktok_hashtags_inserts_fyp_fy_and_five_total() -> None:
    out = tc._finalize_tiktok_hashtags(["#humor", "#futebol", "#cinema"], "pt")
    assert out[:2] == ["#fyp", "#fy"]
    assert len(out) == 5
    assert "#humor" in out


def test_finalize_tiktok_hashtags_strips_duplicate_discovery_tags() -> None:
    out = tc._finalize_tiktok_hashtags(["#FYP", "#fy", "#sóisso"], "pt")
    assert out[:2] == ["#fyp", "#fy"]
    assert any(t.lower() == "#sóisso" for t in out)
    assert "#FYP" not in out


def test_finalize_tiktok_hashtags_fills_from_transcript_when_empty() -> None:
    out = tc._finalize_tiktok_hashtags([], "en", transcript="Football championship final goal")
    assert out[:2] == ["#fyp", "#fy"]
    assert len(out) >= 3
    assert any("football" in t.lower() or "championship" in t.lower() or "final" in t.lower() for t in out[2:])


def test_fallback_caption_pt() -> None:
    out = tc._fallback_caption("  ", "pt")
    assert "Assista" in out or "#Cortes" in out


def test_fallback_caption_en() -> None:
    out = tc._fallback_caption("", "en")
    assert "Watch" in out or "#Shorts" in out


def test_append_source_attribution_pt() -> None:
    from app.download.ytdlp_download import VideoSourceAttribution

    attr = VideoSourceAttribution(
        channel="Peewee",
        channel_url="https://www.youtube.com/@peewee",
    )
    out = tc.append_source_attribution_to_caption(
        "Gancho viral\n#fyp #fy #a #b #c",
        attr,
        language="pt",
    )
    assert "Review original: Peewee" in out
    assert "https://www.youtube.com/@peewee" in out
    assert out.startswith("Gancho viral")


def test_append_source_attribution_skips_when_none() -> None:
    assert tc.append_source_attribution_to_caption("linha\n", None, language="pt") == "linha\n"


def test_save_tiktok_caption_file(tmp_path: Path) -> None:
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"")
    path = tc.save_tiktok_caption_file(str(mp4), "hello\n")
    assert path.endswith(".txt")
    assert Path(path).read_text(encoding="utf-8") == "hello\n"
