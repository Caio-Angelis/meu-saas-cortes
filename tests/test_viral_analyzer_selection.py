from __future__ import annotations

import json

from app.ai_integrations import viral_analyzer as va


def _candidate(
    start: float,
    *,
    category: str = "broad_appeal",
    topic: str = "ideia diferente",
    quality: float = 8,
    **overrides: object,
) -> dict:
    item = {
        "start": start,
        "end": start + 50,
        "category": category,
        "topic": topic,
        "reason": f"Explicação sobre {topic}",
        "hook": f"Por que {topic}",
    }
    for field in va._POSITIVE_SCORE_FIELDS:
        item[field] = quality
    for field in va._PENALTY_FIELDS:
        item[field] = 0
    item.update(overrides)
    return item


def test_context_free_candidate_beats_previous_context_candidate() -> None:
    strong = _candidate(0, topic="erro de improvisação")
    dependent = _candidate(
        60,
        topic="erro de improvisação",
        needs_previous_context=9,
        incomplete_thought=7,
    )

    assert va.compute_viral_score(strong, "tiktok_growth") > va.compute_viral_score(
        dependent, "tiktok_growth"
    )


def test_strong_opening_and_payoff_beat_slow_or_interrupted_clip() -> None:
    strong = _candidate(0, topic="o erro que trava seu improviso", ending_payoff=10)
    weak = _candidate(
        60,
        topic="aula de escala",
        slow_start=8,
        weak_ending=8,
        incomplete_thought=9,
    )

    assert va.compute_viral_score(strong, "tiktok_growth") > va.compute_viral_score(
        weak, "tiktok_growth"
    )
    penalties = va._opening_penalties("Então, como eu estava falando, voltando ao assunto")
    assert penalties["needs_previous_context"] >= 9
    assert penalties["slow_start"] >= 7


def test_normalization_clamps_scores_timestamps_and_unknown_category() -> None:
    segments = [
        {"start": 0, "end": 10, "text": "A maioria das pessoas não percebe isso."},
        {"start": 10, "end": 20, "text": "Esse detalhe muda tudo no acorde."},
        {"start": 20, "end": 30, "text": "E essa é a conclusão."},
    ]

    candidate = va._normalize_candidate(
        {
            "start": -30,
            "end": 999,
            "category": "something_new",
            "hook": "A maioria das pessoas não percebe isso no acorde",
            "hook_strength": 99,
            "needs_previous_context": -4,
        },
        segments=segments,
        total_duration=30,
        target_len=20,
        index=0,
        selection_profile="tiktok_growth",
        performance_profile=None,
    )

    assert candidate is not None
    assert candidate["category"] == "broad_appeal"
    assert candidate["category_was_invalid"] is True
    assert 0 <= candidate["start"] < candidate["end"] <= 30
    assert 0 <= candidate["hook_strength"] <= 10
    assert candidate["needs_previous_context"] == 0
    assert 0 <= candidate["viral_score"] <= 10

    selected, all_candidates = va.rank_viral_candidates(
        [{"start": "bad", "end": 12, "category": "unknown"}], count=1
    )
    assert selected == []
    assert all_candidates[0]["discard_reason"] == "invalid_timestamps"


def test_overlapping_and_semantic_duplicates_are_discarded() -> None:
    selected, all_candidates = va.rank_viral_candidates(
        [
            _candidate(0, topic="erro de improvisação", quality=10),
            _candidate(20, topic="erro de improvisação", quality=9),
            _candidate(70, topic="erro de improvisação", quality=8),
            _candidate(140, topic="história do blues", category="curiosity", quality=7),
        ],
        count=5,
        selection_profile="tiktok_growth",
    )

    assert len(selected) == 2
    assert selected[0]["start"] == 0
    reasons = {item["discard_reason"] for item in all_candidates if not item["selected"]}
    assert "overlap_with_higher_score" in reasons
    assert "semantic_duplicate_of_higher_score" in reasons


