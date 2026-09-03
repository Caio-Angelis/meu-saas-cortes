"""Perfil compacto e determinístico de desempenho para orientar novos cortes.

O relatório exportado pelo TikTok pode ser grande e conter campos que não estão
disponíveis para esta conta. Este módulo reduz o arquivo a sinais editoriais
secundários; ele não tenta estimar retenção, causalidade de horário ou
seguidores ganhos por vídeo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_GENERIC_TERMS = frozenset(
    {
        "fyp",
        "fy",
        "foryou",
        "foryoupage",
        "viral",
        "trend",
        "trending",
        "tiktok",
        "video",
        "videos",
        "corte",
        "cortes",
        "cortesvirais",
        "guitartok",
        "review",
        "original",
        "oficial",
        "aprenda",
        "aprende",
        "mostrando",
        "professor",
        "criador",
        "músico",
        "musico",
        "musica",
        "musical",
        "sobre",
        "explica",
        "fala",
        "falar",
    }
)

_STOPWORDS = frozenset(
    {
        "a",
        "ao",
        "aos",
        "as",
        "da",
        "das",
        "de",
        "do",
        "dos",
        "e",
        "em",
        "esse",
        "essa",
        "isso",
        "ela",
        "ele",
        "for",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "os",
        "para",
        "por",
        "que",
        "se",
        "sem",
        "um",
        "uma",
        "com",
        "como",
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
    "Sweet Child O' Mine",
)

_BROAD_TERMS = frozenset(
    {
        "historia",
        "historico",
        "bastidor",
        "bastidores",
        "opinião",
        "opiniao",
        "erro",
        "mito",
        "segredo",
        "surpresa",
        "curioso",
        "curiosidade",
        "porquê",
        "porque",
        "por que",
        "comparação",
        "comparacao",
        "famoso",
        "famosa",
        "começou",
        "comecou",
    }
)

_TECHNICAL_TERMS = frozenset(
    {
        "escala",
        "escalas",
        "acorde",
        "acordes",
        "pentatônica",
        "pentatonica",
        "intervalo",
        "intervalos",
        "mixolídio",
        "mixolidio",
        "dórico",
        "dorico",
        "cromatismo",
        "shape",
        "semitom",
        "semitons",
        "sweep",
        "ligato",
        "arpejo",
        "teoria",
        "harmonia",
        "fraseado",
    }
)


@dataclass(frozen=True)
class ContentPerformanceProfile:
    """Sinais editoriais pequenos o bastante para entrar em um prompt."""

    strong_topics: tuple[str, ...] = ()
    weak_topics: tuple[str, ...] = ()
    strong_entities: tuple[str, ...] = ()
    weak_entities: tuple[str, ...] = ()
    strong_content_patterns: tuple[str, ...] = ()
    weak_content_patterns: tuple[str, ...] = ()
    preferred_broad_vs_technical: str = "unknown"
    preferred_duration_buckets: tuple[str, ...] = ()
    sample_count: int = 0
    observed_metrics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def cache_key(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def prompt_summary(self, *, max_chars: int = 1800) -> str:
        """Retorna somente o resumo; nunca o JSON de desempenho inteiro."""

        if self.sample_count <= 0:
            return "Nenhum sinal editorial utilizável foi encontrado no relatório histórico."

        def joined(values: tuple[str, ...]) -> str:
            return ", ".join(values) if values else "nenhum sinal claro"

        lines = [
            "SINAL HISTÓRICO SECUNDÁRIO (correlação, não causalidade):",
            f"- amostra observada: {self.sample_count} vídeo(s); métricas disponíveis: "
            f"{joined(self.observed_metrics)}.",
            f"- assuntos recorrentes nos melhores: {joined(self.strong_topics)}.",
            f"- assuntos recorrentes nos mais fracos: {joined(self.weak_topics)}.",
            f"- artistas/entidades recorrentes nos melhores: {joined(self.strong_entities)}.",
            f"- padrões fortes: {joined(self.strong_content_patterns)}.",
            f"- padrões fracos: {joined(self.weak_content_patterns)}.",
            f"- equilíbrio observado entre amplo e técnico: {self.preferred_broad_vs_technical}.",
            f"- durações/buckets recorrentes nos melhores: {joined(self.preferred_duration_buckets)}.",
            "- não há watch time, retenção, completion rate, tráfego ou seguidores ganhos por vídeo; "
            "não invente essas métricas.",
            "- horário de postagem não deve ser tratado como causa nem como regra de seleção.",
        ]
        text = "\n".join(lines)
        return text[:max_chars].rstrip()


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _number(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().casefold().replace("%", "").replace(" ", "")
    if not text or text in {"-", "n/a", "na", "null", "none"}:
        return 0.0
    text = re.sub(r"[^0-9,.-]", "", text)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", text):
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def _percentile(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return 1.0
    ordered = sorted(values)
    equal_positions = [index for index, item in enumerate(ordered) if item == value]
    if equal_positions:
        return (sum(equal_positions) / len(equal_positions)) / (len(ordered) - 1)
    lower = max(index for index, item in enumerate(ordered) if item < value)
    upper = min(index for index, item in enumerate(ordered) if item > value)
    fraction = (value - ordered[lower]) / (ordered[upper] - ordered[lower])
    return (lower + fraction) / (len(ordered) - 1)


def _text_of(row: dict[str, Any]) -> str:
    values = [
        row.get("description"),
        row.get("caption"),
        row.get("title"),
        row.get("short_description"),
        row.get("content"),
    ]
    text = next((str(value).strip() for value in values if str(value or "").strip()), "")
    text = re.split(r"\breview\s+original\s*:", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    hashtags = row.get("hashtags")
    if isinstance(hashtags, list):
        text = f"{text} {' '.join(str(tag) for tag in hashtags)}".strip()
    return re.sub(r"https?://\S+", "", text).strip()


def _nested_metric(row: dict[str, Any], group: str, key: str) -> float:
    nested = row.get(group)
    if isinstance(nested, dict):
        value = nested.get(key)
        if value is not None:
            return _number(value)
    return _number(row.get(key))


def _metric_present(row: dict[str, Any], group: str, key: str) -> bool:
    nested = row.get(group)
    return (isinstance(nested, dict) and key in nested) or key in row


def _topics(text: str) -> list[str]:
    raw_tags = re.findall(r"#([\wÀ-ÿ][\wÀ-ÿ-]{2,})", text, flags=re.UNICODE)
    words = re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'-]{3,}", text.casefold())
    values = raw_tags + words
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _normalize(value)
        if not key or key in _STOPWORDS or key in _GENERIC_TERMS or key in seen:
            continue
        seen.add(key)
        out.append(value.strip("#- "))
    return out


def _entities(text: str) -> list[str]:
    out: list[str] = []
    normalized_text = _normalize(text)
    for entity in _KNOWN_ENTITIES:
        if _normalize(entity) in normalized_text:
            out.append(entity)

    # Captura nomes/bandas que não estão na lista fixa quando aparecem como uma
    # sequência de pelo menos duas palavras capitalizadas no relatório.
    pattern = r"\b[A-ZÀ-ÖØ-Ý][\wÀ-ÿ'-]*(?:\s+[A-ZÀ-ÖØ-Ý][\wÀ-ÿ'-]*)+\b"
    for match in re.findall(pattern, text):
        key = _normalize(match)
        match_words = set(key.split())
        ignored = _GENERIC_TERMS | {"sem", "fim", "ciencia", "ciência"}
        if (
            key
            and not match_words & ignored
            and key not in {_normalize(item) for item in out}
            and len(match) <= 48
        ):
            out.append(match.strip())
    return out[:8]


def _patterns(text: str) -> list[str]:
    normalized = _normalize(text)
    patterns: list[str] = []
    broad_hits = sum(term in normalized for term in _BROAD_TERMS if " " not in term)
    technical_hits = sum(term in normalized for term in _TECHNICAL_TERMS)
    if broad_hits or any(marker in normalized for marker in ("por que", "como comecou", "bastidor")):
        patterns.append("história, bastidor ou curiosidade")
    if any(marker in normalized for marker in ("erro", "mito", "errado", "discordo", "opini")):
        patterns.append("opinião forte ou erro comum")
    if any(marker in normalized for marker in ("como", "aprenda", "aplicar", "improvis", "testar")):
        patterns.append("aplicação prática")
    if technical_hits >= 1:
        patterns.append("explicação técnica")
    if _entities(text):
        patterns.append("artista, banda ou assunto reconhecível")
    return patterns


def _duration_bucket(row: dict[str, Any]) -> str:
    value = str(row.get("duration_bucket") or "").strip()
    if value:
        return value
    duration = _number(row.get("duration"))
    if duration <= 0:
        return ""
    if duration <= 30:
        return "0-30s"
    if duration <= 45:
        return "31-45s"
    if duration <= 60:
        return "46-60s"
    if duration <= 90:
        return "61-90s"
    return "91s+"


def _rows_from_report(report: object) -> list[dict[str, Any]]:
    if isinstance(report, list):
        return [row for row in report if isinstance(row, dict)]
    if not isinstance(report, dict):
        return []
    for key in ("videos", "rows", "items", "data"):
        value = report.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _top_terms(rows: list[dict[str, Any]], key: str, *, limit: int = 6) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for row in rows:
        values = row.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            clean = str(value).strip("# ")
            normalized = _normalize(clean)
            if not normalized:
                continue
            counts[normalized] += 1
            display.setdefault(normalized, clean)
    return tuple(display[key] for key, _count in counts.most_common(limit))


def build_content_performance_profile(report: object) -> ContentPerformanceProfile:
    """Converte um relatório JSON em um perfil pequeno sem métricas inventadas."""

    raw_rows = _rows_from_report(report)
    prepared: list[dict[str, Any]] = []
    observed: set[str] = set()
    for row in raw_rows:
        text = _text_of(row)
        views = _nested_metric(row, "current_metrics", "views")
        if views <= 0:
            views = _number(row.get("views"))
        likes = _nested_metric(row, "current_metrics", "likes")
        comments = _nested_metric(row, "current_metrics", "comments")
        shares = _nested_metric(row, "current_metrics", "shares")
        engagement = _nested_metric(row, "current_metrics", "engagement_rate")
        if engagement <= 0:
            engagement = _number(row.get("engagement"))
        if engagement <= 0 and views > 0 and any((likes, comments, shares)):
            # Percentual relativo: a escala exata não importa para os percentis, mas a
            # fórmula preserva o peso editorial de comentários e compartilhamentos.
            engagement = (likes + comments * 2 + shares * 3) / views * 100.0
        if _metric_present(row, "current_metrics", "views") or views > 0:
            observed.add("views")
        if _metric_present(row, "current_metrics", "likes"):
            observed.add("likes")
        if _metric_present(row, "current_metrics", "comments"):
            observed.add("comments")
        if _metric_present(row, "current_metrics", "shares"):
            observed.add("shares")
        if _metric_present(row, "current_metrics", "engagement_rate") or "engagement" in row:
            observed.add("engagement")
        if not text or not any((views, likes, comments, shares, engagement)):
            continue
        share_rate = shares / views if views > 0 else 0.0
        prepared.append(
            {
                "text": text,
                "views": views,
                "engagement": engagement,
                "share_rate": share_rate,
                "topics": _topics(text),
                "entities": _entities(text),
                "patterns": _patterns(text),
                "duration_bucket": _duration_bucket(row),
            }
        )

    if not prepared:
        return ContentPerformanceProfile(observed_metrics=tuple(sorted(observed)))

    view_values = [float(row["views"]) for row in prepared]
    engagement_values = [float(row["engagement"]) for row in prepared]
    share_values = [float(row["share_rate"]) for row in prepared]
    for row in prepared:
        # Percentis reduzem a influência de um único outlier de views.
        view_signal = _percentile(float(row["views"]), view_values)
        engagement_signal = _percentile(float(row["engagement"]), engagement_values)
        share_signal = _percentile(float(row["share_rate"]), share_values)
        row["signal"] = view_signal * 0.60 + engagement_signal * 0.25 + share_signal * 0.15

    prepared.sort(key=lambda row: (float(row["signal"]), float(row["views"])), reverse=True)
    top_count = max(1, min(8, math.ceil(len(prepared) * 0.25)))
    top = prepared[:top_count]
    weak = prepared[-top_count:]

    top_pattern_counts = Counter(pattern for row in top for pattern in row["patterns"])
    weak_pattern_counts = Counter(pattern for row in weak for pattern in row["patterns"])
    broad_hits = sum("história, bastidor ou curiosidade" in row["patterns"] for row in top)
    technical_hits = sum("explicação técnica" in row["patterns"] for row in top)
    if broad_hits > technical_hits + 1:
        preference = "broad"
    elif technical_hits > broad_hits + 1:
        preference = "technical"
    else:
        preference = "mixed"

    buckets = Counter(
        str(row["duration_bucket"]) for row in top if str(row["duration_bucket"]).strip()
    )
    preferred_buckets = tuple(bucket for bucket, _count in buckets.most_common(3))
    return ContentPerformanceProfile(
        strong_topics=_top_terms(top, "topics"),
        weak_topics=_top_terms(weak, "topics"),
        strong_entities=_top_terms(top, "entities"),
        weak_entities=_top_terms(weak, "entities"),
        strong_content_patterns=tuple(
            pattern for pattern, _count in top_pattern_counts.most_common(5)
        ),
        weak_content_patterns=tuple(
            pattern for pattern, _count in weak_pattern_counts.most_common(5)
        ),
        preferred_broad_vs_technical=preference,
        preferred_duration_buckets=preferred_buckets,
        sample_count=len(prepared),
        observed_metrics=tuple(sorted(observed)),
    )


@lru_cache(maxsize=4)
def _load_profile_cached(path: str, mtime_ns: int, size: int) -> ContentPerformanceProfile | None:
    del mtime_ns, size  # entram na chave para invalidar o cache quando o arquivo muda
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _log.warning("Relatório de desempenho não pôde ser carregado (%s): %s", path, exc)
        return None
    return build_content_performance_profile(report)


def load_content_performance_profile(path: str | Path | None = None) -> ContentPerformanceProfile | None:
    """Carrega opcionalmente o relatório configurado e devolve apenas o perfil."""

    if path is None:
        from app.core.config import TIKTOK_PERFORMANCE_REPORT_PATH

        path = TIKTOK_PERFORMANCE_REPORT_PATH
    if not path:
        return None
    report_path = Path(path).expanduser()
    try:
        stat = report_path.stat()
    except OSError as exc:
        _log.warning("Relatório de desempenho não encontrado (%s): %s", report_path, exc)
        return None
    return _load_profile_cached(str(report_path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
