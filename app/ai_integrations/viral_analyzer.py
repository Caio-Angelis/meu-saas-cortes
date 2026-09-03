"""Descoberta, pontuação e seleção de cortes com foco em crescimento no TikTok.

O Groq faz uma única passagem de descoberta e avaliação. A ordenação final,
deduplicação e diversidade são determinísticas e locais, para que um campo
inválido da resposta não derrube o pipeline nem faça uma chamada por candidato.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any

from app.ai_integrations.groq_chat import groq_user_message_text
from app.analytics.content_profile import ContentPerformanceProfile
from app.core.cancel import raise_if_cancelled
from app.core.config import (
    CLIP_DURATION,
    VIRAL_CANDIDATE_COUNT,
    VIRAL_CLIPS_COUNT,
    VIRAL_SELECTION_PROFILE,
)

_log = logging.getLogger(__name__)

# A conta gratuita/developer do Groq pode reservar somente 8k tokens por minuto.
# O texto do prompt e `max_tokens` entram nessa reserva, então não basta limitar
# apenas o tamanho efetivamente devolvido pelo modelo.
_MAX_TRANSCRIPT_CHARS = 14_000
_MAX_ANALYSIS_COMPLETION_TOKENS = 2_800
# Refino de janelas percorre segmentos O(n) por clipe; transcrições longas geram milhares de segmentos.
_MAX_SEGMENTS_FOR_REFINE = 3000
_BOUNDARY_SEARCH_SEC = 7.0
_START_PREROLL_SEC = 2.0
_MIN_GAP_FOR_NEW_THOUGHT_SEC = 0.7
_MIN_CLIP_SEC = 4.0
_HOOK_DEFAULT_MAX_WORDS = 5  # compatibilidade com o helper público usado por integrações antigas
_HOOK_MAX_WORDS = 10
_CTA_MAX_WORDS = 8
VIRAL_ANALYZER_VERSION = "weighted_candidates_v4_groq_reasoning"

_POSITIVE_SCORE_FIELDS = (
    "hook_strength",
    "standalone_clarity",
    "curiosity",
    "controversy",
    "emotional_strength",
    "practical_value",
    "shareability",
    "comment_potential",
    "general_audience",
    "niche_relevance",
    "famous_person_or_topic",
    "story_progression",
    "ending_payoff",
    "retention_likelihood",
)

_PENALTY_FIELDS = (
    "needs_previous_context",
    "slow_start",
    "too_technical",
    "repetitive",
    "weak_ending",
    "filler",
    "generic_advice",
    "incomplete_thought",
)

_PENALTY_MAX_POINTS = {
    "needs_previous_context": 2.2,
    "slow_start": 1.2,
    "too_technical": 0.8,
    "repetitive": 0.9,
    "weak_ending": 1.7,
    "filler": 0.8,
    "generic_advice": 1.0,
    "incomplete_thought": 2.1,
}

_PROFILE_WEIGHTS: dict[str, dict[str, float]] = {
    "balanced": {
        "hook_strength": 0.16,
        "standalone_clarity": 0.15,
        "curiosity": 0.10,
        "controversy": 0.06,
        "emotional_strength": 0.07,
        "practical_value": 0.07,
        "shareability": 0.08,
        "comment_potential": 0.08,
        "general_audience": 0.07,
        "niche_relevance": 0.04,
        "famous_person_or_topic": 0.03,
        "story_progression": 0.07,
        "ending_payoff": 0.12,
        "retention_likelihood": 0.13,
    },
    "tiktok_growth": {
        "hook_strength": 0.18,
        "standalone_clarity": 0.16,
        "curiosity": 0.12,
        "controversy": 0.06,
        "emotional_strength": 0.07,
        "practical_value": 0.04,
        "shareability": 0.09,
        "comment_potential": 0.09,
        "general_audience": 0.10,
        "niche_relevance": 0.02,
        "famous_person_or_topic": 0.03,
        "story_progression": 0.07,
        "ending_payoff": 0.14,
        "retention_likelihood": 0.16,
    },
    "educational": {
        "hook_strength": 0.10,
        "standalone_clarity": 0.18,
        "curiosity": 0.08,
        "controversy": 0.03,
        "emotional_strength": 0.04,
        "practical_value": 0.18,
        "shareability": 0.06,
        "comment_potential": 0.05,
        "general_audience": 0.09,
        "niche_relevance": 0.08,
        "famous_person_or_topic": 0.02,
        "story_progression": 0.06,
        "ending_payoff": 0.14,
        "retention_likelihood": 0.15,
    },
}

_CATEGORY_ALIASES = {
    "broad": "broad_appeal",
    "broad appeal": "broad_appeal",
    "general": "broad_appeal",
    "controversy": "controversy_opinion",
    "opinion": "controversy_opinion",
    "controversy/opinion": "controversy_opinion",
    "controversy opinion": "controversy_opinion",
    "curiosity": "curiosity",
    "practical": "practical_value",
    "educational": "practical_value",
    "practical value": "practical_value",
    "niche": "niche_hardcore",
    "hardcore": "niche_hardcore",
    "niche/hardcore": "niche_hardcore",
    "niche hardcore": "niche_hardcore",
}
_VALID_CATEGORIES = frozenset(
    {"broad_appeal", "controversy_opinion", "curiosity", "practical_value", "niche_hardcore"}
)
_DIVERSITY_ORDER = (
    "broad_appeal",
    "controversy_opinion",
    "curiosity",
    "practical_value",
    "niche_hardcore",
)

_STOPWORDS = frozenset(
    {
        "a",
        "as",
        "ao",
        "aos",
        "de",
        "da",
        "das",
        "do",
        "dos",
        "e",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "o",
        "os",
        "um",
        "uma",
        "que",
        "se",
        "por",
        "para",
        "com",
        "como",
        "isso",
        "esse",
        "essa",
        "ele",
        "ela",
        "eles",
        "elas",
        "the",
        "and",
        "of",
        "to",
        "in",
        "on",
        "with",
        "this",
        "that",
    }
)

_KNOWN_ENTITIES = (
    "Kiko Loureiro",
    "Slash",
    "John Mayer",
    "Steve Vai",
    "Joe Satriani",
    "Jimi Hendrix",
    "Carlos Santana",
    "Eddie Van Halen",
    "Eric Clapton",
    "B.B. King",
    "Metallica",
    "Guns N' Roses",
    "Guns N Roses",
    "Megadeth",
    "Pink Floyd",
    "Led Zeppelin",
    "Iron Maiden",
    "Nirvana",
    "AC/DC",
)
_RECOGNIZABLE_TOPICS = frozenset(
    {
        "guitarra",
        "violão",
        "blues",
        "rock",
        "pentatônica",
        "pentatonica",
        "improvisação",
        "improvisacao",
        "acorde",
        "acordes",
        "escala",
        "escalas",
        "timbre",
        "riff",
        "solo",
        "composição",
        "composicao",
        "harmonia",
        "teoria musical",
    }
)


@dataclass(frozen=True)
class ViralAnalysisResult:
    """Resultado detalhado; o retorno padrão da função continua sendo uma lista."""

    selected: list[dict]
    candidates: list[dict]
    selection_profile: str
    performance_profile_key: str | None = None
    fallback_used: bool = False


def _coarse_segments_for_refine(
    segments: list[dict], *, max_segments: int = _MAX_SEGMENTS_FOR_REFINE
) -> list[dict]:
    """Agrega segmentos adjacentes para não explodir o custo do refino em vídeos longos."""

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

    lines = [f"[{seg['start']:.0f}s] {seg.get('text', '')}" for seg in segments]
    total = sum(len(line) + 1 for line in lines)
    if total <= _MAX_TRANSCRIPT_CHARS:
        return "\n".join(lines)
    # Amostra uniformemente do início ao fim, preservando os timestamps para o modelo.
    keep = max(1, int(len(lines) * _MAX_TRANSCRIPT_CHARS / total))
    step = max(1, math.ceil(len(lines) / keep))
    return "\n".join(lines[::step])


def _normalize_hook(phrase: str, *, max_words: int = _HOOK_DEFAULT_MAX_WORDS) -> str:
    words = re.sub(r"\s+", " ", (phrase or "").strip()).split()
    return " ".join(words[: max(1, int(max_words))]) if words else ""


def _normalize_cta(phrase: object) -> str:
    text = re.sub(r"\s+", " ", str(phrase or "").strip())
    if not text or "http://" in text.lower() or "https://" in text.lower():
        return ""
    return " ".join(text.split()[:_CTA_MAX_WORDS])


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _as_score(value: object, default: float = 5.0) -> float:
    if isinstance(value, bool):
        return 10.0 if value else 0.0
    if isinstance(value, (int, float)):
        numeric = float(value)
    else:
        match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
        if not match:
            return default
        try:
            numeric = float(match.group(0).replace(",", "."))
        except ValueError:
            return default
    if not math.isfinite(numeric):
        return default
    return round(_clamp(numeric, 0.0, 10.0), 2)


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _normalize_category(value: object) -> tuple[str, bool]:
    raw = re.sub(r"[_-]+", " ", str(value or "").strip().casefold())
    if raw in _VALID_CATEGORIES:
        return raw, False
    category = _CATEGORY_ALIASES.get(raw)
    if category:
        return category, False
    return "broad_appeal", bool(raw)


def _profile_name(value: str | None) -> str:
    name = (value or VIRAL_SELECTION_PROFILE or "balanced").strip().casefold()
    return name if name in _PROFILE_WEIGHTS else "balanced"


def _token_set(value: object) -> set[str]:
    normalized = re.sub(r"[^a-z0-9À-ÿ]+", " ", str(value or "").casefold())
    return {
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _known_entities(text: str) -> list[str]:
    normalized = text.casefold()
    found: list[str] = []
    for entity in _KNOWN_ENTITIES:
        if entity.casefold() in normalized:
            found.append(entity)
    return found


def _recognizable_topic_score(text: str) -> float:
    normalized = text.casefold()
    if any(term.casefold() in normalized for term in _KNOWN_ENTITIES):
        return 8.0
    if any(term.casefold() in normalized for term in _RECOGNIZABLE_TOPICS):
        return 5.5
    return 0.0


def _candidate_transcript_text(segments: list[dict], start: float, end: float) -> str:
    parts = []
    for segment in segments:
        s0 = _safe_float(segment.get("start"), 0.0) or 0.0
        s1 = _safe_float(segment.get("end"), s0) or s0
        if s1 > start and s0 < end:
            text = str(segment.get("text") or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts)


def _opening_penalties(text: str) -> dict[str, float]:
    clean = re.sub(r"\s+", " ", (text or "").strip()).casefold()
    if not clean:
        return {}
    strong_context = re.compile(
        r"^(?:como eu estava falando|voltando ao assunto|como eu disse antes|como falei antes|"
        r"nesse caso|aí depois|ai depois|isso aqui|então|entao|como vimos antes|"
        r"lá atrás|la atras|naquele momento)\b"
    )
    penalties: dict[str, float] = {}
    if strong_context.search(clean):
        penalties["needs_previous_context"] = 9.5
        penalties["slow_start"] = 7.0
    elif re.match(r"^(?:e|mas|porque|por isso|aí|ai|então|entao)\b", clean):
        penalties["needs_previous_context"] = 5.5
        penalties["slow_start"] = 4.5
    first_words = clean.split()[:4]
    if len(first_words) <= 2 and not clean.endswith(('.', '!', '?', '…')):
        penalties["slow_start"] = max(penalties.get("slow_start", 0.0), 5.0)
    if re.match(r"^(?:ele|ela|eles|elas|isso|aquilo|aquele|aquela)\b", clean):
        penalties["needs_previous_context"] = max(
            penalties.get("needs_previous_context", 0.0), 6.5
        )
    return penalties


def _performance_adjustment(
    candidate: dict, performance_profile: ContentPerformanceProfile | None
) -> float:
    if performance_profile is None or performance_profile.sample_count <= 0:
        return 0.0
    entity_values = candidate.get("entities", [])
    if not isinstance(entity_values, list):
        entity_values = [entity_values]
    searchable = _token_set(
        " ".join(
            [
                str(candidate.get("topic") or ""),
                str(candidate.get("reason") or ""),
                str(candidate.get("hook") or ""),
                " ".join(str(value) for value in entity_values),
            ]
        )
    )

    def overlaps(values: tuple[str, ...]) -> bool:
        return any(searchable & _token_set(value) for value in values)

    adjustment = 0.0
    if overlaps(performance_profile.strong_topics) or overlaps(performance_profile.strong_entities):
        adjustment += 0.45
    if overlaps(performance_profile.weak_topics) or overlaps(performance_profile.weak_entities):
        adjustment -= 0.25
    category = candidate.get("category")
    if performance_profile.preferred_broad_vs_technical == "broad" and category == "broad_appeal":
        adjustment += 0.20
    elif (
        performance_profile.preferred_broad_vs_technical == "technical"
        and category == "niche_hardcore"
    ):
        adjustment += 0.15
    return round(_clamp(adjustment, -0.5, 0.6), 2)


def compute_viral_score(
    candidate: dict,
    selection_profile: str | None = None,
    performance_profile: ContentPerformanceProfile | None = None,
) -> float:
    """Calcula um score não linear: positivos ponderados menos penalidades explícitas."""

    profile = _profile_name(selection_profile)
    weights = _PROFILE_WEIGHTS[profile]
    weight_total = sum(weights.values()) or 1.0
    positive = sum(_as_score(candidate.get(field), 5.0) * weights[field] for field in weights)
    positive = positive / weight_total

    penalty_points = 0.0
    for field, max_points in _PENALTY_MAX_POINTS.items():
        penalty_points += (_as_score(candidate.get(field), 0.0) / 10.0) * max_points
    result = positive - penalty_points + _performance_adjustment(candidate, performance_profile)
    return round(_clamp(result, 0.0, 10.0), 2)


def _ends_like_sentence(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and t[-1] in (".", "!", "?", "…")


def _refine_clip_window(
    *,
    start: float,
    segments: list[dict],
    target_len: float,
    end: float | None = None,
) -> tuple[float, float]:
    """Ajusta alguns segundos para começar/terminar em limites naturais de fala."""

    if not segments:
        requested_end = float(end) if end is not None else float(start) + target_len
        return float(start), max(float(start) + _MIN_CLIP_SEC, requested_end)

    video_end = max(float(segment["end"]) for segment in segments)
    start = _clamp(float(start), 0.0, max(0.0, video_end - 0.5))
    requested_end = _safe_float(end)
    if requested_end is None or requested_end <= start + 0.5:
        requested_end = start + float(target_len)
    target_end = _clamp(requested_end, start + _MIN_CLIP_SEC, video_end)

    desired_start = max(0.0, start - _START_PREROLL_SEC)
    best_start = start
    prev_end: float | None = None
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

    search_lo = _clamp(target_end - _BOUNDARY_SEARCH_SEC, start + 1.0, video_end)
    search_hi = _clamp(target_end + _BOUNDARY_SEARCH_SEC, start + 1.0, video_end)
    best_end = target_end
    best_boundary_score = float("inf")
    for seg in segments:
        s0 = float(seg["start"])
        s1 = float(seg["end"])
        if s1 < search_lo:
            continue
        if s0 > search_hi:
            break
        if not _ends_like_sentence(str(seg.get("text", ""))):
            continue
        distance = abs(s1 - target_end)
        early_penalty = 2.0 if s1 < target_end else 0.0
        boundary_score = distance + early_penalty
        if boundary_score < best_boundary_score:
            best_boundary_score = boundary_score
            best_end = s1

    return round(start, 3), round(_clamp(best_end, start + _MIN_CLIP_SEC, video_end), 3)


def _normalize_candidate(
    raw: dict,
    *,
    segments: list[dict],
    total_duration: float,
    target_len: float,
    index: int,
    selection_profile: str,
    performance_profile: ContentPerformanceProfile | None,
) -> dict | None:
    start = _safe_float(raw.get("start"))
    if start is None:
        return None
    requested_end = _safe_float(raw.get("end"), start + target_len)
    start, end = _refine_clip_window(
        start=start,
        end=requested_end,
        segments=segments,
        target_len=target_len,
    )
    if start >= total_duration or end <= start:
        return None
    start = round(_clamp(start, 0.0, max(0.0, total_duration - _MIN_CLIP_SEC)), 3)
    end = round(_clamp(end, start + _MIN_CLIP_SEC, total_duration), 3)

    category, invalid_category = _normalize_category(raw.get("category"))
    clip_text = _candidate_transcript_text(segments, start, end)
    raw_entities = raw.get("entities", [])
    if not isinstance(raw_entities, list):
        raw_entities = [raw_entities] if raw_entities else []
    entities = [str(value).strip() for value in raw_entities if str(value).strip()]
    if not entities:
        entities = _known_entities(clip_text)
    topic = str(raw.get("topic") or raw.get("topic_key") or "").strip()
    if not topic:
        topic = " ".join(clip_text.split()[:6])

    candidate: dict[str, Any] = {
        "start": start,
        "end": end,
        "reason": re.sub(r"\s+", " ", str(raw.get("reason") or "").strip())[:500],
        "hook": _normalize_hook(str(raw.get("hook") or ""), max_words=_HOOK_MAX_WORDS),
        "category": category,
        "topic": topic[:120],
        "entities": entities[:8],
        "category_was_invalid": invalid_category,
    }
    for field in _POSITIVE_SCORE_FIELDS:
        candidate[field] = _as_score(raw.get(field), 5.0)
    for field in _PENALTY_FIELDS:
        alias = "context_penalty" if field == "needs_previous_context" else field
        candidate[field] = _as_score(raw.get(field, raw.get(alias)), 0.0)

    local_penalties = _opening_penalties(clip_text[:500])
    for field, value in local_penalties.items():
        candidate[field] = max(candidate[field], value)
    if clip_text and not _ends_like_sentence(clip_text):
        candidate["weak_ending"] = max(candidate["weak_ending"], 6.0)
        candidate["incomplete_thought"] = max(candidate["incomplete_thought"], 5.0)
    opening_tokens = _token_set(" ".join(clip_text.split()[:12]))
    hook_tokens = _token_set(candidate["hook"])
    if hook_tokens and opening_tokens and len(hook_tokens & opening_tokens) / len(hook_tokens) >= 0.75:
        alternatives = (
            _normalize_hook(topic, max_words=_HOOK_MAX_WORDS),
            _normalize_hook(candidate["reason"], max_words=_HOOK_MAX_WORDS),
        )
        candidate["hook"] = next(
            (alternative for alternative in alternatives if _token_set(alternative) - opening_tokens),
            "",
        )
    if not candidate["hook"]:
        candidate["hook_strength"] = min(candidate["hook_strength"], 2.0)
        candidate["hook"] = _normalize_hook(candidate["reason"], max_words=_HOOK_MAX_WORDS)
    if "famous_person_or_topic" not in raw:
        candidate["famous_person_or_topic"] = max(
            candidate["famous_person_or_topic"], _recognizable_topic_score(clip_text)
        )

    candidate["context_penalty"] = candidate["needs_previous_context"]
    candidate["slow_start_penalty"] = candidate["slow_start"]
    candidate["weak_ending_penalty"] = candidate["weak_ending"]
    candidate["performance_adjustment"] = _performance_adjustment(candidate, performance_profile)
    candidate["viral_score"] = compute_viral_score(
        candidate,
        selection_profile=selection_profile,
        performance_profile=performance_profile,
    )
    cta = _normalize_cta(raw.get("cta"))
    if "cta" not in raw:
        cta = contextual_cta_for_candidate(candidate)
    candidate["cta"] = cta
    candidate["source_candidate_index"] = index
    return candidate


def contextual_cta_for_candidate(candidate: dict, language: str = "pt") -> str:
    """CTA curto e contextual; string vazia significa que não vale interromper o corte."""

    if language.casefold() == "en":
        if candidate.get("category") == "controversy_opinion" and _as_score(
            candidate.get("comment_potential"), 0
        ) >= 7:
            return "Do you agree?"
        if candidate.get("category") == "practical_value" and _as_score(
            candidate.get("practical_value"), 0
        ) >= 7:
            return "Save this to try later"
        if _as_score(candidate.get("curiosity"), 0) >= 8:
            return "Did you know this?"
        return ""
    if candidate.get("category") == "controversy_opinion" and _as_score(
        candidate.get("comment_potential"), 0
    ) >= 7:
        return "Você concorda?"
    if candidate.get("category") == "practical_value" and _as_score(
        candidate.get("practical_value"), 0
    ) >= 7:
        return "Salva pra testar depois"
    if _as_score(candidate.get("curiosity"), 0) >= 8:
        return "Você já sabia?"
    return ""


def _parse_moments(content: str) -> list:
    raw = _extract_json_array(content)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = json.loads(_sanitize_json(raw))
    if isinstance(value, dict):
        value = value.get("candidates") or value.get("moments") or []
    if not isinstance(value, list):
        raise ValueError("Resposta do modelo não é uma lista JSON")
    return value


def _extract_json_array(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON array found in model response")
    return text[start : end + 1]


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


def _overlap(a: dict, b: dict) -> bool:
    intersection = min(float(a["end"]), float(b["end"])) - max(
        float(a["start"]), float(b["start"])
    )
    return intersection > 0.25


def _candidate_similarity(a: dict, b: dict) -> float:
    a_topic = _token_set(a.get("topic"))
    b_topic = _token_set(b.get("topic"))
    if a_topic and b_topic:
        topic_union = a_topic | b_topic
        topic_score = len(a_topic & b_topic) / len(topic_union)
        if topic_score >= 0.75:
            return topic_score
    a_entities = a.get("entities", [])
    b_entities = b.get("entities", [])
    if not isinstance(a_entities, list):
        a_entities = [a_entities]
    if not isinstance(b_entities, list):
        b_entities = [b_entities]
    a_text = _token_set(
        f"{a.get('reason', '')} {a.get('hook', '')} {a.get('topic', '')} "
        f"{' '.join(str(value) for value in a_entities)}"
    )
    b_text = _token_set(
        f"{b.get('reason', '')} {b.get('hook', '')} {b.get('topic', '')} "
        f"{' '.join(str(value) for value in b_entities)}"
    )
    union = a_text | b_text
    return len(a_text & b_text) / len(union) if union else 0.0


def rank_viral_candidates(
    candidates: list[dict],
    *,
    count: int = VIRAL_CLIPS_COUNT,
    selection_profile: str | None = None,
    performance_profile: ContentPerformanceProfile | None = None,
) -> tuple[list[dict], list[dict]]:
    """Ordena, remove colisões/conceitos repetidos e preenche papéis diversos."""

    profile = _profile_name(selection_profile)
    usable: list[dict] = []
    invalid: list[dict] = []
    for candidate in candidates:
        item = dict(candidate)
        item["category"] = _normalize_category(item.get("category"))[0]
        item["viral_score"] = compute_viral_score(
            item, selection_profile=profile, performance_profile=performance_profile
        )
        item["selected"] = False
        item["discard_reason"] = ""
        item["selection_position"] = None
        start = _safe_float(item.get("start"))
        end = _safe_float(item.get("end"))
        if start is None or end is None or end <= start:
            item["ranking_position"] = None
            item["discard_reason"] = "invalid_timestamps"
            invalid.append(item)
            continue
        usable.append(item)
    usable.sort(key=lambda item: float(item.get("viral_score", 0.0)), reverse=True)

    accepted: list[dict] = []
    for position, candidate in enumerate(usable, start=1):
        candidate["ranking_position"] = position
        conflict = next((item for item in accepted if _overlap(candidate, item)), None)
        if conflict is not None:
            candidate["discard_reason"] = "overlap_with_higher_score"
            continue
        similar = next(
            (
                item
                for item in accepted
                if not (candidate.get("fallback_used") and item.get("fallback_used"))
                and _candidate_similarity(candidate, item) >= 0.60
            ),
            None,
        )
        if similar is not None:
            candidate["discard_reason"] = "semantic_duplicate_of_higher_score"
            continue
        accepted.append(candidate)

    selected: list[dict] = []
    selected_ids: set[int] = set()
    for category in _DIVERSITY_ORDER:
        if len(selected) >= count:
            break
        candidate = next((item for item in accepted if item.get("category") == category), None)
        if candidate is not None:
            selected.append(candidate)
            selected_ids.add(id(candidate))

    for candidate in accepted:
        if len(selected) >= count:
            break
        if id(candidate) not in selected_ids:
            selected.append(candidate)
            selected_ids.add(id(candidate))

    for selection_position, candidate in enumerate(selected, start=1):
        candidate["selection_position"] = selection_position

    for candidate in usable:
        if id(candidate) in selected_ids:
            candidate["selected"] = True
        elif not candidate["discard_reason"]:
            candidate["discard_reason"] = "diversity_or_top_count"
    return selected[:count], usable + invalid


def _fallback_moments(
    segments: list[dict],
    *,
    total_duration: float,
    target_len: float,
    count: int,
    selection_profile: str,
    performance_profile: ContentPerformanceProfile | None,
) -> list[dict]:
    if not segments:
        return []
    starts: list[float] = []
    step = max(1.0, (total_duration - min(target_len, total_duration)) / max(1, count - 1))
    for index in range(count):
        starts.append(min(max(0.0, total_duration - _MIN_CLIP_SEC), index * step))
    raw = []
    for index, start in enumerate(starts):
        text = _candidate_transcript_text(segments, start, min(total_duration, start + target_len))
        raw.append(
            {
                "start": start,
                "end": min(total_duration, start + target_len),
                "reason": "Fallback temporal por resposta incompleta da IA.",
                "hook": _normalize_hook(text, max_words=_HOOK_MAX_WORDS),
                "category": "broad_appeal" if index == 0 else "practical_value",
                "topic": " ".join(text.split()[:6]),
            }
        )
    normalized = []
    for index, item in enumerate(raw):
        candidate = _normalize_candidate(
            item,
            segments=segments,
            total_duration=total_duration,
            target_len=target_len,
            index=index,
            selection_profile=selection_profile,
            performance_profile=performance_profile,
        )
        if candidate is not None:
            candidate["fallback_used"] = True
            normalized.append(candidate)
    return normalized


def _build_prompt(
    *,
    transcript_text: str,
    total_duration: float,
    candidate_count: int,
    output_language: str,
    selection_profile: str,
    performance_profile: ContentPerformanceProfile | None,
    clip_target: float,
) -> str:
    if output_language == "pt":
        language_rules = (
            "- `reason`, `hook`, `topic`, `cta` e categorias devem estar em português brasileiro.\n"
            "- `hook`: 3 a 10 palavras, complementar à fala e nunca uma cópia da primeira frase.\n"
        )
    else:
        language_rules = (
            "- `reason`, `hook`, `topic`, `cta` e categorias devem estar em inglês.\n"
            "- `hook`: 3 a 10 words, complementary to the spoken opening, never a copy of it.\n"
        )
    profile_summary = (
        performance_profile.prompt_summary()
        if performance_profile is not None
        else "Nenhum relatório histórico foi fornecido; use apenas a transcrição."
    )
    return (
        "Você é um editor de cortes para TikTok de guitarra, música, rock, blues, teoria musical "
        "e entrevistas com músicos. Encontre trechos que funcionem sem o resto do vídeo. "
        f"Gere {candidate_count} CANDIDATOS distintos, não apenas os {VIRAL_CLIPS_COUNT} finais; "
        "o ranking e a diversidade serão aplicados localmente.\n\n"
        "A fala original é a fonte da verdade: não invente, reescreva ou complete o conteúdo. "
        "Um hook escrito na tela não salva um começo falado fraco. Penalize muito aberturas como "
        "'como eu estava falando', 'voltando ao assunto', 'então', 'como eu disse antes', "
        "'nesse caso', 'aí depois', 'isso aqui' ou referências a algo distante. Prefira uma "
        "afirmação, pergunta, conflito, curiosidade ou ideia forte já no áudio inicial. Formas "
        "desejáveis incluem 'O maior erro de quem...', 'A maioria não percebe...' e 'O problema "
        "não é...'; são exemplos de estrutura, não texto para inventar.\n\n"
        "Cada candidato deve ser uma janela contínua de aproximadamente "
        f"{clip_target:.0f} segundos, dentro de 0 a {total_duration:.1f}s. Pode começar até poucos "
        "segundos antes ou terminar alguns segundos depois para fechar o pensamento. "
        "Avalie o áudio e o final do trecho, não só o assunto.\n\n"
        "Categorias estratégicas permitidas: broad_appeal, controversy_opinion, curiosity, "
        "practical_value, niche_hardcore. Tente cobrir categorias diferentes quando houver material.\n"
        "- broad_appeal: público geral, história, surpresa ou fenômeno compreensível sem tocar guitarra.\n"
        "- controversy_opinion: opinião/mito/erro/comparação que gere discordância legítima; não fabrique polêmica.\n"
        "- curiosity: algo que faça pensar 'eu não sabia disso', incluindo artistas, bastidores ou ciência.\n"
        "- practical_value: uma aplicação útil rapidamente, mas com conflito, exemplo ou payoff; evite aula genérica.\n"
        "- niche_hardcore: técnica para músicos, desde que tenha hook e conclusão fortes.\n\n"
        "Pontue cada campo de 0 a 10. Positivos: hook_strength, standalone_clarity, curiosity, "
        "controversy, emotional_strength, practical_value, shareability, comment_potential, "
        "general_audience, niche_relevance, famous_person_or_topic, story_progression, "
        "ending_payoff, retention_likelihood. Penalidades de 0 a 10: needs_previous_context, "
        "slow_start, too_technical, repetitive, weak_ending, filler, generic_advice, "
        "incomplete_thought.\n"
        "Standalone_clarity cai se faltar a pergunta, houver pronomes sem referência, gráfico ausente, "
        "explicação interrompida ou história de minutos antes. Penalize definição de escala, sequência "
        "de notas e instrução básica sem conflito/aplicação. Um nome famoso ajuda pouco e nunca vence "
        "sozinho um trecho excelente.\n\n"
        f"Perfil de seleção local: {selection_profile}. Hook, clareza sem contexto, curiosidade, "
        "retenção provável e payoff têm prioridade.\n"
        f"{profile_summary}\n\n"
        f"{language_rules}"
        "- `entities`: nomes de pessoas, bandas, músicas, técnicas ou assuntos reconhecíveis que aparecem literalmente.\n"
        "- `topic`: etiqueta curta para detectar o mesmo conceito em outros candidatos.\n"
        "- `cta`: opcional, 2 a 6 palavras, somente se fizer sentido (ex.: Você concorda?, Salva pra testar depois).\n"
        "- Retorne SOMENTE um array JSON válido, sem markdown.\n\n"
        "Formato obrigatório:\n"
        '[{"start":123.4,"end":173.1,"category":"broad_appeal",'
        '"topic":"erro de improvisação","entities":["nome literal"],'
        '"hook":"O erro que trava seu improviso","cta":"Você já fazia isso?",'
        '"reason":"motivo curto",'
        '"hook_strength":9,"standalone_clarity":9,"curiosity":8,"controversy":4,'
        '"emotional_strength":6,"practical_value":8,"shareability":8,"comment_potential":7,'
        '"general_audience":9,"niche_relevance":8,"famous_person_or_topic":2,'
        '"story_progression":8,"ending_payoff":9,"retention_likelihood":9,'
        '"needs_previous_context":0,"slow_start":0,"too_technical":2,"repetitive":0,'
        '"weak_ending":0,"filler":0,"generic_advice":0,"incomplete_thought":0}]\n\n'
        f"TRANSCRIÇÃO (duração total: {total_duration:.1f}s):\n{transcript_text}"
    )


def analyze_viral_moments(
    segments: list[dict],
    output_language: str = "pt",
    *,
    selection_profile: str | None = None,
    performance_profile: ContentPerformanceProfile | None = None,
    return_metadata: bool = False,
    target_clip_duration: float | None = None,
) -> list[dict] | ViralAnalysisResult:
    """Analisa candidatos em uma chamada e devolve os cinco finais por padrão."""

    if not segments:
        raise ValueError("Empty transcript — cannot analyze viral moments.")
    profile = _profile_name(selection_profile)
    lang = (output_language or "pt").strip().casefold()
    total_duration = max(float(segment["end"]) for segment in segments)
    transcript_text = _build_transcript_text(segments)
    candidate_count = max(VIRAL_CLIPS_COUNT, min(20, VIRAL_CANDIDATE_COUNT))
    clip_target = (
        float(target_clip_duration)
        if target_clip_duration is not None and target_clip_duration > 5
        else float(CLIP_DURATION)
    )

    prompt = _build_prompt(
        transcript_text=transcript_text,
        total_duration=total_duration,
        candidate_count=candidate_count,
        output_language=lang,
        selection_profile=profile,
        performance_profile=performance_profile,
        clip_target=clip_target,
    )
    _log.info(
        "Chamando Groq para descoberta/ranking viral (%s segmentos, ~%.0fs, %s candidatos, perfil=%s).",
        len(segments),
        total_duration,
        candidate_count,
        profile,
    )
    raw_moments: list[Any] = []
    used_fallback = False
    last_error: Exception | None = None
    analysis_max_tokens = min(
        _MAX_ANALYSIS_COMPLETION_TOKENS,
        max(2_400, candidate_count * 230),
    )
    # JSON malformado do modelo é instável por tentativa; repetir antes do fallback temporal.
    for attempt in range(3):
        content = groq_user_message_text(
            prompt,
            temperature=0.3,
            max_tokens=analysis_max_tokens,
            none_as_empty=False,
            retry_label="Groq",
            bad_request_runtime=lambda e: RuntimeError(f"Prompt muito longo para o modelo: {e}"),
            rate_limit_message="Groq rate limit excedido. Aguarde alguns minutos e tente novamente.",
        )
        try:
            if content is None or not str(content).strip():
                raise ValueError("Resposta vazia do modelo")
            raw_moments = _parse_moments(str(content))
            last_error = None
            break
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            _log.warning("JSON viral inválido na tentativa %s/3 (%s).", attempt + 1, exc)
    if last_error is not None:
        # Campos novos inválidos não podem impedir a produção; a janela temporal ainda é útil.
        _log.warning("JSON viral inválido após 3 tentativas (%s); usando fallback temporal seguro.", last_error)
        raw_moments = []
        used_fallback = True

    refine_segments = _coarse_segments_for_refine(segments)
    normalized: list[dict] = []
    for index, item in enumerate(raw_moments[: max(candidate_count, VIRAL_CLIPS_COUNT)]):
        raise_if_cancelled()
        if not isinstance(item, dict):
            continue
        candidate = _normalize_candidate(
            item,
            segments=refine_segments,
            total_duration=total_duration,
            target_len=clip_target,
            index=index,
            selection_profile=profile,
            performance_profile=performance_profile,
        )
        if candidate is not None:
            normalized.append(candidate)

    selected, candidates = rank_viral_candidates(
        normalized,
        count=VIRAL_CLIPS_COUNT,
        selection_profile=profile,
        performance_profile=performance_profile,
    )
    if len(selected) < VIRAL_CLIPS_COUNT:
        missing = VIRAL_CLIPS_COUNT - len(selected)
        _log.warning(
            "A IA retornou somente %s seleção(ões) utilizável(is); "
            "completando %s com fallback temporal.",
            len(selected),
            missing,
        )
        temporal_fallback = _fallback_moments(
            refine_segments,
            total_duration=total_duration,
            target_len=clip_target,
            count=max(candidate_count, VIRAL_CLIPS_COUNT),
            selection_profile=profile,
            performance_profile=performance_profile,
        )
        if temporal_fallback:
            used_fallback = True
            normalized.extend(temporal_fallback)
            selected, candidates = rank_viral_candidates(
                normalized,
                count=VIRAL_CLIPS_COUNT,
                selection_profile=profile,
                performance_profile=performance_profile,
            )
    result = ViralAnalysisResult(
        selected=selected,
        candidates=candidates,
        selection_profile=profile,
        performance_profile_key=(performance_profile.cache_key if performance_profile else None),
        fallback_used=used_fallback,
    )
    _log.info(
        "Análise viral concluída: %s selecionado(s) de %s candidato(s); perfil=%s.",
        len(selected),
        len(candidates),
        profile,
    )
    return result if return_metadata else selected
