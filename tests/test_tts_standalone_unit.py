"""Testes unitários do TTS avulso (sem rede / Edge-TTS)."""

from __future__ import annotations

import pytest

from app.tts.tts_standalone import (
    DEFAULT_PREVIEW_PHRASE,
    normalize_tts_text,
    preview_sample_text,
    slug_output_stem,
)


def test_normalize_tts_text_strips():
    assert normalize_tts_text("  olá  ") == "olá"


def test_normalize_tts_text_empty_raises():
    with pytest.raises(ValueError, match="texto"):
        normalize_tts_text("   ")


def test_preview_sample_short_text():
    assert preview_sample_text("Oi mundo") == "Oi mundo"


def test_preview_sample_empty_uses_default():
    assert preview_sample_text("") == DEFAULT_PREVIEW_PHRASE


def test_preview_sample_truncates_long_text():
    long = "palavra " * 80
    sample = preview_sample_text(long)
    assert len(sample) <= 210
    assert sample.endswith("…")


def test_slug_output_stem():
    assert slug_output_stem("Olá, mundo!") == "Olá_mundo"
    assert slug_output_stem("   ") == "locucao"
