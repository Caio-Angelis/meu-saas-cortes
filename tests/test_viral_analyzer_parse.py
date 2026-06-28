"""Parse de resposta do modelo e utilitários de janela (sem chamar Groq)."""

from __future__ import annotations

import json

import pytest

from app.ai_integrations import viral_analyzer as va


def test_extract_json_array_strips_fence() -> None:
    raw = '```json\n[{"start":1,"end":2,"reason":"a","hook":"b"}]\n```'
    inner = va._extract_json_array(raw)
    assert json.loads(inner)[0]["start"] == 1


def test_extract_json_array_plain() -> None:
    raw = 'noise [{"x": 1}] tail'
    assert json.loads(va._extract_json_array(raw))[0]["x"] == 1


def test_extract_json_array_missing_raises() -> None:
    with pytest.raises(ValueError, match="No JSON array"):
        va._extract_json_array("no brackets")


def test_sanitize_json_newline_inside_string() -> None:
    # JSON inválido: quebra de linha literal dentro de uma string entre aspas
    dirty = '[{"a":"line1' + "\n" + 'line2"}]'
    clean = va._sanitize_json(dirty)
    data = json.loads(clean)
    assert data[0]["a"] == "line1 line2"


def test_parse_moments_roundtrip() -> None:
    content = "```\n" + json.dumps([{"start": 0, "end": 5, "reason": "r", "hook": "h"}]) + "\n```"
    rows = va._parse_moments(content)
    assert rows[0]["hook"] == "h"


def test_normalize_hook_max_five_words() -> None:
    assert va._normalize_hook("one two three four five six seven") == "one two three four five"


def test_build_transcript_text_respects_limit() -> None:
    segs = [{"start": float(i), "end": float(i + 1), "text": "x" * 500} for i in range(50)]
    txt = va._build_transcript_text(segs)
    assert len(txt) <= va._MAX_TRANSCRIPT_CHARS + 200


def test_ends_like_sentence() -> None:
    assert va._ends_like_sentence("Ok.") is True
    assert va._ends_like_sentence("no") is False


def test_refine_clip_window_no_segments() -> None:
    s, e = va._refine_clip_window(start=1.0, segments=[], target_len=10.0)
    assert s == 1.0 and e == 11.0
