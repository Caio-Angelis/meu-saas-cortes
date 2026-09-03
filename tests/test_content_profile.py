from __future__ import annotations

import json
from pathlib import Path

from app.analytics.content_profile import (
    build_content_performance_profile,
    load_content_performance_profile,
)


def test_empty_report_is_safe_and_contains_no_retention_metric() -> None:
    profile = build_content_performance_profile({})

    assert profile.sample_count == 0
    assert profile.strong_topics == ()
    assert "retention" not in profile.to_dict()


def test_small_report_extracts_topics_entities_and_patterns() -> None:
    report = {
        "videos": [
            {
                "description": "Kiko Loureiro explica improvisação no blues #KikoLoureiro #Blues",
                "hashtags": ["KikoLoureiro", "Blues"],
                "duration": 50,
                "current_metrics": {"views": 1000, "likes": 100, "comments": 20, "shares": 10},
            },
            {
                "description": "Definição de escala musical #EscalaMusical",
                "duration": 50,
                "current_metrics": {"views": 100, "likes": 2, "comments": 0, "shares": 0},
            },
        ]
    }

    profile = build_content_performance_profile(report)

    assert profile.sample_count == 2
    assert any("blues" in value.casefold() for value in profile.strong_topics)
    assert any("kiko" in value.casefold() for value in profile.strong_entities)
    assert profile.strong_content_patterns
    assert "horário" in profile.prompt_summary()


def test_outlier_does_not_make_all_topics_equal() -> None:
    report = {
        "videos": [
            {
                "description": "História do rock #Rock",
                "current_metrics": {"views": 1_000_000, "likes": 1000},
            },
            {
                "description": "Aula fria de escala #Escalas",
                "current_metrics": {"views": 10, "likes": 1},
            },
            {
                "description": "Erro comum no improviso #Improvisacao",
                "current_metrics": {"views": 20, "likes": 5},
            },
            {
                "description": "Timbre de guitarra #Guitarra",
                "current_metrics": {"views": 30, "likes": 4},
            },
        ]
    }

    profile = build_content_performance_profile(report)

    assert profile.strong_topics
    assert profile.weak_topics
    assert profile.strong_topics != profile.weak_topics


def test_missing_optional_metrics_and_invalid_rows_do_not_break(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "videos": [
                    {"description": "Sem métricas", "duration": 50},
                    {"description": "Com views", "current_metrics": {"views": 4}},
                    {"description": "Com engagement", "engagement": 3.2},
                ]
            }
        ),
        encoding="utf-8",
    )

    profile = load_content_performance_profile(report_path)

    assert profile is not None
    assert profile.sample_count == 2
    assert "views" in profile.observed_metrics
    assert "engagement" in profile.observed_metrics
    assert "watch time" in profile.prompt_summary().casefold()
    assert "followers gained" not in profile.to_dict()


def test_invalid_report_path_is_optional(tmp_path: Path) -> None:
    assert load_content_performance_profile(tmp_path / "missing.json") is None
