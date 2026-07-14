"""Converte SRT em ASS com PlayRes fixo (9:16), para libass posicionar no rodapé com fonte pequena."""
from __future__ import annotations

import re
from pathlib import Path


def _srt_timestamp_to_ass(srt_ts: str) -> str:
    srt_ts = srt_ts.strip()
    hms, ms = srt_ts.split(",", 1)
    h, m, s = hms.split(":")
    cs = min(99, int(int(ms) / 10))
    return f"{int(h)}:{int(m):02d}:{int(s):02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    t = text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
    return t.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")


def _iter_srt_entries(content: str):
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        i = 0
        if lines[0].strip().isdigit():
            i = 1
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        left, right = [x.strip() for x in lines[i].split("-->", 1)]
        body = "\n".join(lines[i + 1 :]).strip()
        yield left, right, body


def write_tiktok_ass_from_srt(
    srt_path: str,
    ass_path: str,
    *,
    play_res_x: int,
    play_res_y: int,
    font_name: str,
    font_size: int,
    primary_ass: str,
    back_ass: str,
    margin_l: int,
    margin_r: int,
    margin_v: int,
    alignment: int,
) -> str:
    """
    alignment: ASS V4+ (2 = inferior centro, 8 = superior centro).
    """
    Path(ass_path).parent.mkdir(parents=True, exist_ok=True)
    content = Path(srt_path).read_text(encoding="utf-8-sig")

    # Style completo evita fallback do libass (fonte enorme / topo) com SRT+force_style.
    style = (
        f"Style: Default,{font_name},{font_size},{primary_ass},&H000000FF,&H00000000,"
        f"{back_ass},1,0,0,0,100,100,0,0,1,4,1,{alignment},"
        f"{margin_l},{margin_r},{margin_v},1"
    )

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events: list[str] = []
    for start_s, end_s, body in _iter_srt_entries(content):
        if not body:
            continue
        a0 = _srt_timestamp_to_ass(start_s)
        a1 = _srt_timestamp_to_ass(end_s)
        txt = _escape_ass_text(body)
        events.append(f"Dialogue: 0,{a0},{a1},Default,,0,0,0,,{txt}")

    Path(ass_path).write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return ass_path