def test_five_selected_candidates_cover_distinct_roles_when_available() -> None:
    candidates = [
        _candidate(0, category="niche_hardcore", topic="arpejo avançado", quality=10),
        _candidate(60, category="practical_value", topic="aplicar pentatônica", quality=9),
        _candidate(120, category="curiosity", topic="por que esse acorde", quality=8),
        _candidate(180, category="controversy_opinion", topic="mito do equipamento", quality=7),
        _candidate(240, category="broad_appeal", topic="história do rock", quality=6),
    ]

    selected, _all_candidates = va.rank_viral_candidates(
        candidates, count=5, selection_profile="tiktok_growth"
    )

    assert len(selected) == 5
    assert {item["category"] for item in selected} == {
        "broad_appeal",
        "controversy_opinion",
        "curiosity",
        "practical_value",
        "niche_hardcore",
    }
    assert all(item["selected"] for item in selected)


def test_invalid_json_uses_safe_temporal_fallback(monkeypatch) -> None:
    segments = [
        {"start": float(index), "end": float(index + 1), "text": "Uma frase completa."}
        for index in range(0, 360)
    ]
    calls = 0

    def fake_groq(*args, **kwargs):
        nonlocal calls
        calls += 1
        return "not json"

    monkeypatch.setattr(va, "groq_user_message_text", fake_groq)
    result = va.analyze_viral_moments(
        segments,
        selection_profile="tiktok_growth",
        return_metadata=True,
    )

    assert calls == 3
    assert isinstance(result, va.ViralAnalysisResult)
    assert result.selected
    assert all(item.get("fallback_used") for item in result.candidates)


def test_incomplete_valid_response_is_completed_with_temporal_fallback(monkeypatch) -> None:
    segments = [
        {"start": float(index), "end": float(index + 1), "text": "Uma frase completa."}
        for index in range(0, 360)
    ]
    monkeypatch.setattr(
        va,
        "groq_user_message_text",
        lambda *args, **kwargs: json.dumps([_candidate(0, topic="primeiro trecho")]),
    )

    result = va.analyze_viral_moments(
        segments,
        selection_profile="tiktok_growth",
        return_metadata=True,
    )

    assert isinstance(result, va.ViralAnalysisResult)
    assert len(result.selected) == va.VIRAL_CLIPS_COUNT
    assert result.fallback_used is True
    assert any(item.get("fallback_used") for item in result.candidates)
    assert any(not item.get("fallback_used") for item in result.selected)


def test_model_response_can_be_object_with_candidates_and_local_ranking(monkeypatch) -> None:
    segments = [
        {"start": float(index), "end": float(index + 1), "text": "Uma frase completa."}
        for index in range(0, 120)
    ]
    response = {"candidates": [_candidate(0, topic="história do rock")]}
    monkeypatch.setattr(va, "groq_user_message_text", lambda *args, **kwargs: json.dumps(response))

    result = va.analyze_viral_moments(segments, return_metadata=True)

    assert isinstance(result, va.ViralAnalysisResult)
    assert result.candidates
    assert result.candidates[0]["ranking_position"] == 1


def test_contextual_cta_is_optional_and_matches_candidate_role() -> None:
    assert (
        va.contextual_cta_for_candidate(
            {"category": "controversy_opinion", "comment_potential": 8}
        )
        == "Você concorda?"
    )
    assert (
        va.contextual_cta_for_candidate(
            {"category": "practical_value", "practical_value": 8}
        )
        == "Salva pra testar depois"
    )
    assert va.contextual_cta_for_candidate({"category": "broad_appeal", "curiosity": 4}) == ""

    normalized = va._normalize_candidate(
        {
            "start": 0,
            "end": 10,
            "category": "broad_appeal",
            "cta": "",
        },
        segments=[{"start": 0, "end": 10, "text": "Uma frase completa."}],
        total_duration=10,
        target_len=10,
        index=0,
        selection_profile="tiktok_growth",
        performance_profile=None,
    )
    assert normalized is not None
    assert normalized["cta"] == ""
