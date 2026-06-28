"""Catálogo de vozes TTS (sem rede)."""

from __future__ import annotations

import pytest

from app.tts.tts_voices import resolve_voice, voice_id_from_label


def test_resolve_voice_gemini_prefix():
    opt = resolve_voice("gemini:Achernar")
    assert opt.provider == "gemini"
    assert opt.engine_voice == "Achernar"


def test_resolve_voice_edge_legacy_name():
    opt = resolve_voice("pt-BR-FranciscaNeural")
    assert opt.provider == "edge"
    assert opt.engine_voice == "pt-BR-FranciscaNeural"


def test_resolve_voice_empty_uses_default():
    opt = resolve_voice("")
    assert opt.provider in ("local", "edge", "gemini")
    assert opt.engine_voice


def test_resolve_voice_local_prefix(monkeypatch):
    monkeypatch.setattr("app.tts.tts_voices.local_tts_available", lambda: True)
    opt = resolve_voice("local:pf_dora")
    assert opt.provider == "local"
    assert opt.engine_voice == "pf_dora"


def test_resolve_local_without_package_raises(monkeypatch):
    monkeypatch.setattr("app.tts.tts_voices.local_tts_available", lambda: False)
    with pytest.raises(ValueError, match="install_local_tts"):
        resolve_voice("local:pf_dora")


def test_voice_id_from_label_roundtrip():
    opt = resolve_voice("edge:pt-BR-FranciscaNeural")
    vid = voice_id_from_label(opt.label)
    assert vid == opt.voice_id


def test_resolve_gemini_unknown_creates_dynamic_when_key_set(monkeypatch):
    monkeypatch.setattr("app.tts.tts_voices.gemini_tts_available", lambda: True)
    opt = resolve_voice("gemini:CustomVoice")
    assert opt.engine_voice == "CustomVoice"


def test_resolve_gemini_without_key_raises(monkeypatch):
    monkeypatch.setattr("app.tts.tts_voices.gemini_tts_available", lambda: False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        resolve_voice("gemini:Achernar")
