from __future__ import annotations

import json
from pathlib import Path

import app.pipelines.cortes.pipeline as pipeline


def test_manifest_keeps_selection_explainability_without_private_cache_key(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)
    candidates = [
        {
            "start": 1,
            "end": 51,
            "viral_score": 8.7,
            "category": "broad_appeal",
            "selected": True,
            "ranking_position": 1,
            "discard_reason": "",
        },
        {
            "start": 61,
            "end": 111,
            "viral_score": 6.2,
            "category": "practical_value",
            "selected": False,
            "ranking_position": 2,
            "discard_reason": "semantic_duplicate_of_higher_score",
        },
    ]
    manifest = pipeline._write_run_manifest(
        video_path="/tmp/source.mp4",
        video_name="source",
        video_fp="fingerprint",
        options={"target_language": "pt"},
        cache_hits={
            "segments": True,
            "moments": False,
            "_selection": {
                "profile": "tiktok_growth",
                "candidates_considered": candidates,
            },
        },
        moments=[candidates[0]],
        outputs=["/tmp/clip.mp4"],
    )

    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    assert data["selection"]["profile"] == "tiktok_growth"
    assert data["selection"]["candidates_considered"] == candidates
    assert "_selection" not in data["cache_hits"]
