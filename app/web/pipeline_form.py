"""Parâmetros do pipeline a partir do formulário web."""

from __future__ import annotations

import math
from typing import Any


def normalize_hex(value: str, default: str) -> str:
    v = (value or "").strip() or default
    if not v.startswith("#"):
        v = "#" + v
    return v.upper() if len(v) == 7 else default


def normalize_optional_float(value: str | float | int | None, field_name: str) -> float | None:
    """Normaliza segundos opcionais do corte manual sem aceitar NaN/infinito."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        number = float(raw.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{field_name} deve ser um número em segundos.") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field_name} deve ser um número finito maior ou igual a zero.")
    return round(number, 3)


def normalize_overlay_text(value: str | None, *, max_chars: int) -> str:
    """Limpa texto que será renderizado pelo FFmpeg e limita o tamanho do overlay."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:max_chars]


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
    hook_text: str = "",
    outro_text: str = "",
    clip_start: str | float | int | None = "",
    clip_end: str | float | int | None = "",
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
        "hook_text": normalize_overlay_text(hook_text, max_chars=180),
        "outro_text": normalize_overlay_text(outro_text, max_chars=640),
        "manual_start": normalize_optional_float(clip_start, "clip_start"),
        "manual_end": normalize_optional_float(clip_end, "clip_end"),
    }
    start = opts["manual_start"]
    end = opts["manual_end"]
    if (start is None) != (end is None):
        raise ValueError("Preencha clip_start e clip_end juntos para usar um corte manual.")
    if start is not None and end is not None and end <= start:
        raise ValueError("clip_end deve ser maior que clip_start.")
    return opts
