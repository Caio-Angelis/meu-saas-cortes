"""Tradução em lote (delimitador): ordenação e fallback."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def tr(monkeypatch):
    import app.ai_integrations.translator as tr_mod

    importlib.reload(tr_mod)
    monkeypatch.setattr(tr_mod, "TRANSLATE_BATCH", True)
    monkeypatch.setattr(tr_mod, "TRANSLATE_BATCH_MAX_CHARS", 10_000)
    return tr_mod


def test_batch_joins_segments(tr):
    calls: list[str] = []

    def fake_translate(text: str, source: str = "auto", target: str = "pt"):
        calls.append(text)
        if "\x1e" in text:
            a, b = text.split("\x1e", 1)
            return f"T({a})\x1eT({b})"
        return text

    tr.translate_text = fake_translate
    segments = [{"s": i, "text": chr(97 + i)} for i in range(2)]
    out = tr.translate_segments(segments, "en", "pt")
    assert [s["text"] for s in out] == ["T(a)", "T(b)"]
    assert len(calls) >= 1
    assert calls[0].count("\x1e") == 1


def test_batch_fallback_when_split_mismatch(tr):
    def fake_translate(text: str, source: str = "auto", target: str = "pt"):
        if "\x1e" in text:
            return "incomplete"
        return {"one": "1", "two": "2"}.get(text, text)

    tr.translate_text = fake_translate
    segments = [{"i": j, "text": t} for j, t in enumerate(["one", "two"], start=1)]
    out = tr.translate_segments(segments, "auto", "pt")
    assert [s["text"] for s in out] == ["1", "2"]
