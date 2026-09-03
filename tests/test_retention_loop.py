import json

import pytest

from app.analytics.retention_loop import (
    RetentionLoopError,
    analyze_retention_report_file,
    build_retention_insight,
    load_growth_profile,
    save_growth_profile,
)


def make_video(
    *,
    views: float,
    engagement: float,
    bucket: str,
    weekday: str,
    hour: int,
    question: bool,
    tags: list[str],
    gain: int,
) -> dict:
    return {
        "description": f"Clip de teste #{tags[0]} #fyp",
        "duration_bucket": bucket,
        "publication_weekday": weekday,
        "publication_hour": hour,
        "hashtags": tags + ["fyp"],
        "hashtags_count": len(tags) + 1,
        "has_question_mark": question,
        "has_exclamation_mark": False,
        "has_emojis": False,
        "current_metrics": {
            "views": views,
            "likes": views * engagement / 100,
            "comments": 1,
            "shares": 0,
            "engagement_rate": engagement,
        },
        "account_followers_near_publish": 100,
        "account_followers_24h_after_publish": 100 + gain,
    }


def report() -> dict:
    strong = [
        make_video(
            views=1000 + i * 100,
            engagement=8.0,
            bucket="46-60s",
            weekday="quarta-feira",
            hour=21,
            question=True,
            tags=["violao"],
            gain=5,
        )
        for i in range(4)
    ]
    weak = [
        make_video(
            views=100 - i * 10,
            engagement=4.0,
            bucket="0-30s",
            weekday="segunda-feira",
            hour=6,
            question=False,
            tags=["teoria"],
            gain=0,
        )
        for i in range(4)
    ]
    return {"videos": strong + weak}


def test_duration_signal_and_recommendation():
    insight = build_retention_insight(report())
    assert insight.duration_signals[0].bucket == "46-60s"
    assert insight.recommended_clip_duration_sec == 53


def test_post_windows_ranked():
    insight = build_retention_insight(report())
    weekdays = [w for w in insight.post_windows if w.kind == "weekday"]
    hours = [w for w in insight.post_windows if w.kind == "hour"]
    assert weekdays[0].label == "quarta-feira"
    assert hours[0].label == "21h"


def test_caption_lift_direction():
    insight = build_retention_insight(report())
    question = next(c for c in insight.caption_signals if c.feature == "pergunta na legenda")
    assert question.lift == 2.0


def test_follower_magnets():
    insight = build_retention_insight(report())
    assert insight.follower_magnets[0].topic == "violao"
    assert insight.follower_magnets[0].followers_gained == 20


def test_summary_mentions_key_signals():
    insight = build_retention_insight(report())
    assert "46-60s" in insight.summary
    assert "53s" in insight.summary
    assert "violao" in insight.summary


def test_save_and_load_growth_profile(tmp_path):
    insight = build_retention_insight(report())
    target = save_growth_profile(insight, tmp_path / "growth_profile.json")
    loaded = load_growth_profile(target)
    assert loaded is not None
    assert loaded["recommended_clip_duration_sec"] == 53
    assert loaded["best_post_windows"][0]["label"] == "quarta-feira"


def test_load_missing_profile_returns_none(tmp_path):
    assert load_growth_profile(tmp_path / "nao_existe.json") is None


def test_insufficient_samples_raises():
    with pytest.raises(RetentionLoopError):
        build_retention_insight({"videos": report()["videos"][:2]})


def test_analyze_report_file(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report()), encoding="utf-8")
    insight = analyze_retention_report_file(path)
    assert insight.video_count == 8
