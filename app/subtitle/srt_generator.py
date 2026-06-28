import re
from pathlib import Path

from app.subtitle.formatter import seconds_to_srt_timestamp

_MAX_CAPTION_CHARS = 160
_MAX_LINE_CHARS = 44
_MAX_LINES = 2

_REPEAT_CHAR_RE = re.compile(r"(.)\1{6,}")
_REPEAT_WORD_RE = re.compile(r"\b(\w+)(?:\s+\1){3,}\b", flags=re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _clean_caption_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = t.replace("\u200b", " ")
    t = re.sub(_WS_RE, " ", t).strip()
    # Evita "alucinação" comum: caracteres/strings repetidas por muito tempo.
    t = _REPEAT_CHAR_RE.sub(r"\1\1\1", t)
    t = _REPEAT_WORD_RE.sub(r"\1", t)
    # Limite duro de tamanho (antes de quebrar linha).
    if len(t) > _MAX_CAPTION_CHARS:
        t = t[: _MAX_CAPTION_CHARS - 1].rstrip() + "…"
    return t


def _wrap_two_lines(text: str) -> str:
    """Quebra texto em no máximo 2 linhas (padrão TikTok)."""
    t = _clean_caption_text(text)
    if not t:
        return ""
    if "\n" in t:
        t = re.sub(r"\s*\n\s*", " ", t).strip()

    words = t.split()
    lines: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        add = (1 if cur else 0) + len(w)
        if cur and (cur_len + add) > _MAX_LINE_CHARS:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
            if len(lines) >= _MAX_LINES:
                break
        else:
            cur.append(w)
            cur_len += add
    if len(lines) < _MAX_LINES and cur:
        lines.append(" ".join(cur))

    out = "\n".join(lines[:_MAX_LINES]).strip()
    if not out:
        return ""
    # Se truncou palavras, coloca reticências.
    if len(out.replace("\n", " ")) < len(t) and not out.endswith("…"):
        out = out.rstrip(". ") + "…"
    return out


def generate_srt(
    segments: list[dict],
    output_path: str,
    offset: float = 0.0,
    playback_speed: float = 1.0,
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        idx = 1
        for seg in segments:
            start = (seg["start"] - offset) / playback_speed
            end = (seg["end"] - offset) / playback_speed
            if end <= start:
                continue
            body = _wrap_two_lines(str(seg.get("text", "")))
            if not body:
                continue
            f.write(
                f"{idx}\n"
                f"{seconds_to_srt_timestamp(start)} --> {seconds_to_srt_timestamp(end)}\n"
                f"{body}\n\n"
            )
            idx += 1

    return output_path
