from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import app.video_processing.video_splitter as splitter


def test_full_chunk_count_uses_only_complete_twenty_minute_blocks() -> None:
    assert splitter._full_chunk_count(1200.0, 1200.0) == 1
    assert splitter._full_chunk_count(1200.01, 1200.0) == 1
    assert splitter._full_chunk_count(2399.999, 1200.0) == 2
    assert splitter._full_chunk_count(3600.0, 1200.0) == 3
    assert splitter._full_chunk_count(4020.0, 1200.0) == 3


def test_short_video_is_kept_intact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(splitter, "probe_video_duration_seconds", lambda _path: 1200.0)

    result = splitter.split_video_into_chunks(source, tmp_path / "chunks")

    assert result.was_split is False
    assert result.paths == (str(source.resolve()),)
    assert result.discarded_remainder_sec == 0.0
    assert not (tmp_path / "chunks").exists()


def test_one_hour_video_generates_three_chunks_and_discards_remainder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    commands: list[list[str]] = []
    monkeypatch.setattr(splitter, "probe_video_duration_seconds", lambda _path: 4020.0)

    def fake_run(command, **_kwargs):
        command = list(command)
        commands.append(command)
        Path(command[-1]).write_bytes(b"chunk")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(splitter, "run_cancelable", fake_run)

    result = splitter.split_video_into_chunks(source, tmp_path / "chunks")

    assert result.was_split is True
    assert len(result.paths) == 3
    assert result.discarded_remainder_sec == pytest.approx(420.0)
    assert all(Path(path).is_file() for path in result.paths)
    assert [command[command.index("-ss") + 1] for command in commands] == ["0.000", "1200.000", "2400.000"]
    assert all(command[command.index("-t") + 1] == "1200.000" for command in commands)


def test_split_directory_cleanup_does_not_touch_source(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    chunks = tmp_path / "chunks"
    source.write_bytes(b"source")
    chunks.mkdir()
    (chunks / "part.mp4").write_bytes(b"chunk")

    splitter.cleanup_split_directory(chunks)

    assert source.is_file()
    assert not chunks.exists()
