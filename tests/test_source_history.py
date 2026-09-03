from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.core.source_history import (
    DuplicateSourceError,
    SourceHistory,
    canonical_source_key,
)
from app.download import ytdlp_download as ytdl


def test_canonical_source_key_unifies_youtube_url_variants() -> None:
    key = canonical_source_key("https://www.youtube.com/watch?v=abc123&feature=share")
    assert key == canonical_source_key("https://youtu.be/abc123?si=tracking")
    assert key == canonical_source_key("https://www.youtube.com/shorts/abc123")


def test_canonical_source_key_normalizes_generic_url() -> None:
    assert canonical_source_key("HTTPS://Example.COM/video/?utm_source=x&part=2") == (
        "url:https://example.com/video?part=2"
    )


def test_source_history_blocks_equivalent_source_and_retries_failures(tmp_path) -> None:
    history = SourceHistory(tmp_path / "source_history.sqlite")
    url = "https://www.youtube.com/watch?v=abc123"

    key = history.claim(url)
    with pytest.raises(DuplicateSourceError):
        history.claim("https://youtu.be/abc123?feature=share")

    history.mark_failed(key, "erro temporário")
    assert not history.is_used(url)

    retried_key = history.claim("https://youtu.be/abc123")
    history.mark_downloaded(retried_key, downloaded_path="/tmp/video.mp4")
    assert history.is_used(url)
    assert retried_key in history.used_keys()


def test_source_history_returns_existing_download(tmp_path) -> None:
    history = SourceHistory(tmp_path / "source_history.sqlite")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    url = "https://www.youtube.com/watch?v=existing"

    key = history.claim(url)
    history.mark_downloaded(key, downloaded_path=str(video), channel="Canal")

    existing = history.get_downloaded(url)
    assert existing is not None
    assert existing.path == str(video)
    assert existing.channel == "Canal"


def test_source_history_allows_only_one_concurrent_claim(tmp_path) -> None:
    db_path = tmp_path / "source_history.sqlite"
    histories = [SourceHistory(db_path), SourceHistory(db_path)]

    def try_claim(history: SourceHistory) -> str:
        try:
            return history.claim("https://www.youtube.com/watch?v=concurrent")
        except DuplicateSourceError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(try_claim, histories))

    assert sum(result == "duplicate" for result in results) == 1
    assert sum(result.startswith("youtube:") for result in results) == 1


def test_search_theme_excludes_sources_already_in_history(monkeypatch) -> None:
    used_key = canonical_source_key("https://www.youtube.com/watch?v=used")

    class HistoryStub:
        def used_keys(self) -> set[str]:
            return {used_key}

    entries = [
        {
            "id": "used",
            "title": "Já usado",
            "duration": 3600,
            "view_count": 99_000,
            "webpage_url": "https://www.youtube.com/watch?v=used",
        },
        {
            "id": "new",
            "title": "Novo",
            "duration": 3600,
            "view_count": 100,
            "webpage_url": "https://www.youtube.com/watch?v=new",
        },
    ]
    monkeypatch.setattr(ytdl, "get_source_history", lambda: HistoryStub())
    monkeypatch.setattr(ytdl, "resolve_ytdlp_cmd", lambda: ("yt-dlp",))
    monkeypatch.setattr(
        ytdl.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="\n".join(json.dumps(item) for item in entries),
            stderr="",
        ),
    )

    hit = ytdl.search_youtube_top_by_views("tema", min_duration_sec=600, search_n=2)

    assert hit.url.endswith("v=new")


def test_download_video_records_source_before_and_after_download(monkeypatch, tmp_path) -> None:
    history = SourceHistory(tmp_path / "source_history.sqlite")
    monkeypatch.setattr(ytdl, "get_source_history", lambda: history)
    calls = 0

    def fake_download(url: str, dest_dir, *, no_playlist: bool = True):
        nonlocal calls
        calls += 1
        path = tmp_path / "video.mp4"
        path.write_bytes(b"video")
        return ytdl.DownloadResult(path=str(path))

    monkeypatch.setattr(ytdl, "_download_video_untracked", fake_download)
    first_url = "https://www.youtube.com/watch?v=abc123"
    first = ytdl.download_video(first_url, tmp_path)
    second = ytdl.download_video("https://youtu.be/abc123", tmp_path)

    assert first.path == second.path
    assert calls == 1
    assert history.is_used(first_url)
