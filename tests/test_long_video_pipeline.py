from __future__ import annotations

from pathlib import Path

import pytest

import app.pipelines.cortes.pipeline as pipeline
from app.video_processing.video_splitter import VideoSplitResult


def test_run_pipeline_expands_one_hour_into_three_inputs_of_five_clips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(pipeline, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path / "output")

    def fake_split(video_path: str, output_dir: str | Path) -> VideoSplitResult:
        output_dir = Path(output_dir)
        paths = tuple(str(output_dir / f"part_{i}.mp4") for i in range(1, 4))
        return VideoSplitResult(
            paths=paths,
            source_duration_sec=3600.0,
            discarded_remainder_sec=0.0,
            was_split=True,
        )

    captured: dict[str, object] = {}

    def fake_run_pipeline_expanded(**kwargs):
        captured.update(kwargs)
        inputs = kwargs["video_path"]
        assert isinstance(inputs, list)
        return [f"clip_{i}.mp4" for i in range(len(inputs) * 5)]

    monkeypatch.setattr(pipeline, "split_video_into_chunks", fake_split)
    monkeypatch.setattr(pipeline, "_run_pipeline_expanded", fake_run_pipeline_expanded)

    results = pipeline.run_pipeline(source)

    assert len(results) == 15
    expanded = captured["video_path"]
    assert isinstance(expanded, list)
    assert len(expanded) == 3
    assert all(Path(path).parent.name == "source_001" for path in expanded)
    assert all(Path(path).name in {"part_1.mp4", "part_2.mp4", "part_3.mp4"} for path in expanded)
    assert not list((tmp_path / "temp").glob("_long_video_chunks_*"))
