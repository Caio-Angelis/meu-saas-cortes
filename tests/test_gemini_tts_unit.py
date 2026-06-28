"""Testes unitários do parser Gemini TTS (sem rede)."""

import base64

import pytest

from app.tts.gemini_tts import _extract_pcm_bytes, _part_inline_audio


def test_part_inline_audio_camel_case():
    pcm = b"\x00\x01" * 200
    part = {
        "inlineData": {
            "mimeType": "audio/L16;rate=24000",
            "data": base64.b64encode(pcm).decode(),
        }
    }
    assert _part_inline_audio(part) == pcm


def test_part_inline_audio_snake_case():
    pcm = b"\xff" * 300
    part = {
        "inline_data": {
            "mime_type": "audio/pcm",
            "data": base64.b64encode(pcm).decode(),
        }
    }
    assert _part_inline_audio(part) == pcm


def test_extract_pcm_skips_text_part_finds_audio_in_second():
    pcm = b"\xab\xcd" * 150
    body = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"text": "not audio"},
                        {
                            "inlineData": {
                                "mimeType": "audio/L16;rate=24000",
                                "data": base64.b64encode(pcm).decode(),
                            }
                        },
                    ]
                },
            }
        ]
    }
    assert _extract_pcm_bytes(body) == pcm


def test_extract_pcm_raises_when_only_text():
    with pytest.raises(RuntimeError, match="sem áudio inline"):
        _extract_pcm_bytes(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Hello only text"}]}}
                ]
            }
        )
