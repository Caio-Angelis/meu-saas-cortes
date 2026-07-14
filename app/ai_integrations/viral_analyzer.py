import json
import logging
import math
import re

from app.ai_integrations.groq_chat import groq_user_message_text
from app.core.cancel import raise_if_cancelled
from app.core.config import CLIP_DURATION, VIRAL_CLIPS_COUNT

_log = logging.getLogger(__name__)

_MAX_TRANSCRIPT_CHARS = 24_000
# Refino de janelas percorre segmentos O(n) por clipe; transcrições longas geram milhares de segmentos.
_MAX_SEGMENTS_FOR_REFINE = 3000


def _coarse_segments_for_refine(segments: list[dict], *, max_segments: int = _MAX_SEGMENTS_FOR_REFINE) -> list[dict]:
    """
    Agrega segmentos adjacentes para o refino de janelas (mantém timestamps reais).
    Evita que _refine_clip_window faça dezenas de milhões de iterações em vídeos longos.
    """
    if max_segments < 8 or len(segments) <= max_segments:
        return segments
    n = len(segments)
    group = (n + max_segments - 1) // max_segments
    out: list[dict] = []
    i = 0
    while i < n:
        j = min(n, i + group)
        chunk = segments[i:j]
        texts = [str(x.get("text") or "").strip() for x in chunk]
        joined = " ".join(t for t in texts if t).strip()
        mid = chunk[len(chunk) // 2]
        out.append(
            {
                "start": float(chunk[0]["start"]),
                "end": float(chunk[-1]["end"]),
                "text": joined or str(mid.get("text") or "").strip(),
            }
        )
        i = j
    return out


def _build_transcript_text(segments: list[dict]) -> str:
    if not segments:
        return ""

    linhas_full = [f"[{seg['start']:.0f}s] {seg['text']}" for seg in segments]
    total = sum(len(x) + 1 for x in linhas_full)
    if total <= _MAX_TRANSCRIPT_CHARS:
        return "\n".join(linhas_full)
    # Não cabe tudo: amostra uniformemente do início ao fim (cobre o vídeo inteiro).
    keep = max(1, int(len(linhas_full) * _MAX_TRANSCRIPT_CHARS / total))
    step = max(1, math.ceil(len(linhas_full) / keep))
    amostra = linhas_full[::step]
    return "\n".join(amostra)


def _normalize_hook(phrase: str) -> str:
    words = (phrase or "").strip().split()
    return " ".join(words[:5]) if words else ""


_BOUNDARY_SEARCH_SEC = 7.0
_START_PREROLL_SEC = 2.0
_MIN_GAP_FOR_NEW_THOUGHT_SEC = 0.7


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _ends_like_sentence(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and t[-1] in (".", "!", "?", "…")


def _refine_clip_window(
    *,
    start: float,
    segments: list[dict],
    target_len: float,
) -> tuple[float, float]:
    """
    Refina uma janela para ter mais "começo/meio/fim":
    - start: puxa um pouco para trás até um limite natural (pausa)
    - end: tenta encerrar num fim de frase próximo do target
    """
    if not segments:
        return start, start + target_len

    video_end = float(segments[-1]["end"])
    start = _clamp(float(start), 0.0, max(0.0, video_end - 0.5))
    target_end = _clamp(start + float(target_len), 0.2, video_end)

    # Ajuste do start: tenta começar após uma pausa (novo pensamento), dentro de um preroll curto.
    desired_start = max(0.0, start - _START_PREROLL_SEC)
    best_start = start
    prev_end = None
    for seg in segments:
        s0 = float(seg["start"])
        s1 = float(seg["end"])
        if s1 < desired_start:
            prev_end = s1
            continue
        if s0 > start:
            break
        if prev_end is not None and (s0 - prev_end) >= _MIN_GAP_FOR_NEW_THOUGHT_SEC:
            best_start = s0
        prev_end = s1
    start = _clamp(best_start, 0.0, max(0.0, target_end - 0.3))

    # Ajuste do end: tenta encerrar em fim de frase perto do target_end.
    search_lo = _clamp(target_end - _BOUNDARY_SEARCH_SEC, start + 1.0, video_end)
    search_hi = _clamp(target_end + _BOUNDARY_SEARCH_SEC, start + 1.0, video_end)
    best_end = target_end
    best_score = float("inf")
    for seg in segments:
        s0 = float(seg["start"])
        s1 = float(seg["end"])
        if s1 < search_lo:
            continue
        if s0 > search_hi:
            break
        if not _ends_like_sentence(str(seg.get("text", ""))):
            continue
        dist = abs(s1 - target_end)
        early_penalty = 2.0 if s1 < target_end else 0.0
        score = dist + early_penalty
        if score < best_score:
            best_score = score
            best_end = s1

    end = _clamp(best_end, start + 4.0, video_end)
    return start, end


def _extract_json_array(text: str) -> str:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON array found in model response")

    return text[start:end + 1]


def _sanitize_json(raw: str) -> str:
    in_string = False
    escape_next = False
    chars = []

    for char in raw:
        if escape_next:
            chars.append(char)
            escape_next = False
        elif char == "\\":
            chars.append(char)
            escape_next = True
        elif char == '"':
            chars.append(char)
            in_string = not in_string
        elif in_string and char in "\n\r\t":
            chars.append(" ")
        else:
            chars.append(char)

    return "".join(chars)


def _parse_moments(content: str) -> list:
    raw = _extract_json_array(content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_sanitize_json(raw))


def analyze_viral_moments(
    segments: list[dict],
    output_language: str = "pt",
) -> list[dict]:
    if not segments:
        raise ValueError("Empty transcript — cannot analyze viral moments.")

    total_duration = segments[-1]["end"]
    transcript_text = _build_transcript_text(segments)

    lang = (output_language or "pt").strip().lower()
    if lang == "pt":
        lang_rules = (
            "- The 'reason' field: one SHORT single line in **Brazilian Portuguese (pt-BR)**, "
            "no line breaks\n"
            "- The 'hook' field: punchy on-screen phrase in **Brazilian Portuguese**, "
            "AT MOST 5 words, main pain point or desire for this clip\n"
        )
    else:
        lang_rules = (
            "- The 'reason' field: one SHORT single line in **English**, no line breaks\n"
            "- The 'hook' field: punchy on-screen phrase in **English**, "
            "AT MOST 5 words, main pain point or desire for this clip\n"
        )

    prompt = (
        f"You are a viral content expert. Analyze this video transcript and identify the "
        f"{VIRAL_CLIPS_COUNT} best moments that would go viral on social media.\n\n"
        f"Rules:\n"
        f"- Each clip must start at a timestamp shown in the transcript [Xs]\n"
        f"- Each clip should be around {CLIP_DURATION} seconds\n"
        f"- Clips must not overlap\n"
        f"- Prefer moments with high emotional impact, humor, surprising revelations, or strong opinions\n"
        f"{lang_rules}"
        f"- Return ONLY a valid JSON array, no markdown, no explanation\n\n"
        f"Format: "
        f'[{{"start": 10, "end": 60, "reason": "brief reason", "hook": "max five words"}}, ...]\n\n'
        f"Transcript (total: {total_duration:.0f}s):\n{transcript_text}"
    )

    _log.info(
        "Chamando Groq para análise viral (transcrição: %s segmentos, ~%.0fs).",
        len(segments),
        float(segments[-1]["end"]),
    )
    content = groq_user_message_text(
        prompt,
        temperature=0.3,
        max_tokens=1024,
        none_as_empty=False,
        retry_label="Groq",
        bad_request_runtime=lambda e: RuntimeError(f"Prompt muito longo para o modelo: {e}"),
        rate_limit_message=(
            "Groq rate limit excedido. Aguarde alguns minutos e tente novamente."
        ),
    )
    if content is None or not str(content).strip():
        raise ValueError("Resposta vazia do modelo ao analisar momentos virais — tente de novo.")

    _log.info("Resposta recebida (%s caracteres); extraindo JSON…", len(content))
    try:
        raw_moments = _parse_moments(content)
    except Exception:
        _log.exception("Falha ao interpretar JSON dos momentos virais (primeiros 400 chars): %r", content[:400])
        raise
    if not isinstance(raw_moments, list):
        raise ValueError(f"Resposta do modelo não é uma lista JSON: {type(raw_moments).__name__}")
    refine_segments = _coarse_segments_for_refine(segments)
    if len(refine_segments) < len(segments):
        _log.info(
            "Refino de janelas: %s → %s segmentos (agregação para não travar em áudio longo).",
            len(segments),
            len(refine_segments),
        )

    validated: list[dict] = []
    for m in raw_moments[:VIRAL_CLIPS_COUNT]:
        raise_if_cancelled()
        start0 = float(m["start"])
        start, end = _refine_clip_window(
            start=start0,
            segments=refine_segments,
            target_len=float(CLIP_DURATION),
        )
        start = round(start, 3)
        end = round(end, 3)
        hook = _normalize_hook(str(m.get("hook", "")))
        if not hook:
            hook = _normalize_hook(str(m.get("reason", "")))
        validated.append({"start": start, "end": end, "reason": m.get("reason", ""), "hook": hook})

    # Remove sobreposições (mantém os primeiros momentos por ordem de início).
    validated.sort(key=lambda x: x["start"])
    non_overlapping: list[dict] = []
    last_end = -1.0
    for m in validated:
        if float(m["start"]) < last_end + 0.25:
            continue
        non_overlapping.append(m)
        last_end = float(m["end"])

    out = non_overlapping[:VIRAL_CLIPS_COUNT]
    _log.info("Análise viral concluída: %s clipe(s) após remoção de sobreposição.", len(out))
    return out
