"""Normalização compartilhada das legendas de postagem."""

from __future__ import annotations

import re

_URL_RE = re.compile(r"(?i)(?<![\w@])(?:https?://|www\.)[^\s<>{}\[\]]+")
_SPACES_RE = re.compile(r"[ \t]{2,}")
_LINK_LABEL_ONLY_RE = re.compile(
    r"(?i)^\s*(?:canal|fonte|source|link|vídeo(?:\s+original)?|video(?:\s+original)?|original)\s*:?\s*$"
)


def remove_links_from_caption(text: str) -> str:
    """Remove URLs http(s)/www sem alterar o restante da legenda ou as hashtags."""
    output: list[str] = []
    previous_blank = False
    for raw_line in (text or "").splitlines():
        had_link = bool(_URL_RE.search(raw_line))
        cleaned = _URL_RE.sub("", raw_line)
        cleaned = _SPACES_RE.sub(" ", cleaned).rstrip()
        if had_link and _LINK_LABEL_ONLY_RE.fullmatch(cleaned):
            continue
        if had_link and not cleaned.strip(" \t-\u2013\u2014:;,.|()[]{}"):
            continue
        is_blank = not cleaned.strip()
        if is_blank and previous_blank:
            continue
        output.append(cleaned)
        previous_blank = is_blank
    return "\n".join(output).strip()
