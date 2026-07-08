from app.download.ytdlp_download import (
    attribution_from_ytdlp_info,
    collect_urls_from_lines,
    normalize_media_url,
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
