"""Parâmetros do pipeline a partir do formulário web."""

from __future__ import annotations

from typing import Any


def normalize_hex(value: str, default: str) -> str:
    v = (value or "").strip() or default
    if not v.startswith("#"):
        v = "#" + v
    return v.upper() if len(v) == 7 else default


def pipeline_kwargs_from_form(
    *,
    lang: str,
    position: str,
    font: str,
    color: str,
    bg_color: str,
    opacity: int,
    dub_en: bool,
    dub_pt: bool,
    tts_voice: str,
    export_zip: bool = False,
) -> dict[str, Any]:
    dub_to = None
    if dub_en:
        dub_to = "en"
    elif dub_pt:
        dub_to = "pt"
    opts: dict[str, Any] = {
        "target_language": lang,
        "posicao": position,
        "fonte": (font or "Arial").strip() or "Arial",
        "cor_letra": normalize_hex(color, "#FFFF00"),
        "cor_fundo": normalize_hex(bg_color, "#000000"),
        "opacidade": max(0, min(100, int(opacity))),
        "dub_to": dub_to,
        "tts_voice": (tts_voice or "").strip() or None,
        "export_zip": export_zip,
    }
    return opts
