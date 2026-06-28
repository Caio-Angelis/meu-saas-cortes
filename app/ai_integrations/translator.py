from __future__ import annotations

from functools import lru_cache

from deep_translator import GoogleTranslator

from app.core.config import TRANSLATE_BATCH, TRANSLATE_BATCH_MAX_CHARS
from app.core.limits import translate_limiter, translate_retry_policy, with_retries

# Delimitador pouco provável em falas; se aparecer no texto, caímos no fallback por segmento.
_BATCH_SEP = "\x1e"


@lru_cache(maxsize=64)
def _translator_instance(source: str, target: str) -> GoogleTranslator:
    return GoogleTranslator(source=source, target=target)


def translate_text(text: str, source: str = "auto", target: str = "pt") -> str:
    clean = (text or "").strip()
    if not clean:
        return ""

    return _translate_text_cached(clean, source=source, target=target)


@lru_cache(maxsize=4096)
def _translate_text_cached(text: str, *, source: str, target: str) -> str:
    """
    - Reusa instância do tradutor por (source,target)
    - Cacheia traduções por texto (melhora muito em falas repetidas)
    - Limita concorrência para não “derrubar” o serviço em paralelo
    - Retries com backoff + jitter (rede instável / rate limit)
    """

    def _do() -> str:
        with translate_limiter.acquire():
            return _translator_instance(source, target).translate(text)

    def _retryable(_e: Exception) -> bool:
        # deep_translator levanta exceções genéricas; aqui tentamos suavizar falhas transitórias.
        return True

    try:
        out = with_retries(_do, policy=translate_retry_policy, should_retry=_retryable)
        return str(out).strip()
    except Exception:
        return text


def _translate_segments_one_by_one(
    segments: list[dict], source: str, target: str
) -> list[dict]:
    out: list[dict] = []
    for seg in segments:
        txt = translate_text(seg.get("text", ""), source, target)
        out.append({**seg, "text": txt})
    return out


def _segment_batches(
    segments: list[dict], max_chars: int
) -> list[list[int]]:
    batches: list[list[int]] = []
    cur: list[int] = []
    cur_len = 0
    overhead = 1

    for i, seg in enumerate(segments):
        txt = (seg.get("text") or "").strip()
        if _BATCH_SEP in txt:
            return []
        piece_len = len(txt)

        if not cur:
            cur = [i]
            cur_len = piece_len
            continue

        extra = overhead + piece_len
        if cur_len + extra > max_chars:
            batches.append(cur)
            cur = [i]
            cur_len = piece_len
        else:
            cur.append(i)
            cur_len += extra

    if cur:
        batches.append(cur)
    return batches


def translate_segments(
    segments: list[dict], source: str = "auto", target: str = "pt"
) -> list[dict]:
    if (
        not TRANSLATE_BATCH
        or len(segments) <= 1
        or TRANSLATE_BATCH_MAX_CHARS < 64
    ):
        return _translate_segments_one_by_one(segments, source, target)

    batches = _segment_batches(segments, TRANSLATE_BATCH_MAX_CHARS)
    if not batches:
        return _translate_segments_one_by_one(segments, source, target)

    out: list[dict | None] = [None] * len(segments)

    for batch in batches:
        if len(batch) == 1:
            i = batch[0]
            txt = translate_text(segments[i].get("text", ""), source, target)
            out[i] = {**segments[i], "text": txt}
            continue

        texts = [(segments[i].get("text") or "").strip() for i in batch]
        payload = _BATCH_SEP.join(texts)
        if len(payload) > TRANSLATE_BATCH_MAX_CHARS + 256:
            for i in batch:
                txt = translate_text(segments[i].get("text", ""), source, target)
                out[i] = {**segments[i], "text": txt}
            continue

        blob = translate_text(payload, source, target)
        parts = blob.split(_BATCH_SEP)

        if len(parts) != len(batch):
            for i in batch:
                txt = translate_text(segments[i].get("text", ""), source, target)
                out[i] = {**segments[i], "text": txt}
            continue

        for idx, i in enumerate(batch):
            out[i] = {**segments[i], "text": parts[idx].strip()}

    return [s if s is not None else segments[i] for i, s in enumerate(out)]
