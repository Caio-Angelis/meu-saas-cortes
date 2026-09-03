from app.download.ytdlp_download import (
    attribution_from_ytdlp_info,
    collect_urls_from_lines,
    normalize_media_url,
    pick_top_viewed_among_long,
    rank_theme_sources,
    resolve_ytdlp_cmd,
)


def test_normalize_media_url() -> None:
    assert normalize_media_url("https://youtu.be/abc") == "https://youtu.be/abc"
    assert normalize_media_url("youtu.be/xyz") == "https://youtu.be/xyz"
    assert normalize_media_url("  www.youtube.com/watch?v=1  ") == "https://www.youtube.com/watch?v=1"
    assert normalize_media_url("# comentário") is None
    assert normalize_media_url("") is None


def test_attribution_from_ytdlp_info_channel() -> None:
    data = {
        "channel": "Peewee",
        "channel_url": "https://www.youtube.com/@peewee",
        "webpage_url": "https://www.youtube.com/watch?v=abc",
    }
    attr = attribution_from_ytdlp_info(data)
    assert attr is not None
    assert attr.channel == "Peewee"
    assert attr.channel_url == "https://www.youtube.com/@peewee"
    assert attr.source_url == "https://www.youtube.com/watch?v=abc"


def test_attribution_from_ytdlp_info_uploader_fallback() -> None:
    attr = attribution_from_ytdlp_info({"uploader": "Canal X"})
    assert attr is not None
    assert attr.channel == "Canal X"


def test_attribution_from_ytdlp_info_empty() -> None:
    assert attribution_from_ytdlp_info({}) is None
    assert attribution_from_ytdlp_info({"title": "x"}) is None


def test_resolve_ytdlp_cmd_falls_back_to_module(monkeypatch) -> None:
    import app.download.ytdlp_download as ytdl

    monkeypatch.delenv("YTDLP_PATH", raising=False)
    monkeypatch.delenv("YT_DLP_PATH", raising=False)
    monkeypatch.setattr(ytdl.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        ytdl,
        "_ytdlp_cmd_runnable",
        lambda cmd: len(cmd) >= 3 and cmd[1:3] == ["-m", "yt_dlp"],
    )
    monkeypatch.setattr(ytdl.sys, "executable", "/fake/venv/bin/python")
    ytdl.resolve_ytdlp_cmd.cache_clear()
    cmd = resolve_ytdlp_cmd()
    assert cmd == ("/fake/venv/bin/python", "-m", "yt_dlp")


def test_collect_urls_dedupes() -> None:
    text = """
    https://a.com/x
    https://a.com/x
    # dup
    https://b.com/y
    """
    assert collect_urls_from_lines(text) == ["https://a.com/x", "https://b.com/y"]


def test_collect_urls_dedupes_youtube_variants() -> None:
    text = """
    https://www.youtube.com/watch?v=abc123&feature=share
    https://youtu.be/abc123?si=tracking
    """
    assert collect_urls_from_lines(text) == [
        "https://www.youtube.com/watch?v=abc123&feature=share"
    ]


def test_pick_top_viewed_among_long_picks_max_views() -> None:
    entries = [
        {
            "id": "short",
            "title": "Short",
            "duration": 45,
            "view_count": 99_000_000,
            "webpage_url": "https://www.youtube.com/watch?v=short",
        },
        {
            "id": "mid",
            "title": "Mid",
            "duration": 700,
            "view_count": 1_000,
            "webpage_url": "https://www.youtube.com/watch?v=mid",
        },
        {
            "id": "top",
            "title": "Filme Odisseia completo",
            "duration": 7200,
            "view_count": 5_000_000,
            "channel": "Canal X",
            "webpage_url": "https://www.youtube.com/watch?v=top",
        },
    ]
    hit = pick_top_viewed_among_long(entries, min_duration_sec=600)
    assert hit is not None
    assert hit.url.endswith("v=top")
    assert hit.title == "Filme Odisseia completo"
    assert hit.view_count == 5_000_000
    assert hit.duration_sec == 7200
    assert hit.channel == "Canal X"


def test_pick_top_viewed_among_long_builds_url_from_id() -> None:
    entries = [
        {"id": "abc123", "title": "Long", "duration": 900, "view_count": 10},
    ]
    hit = pick_top_viewed_among_long(entries, min_duration_sec=600)
    assert hit is not None
    assert hit.url == "https://www.youtube.com/watch?v=abc123"


def test_pick_top_viewed_among_long_none_when_all_short() -> None:
    entries = [
        {"id": "a", "title": "A", "duration": 59, "view_count": 999},
        {"id": "b", "title": "B", "duration": 500, "view_count": 50},
    ]
    assert pick_top_viewed_among_long(entries, min_duration_sec=600) is None


def test_pick_top_viewed_among_long_skips_missing_duration() -> None:
    entries = [
        {"id": "nodur", "title": "No dur", "view_count": 9_999_999},
        {"id": "ok", "title": "Ok", "duration": 601, "view_count": 2},
    ]
    hit = pick_top_viewed_among_long(entries, min_duration_sec=600)
    assert hit is not None
    assert hit.url.endswith("v=ok")


def test_source_score_can_prefer_relevant_spoken_video_over_raw_views() -> None:
    entries = [
        {
            "id": "music",
            "title": "Guitarra música completa official audio",
            "duration": 3600,
            "view_count": 50_000_000,
        },
        {
            "id": "interview",
            "title": "Entrevista sobre guitarra e improvisação",
            "duration": 3600,
            "view_count": 20_000,
        },
    ]

    ranked = rank_theme_sources(entries, query="guitarra improvisação", min_duration_sec=600)

    assert ranked[0].url.endswith("v=interview")
    assert ranked[0].source_score > ranked[1].source_score
    assert ranked[0].format_score > ranked[1].format_score


def test_source_score_handles_missing_metadata_and_history_exclusion() -> None:
    ranked = rank_theme_sources(
        [
            {"id": "old", "title": "Podcast de guitarra"},
            {"id": "new", "title": "Conversa sobre blues"},
        ],
        query="blues",
        min_duration_sec=600,
        exclude_source_keys={"youtube:old"},
    )

    assert len(ranked) == 1
    assert ranked[0].url.endswith("v=new")
    assert ranked[0].duration_sec == 0
