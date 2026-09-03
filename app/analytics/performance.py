"""Leitura de CSV de redes sociais e recomendação dos próximos temas."""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.ai_integrations.groq_chat import groq_user_message_text
from app.core.config import GROQ_FAST_MODEL

_log = logging.getLogger(__name__)


class PerformanceAnalysisError(ValueError):
    """CSV ausente, ilegível ou sem as colunas mínimas para análise."""


@dataclass(frozen=True)
class RankedContent:
    label: str
    theme: str
    views: float
    likes: float
    comments: float
    shares: float
    saves: float
    followers: float
    engagement_rate: float
    retention_value: float | None
    score: float


@dataclass(frozen=True)
class ThemeRecommendation:
    theme: str
    why: str
    next_video: str
    evidence: str
    score: float


@dataclass(frozen=True)
class PerformanceAnalysis:
    source_path: Path
    row_count: int
    valid_row_count: int
    mapped_columns: dict[str, str]
    recommendations: tuple[ThemeRecommendation, ...]
    used_ai: bool
    summary: str


_ALIASES: dict[str, tuple[str, ...]] = {
    "theme": (
        "tema",
        "theme",
        "topico",
        "topic",
        "assunto",
        "nicho",
        "niche",
        "categoria",
        "category",
        "content pillar",
        "pilar de conteudo",
    ),
    "content": (
        "titulo",
        "title",
        "video title",
        "titulo do video",
        "nome do video",
        "caption",
        "legenda",
        "description",
        "descricao",
        "post text",
        "texto do post",
        "video post",
        "conteudo",
        "content",
        "video",
    ),
    "views": (
        "video views",
        "views",
        "visualizacoes de video",
        "visualizacoes",
        "reproducoes",
        "plays",
        "exibicoes",
    ),
    "reach": ("reach", "alcance", "unique viewers", "espectadores unicos"),
    "impressions": ("impressions", "impressoes"),
    "likes": ("likes", "curtidas", "gostei"),
    "comments": ("comments", "comentarios", "comentario"),
    "shares": ("shares", "compartilhamentos", "compartilhado"),
    "saves": ("saves", "saved", "salvamentos", "favoritos", "favorites"),
    "followers": (
        "new followers",
        "followers gained",
        "novos seguidores",
        "seguidores conquistados",
        "seguidores ganhos",
        "subscribers",
        "subscribers gained",
        "new subscribers",
        "inscritos",
        "novos inscritos",
        "follows",
    ),
    "completion": (
        "watched full video",
        "watched full video rate",
        "completion rate",
        "full video watched rate",
        "assistiu ao video completo",
        "video completo",
        "taxa de conclusao",
        "retencao",
        "retention rate",
        "average percentage viewed",
        "average percentage viewed percent",
        "porcentagem media visualizada",
    ),
    "avg_watch": (
        "average watch time",
        "avg watch time",
        "average view duration",
        "average time watched",
        "tempo medio de visualizacao",
        "duracao media da visualizacao",
        "tempo medio assistido",
    ),
    "duration": ("video duration", "duracao do video", "duration", "duracao"),
    "date": ("date", "data", "post date", "data da postagem", "publish date"),
}


def _normalize_header(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _find_column(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {header: _normalize_header(header) for header in headers}
    wanted = tuple(_normalize_header(alias) for alias in aliases)
    for alias in wanted:
        for header, norm in normalized.items():
            if norm == alias:
                return header
    for alias in wanted:
        if len(alias) < 5:
            continue
        for header, norm in normalized.items():
            if alias in norm or (len(norm) >= 5 and norm in alias):
                return header
    return None


def _parse_number(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().casefold().replace("\u00a0", "").replace(" ", "")
    if not text or text in {"-", "—", "n/a", "na", "null", "none"}:
        return 0.0
    multiplier = 1.0
    if text.endswith(("k", "mil")):
        multiplier = 1_000.0
        text = re.sub(r"(?:k|mil)$", "", text)
    elif text.endswith(("m", "mi", "milhao", "milhoes")):
        multiplier = 1_000_000.0
        text = re.sub(r"(?:m|mi|milhao|milhoes)$", "", text)
    text = text.rstrip("%")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text or text in {"-", ".", ","}:
        return 0.0
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    else:
        text = text.replace(",", ".")
    try:
        return max(0.0, float(text) * multiplier)
    except ValueError:
        return 0.0


def _parse_duration_seconds(value: object) -> float:
    text = str(value or "").strip().casefold()
    if not text:
        return 0.0
    if ":" in text:
        try:
            parts = [float(part.replace(",", ".")) for part in text.split(":")]
        except ValueError:
            parts = []
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
    minutes = re.search(r"([\d.,]+)\s*(?:m|min)", text)
    seconds = re.search(r"([\d.,]+)\s*(?:s|seg)", text)
    if minutes or seconds:
        return _parse_number(minutes.group(1) if minutes else 0) * 60 + _parse_number(
            seconds.group(1) if seconds else 0
        )
    return _parse_number(text)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise PerformanceAnalysisError(f"CSV não encontrado: {path}")
    if path.suffix.casefold() != ".csv":
        raise PerformanceAnalysisError("Selecione um arquivo com extensão .csv.")
    raw = path.read_bytes()
    if not raw:
        raise PerformanceAnalysisError("O arquivo CSV está vazio.")
    decoded: str | None = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise PerformanceAnalysisError("Não foi possível identificar a codificação do CSV.")
    try:
        dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if decoded.count(";") > decoded.count(",") else ","
    reader = csv.DictReader(decoded.splitlines(), delimiter=delimiter)
    headers = [str(value or "").strip() for value in (reader.fieldnames or [])]
    if not headers:
        raise PerformanceAnalysisError("O CSV não possui cabeçalho.")
    rows: list[dict[str, str]] = []
    for row in reader:
        clean = {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        if any(clean.values()):
            rows.append(clean)
    if not rows:
        raise PerformanceAnalysisError("O CSV não possui linhas de desempenho.")
    return headers, rows


def _detect_columns(headers: list[str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for key, aliases in _ALIASES.items():
        column = _find_column(headers, aliases)
        if column:
            mapped[key] = column
    if "theme" not in mapped and "content" not in mapped:
        raise PerformanceAnalysisError(
            "Não encontrei a coluna de tema, título ou legenda. "
            "Use um cabeçalho como Tema, Título, Video title, Caption ou Descrição."
        )
    if not any(key in mapped for key in ("views", "reach", "impressions")):
        raise PerformanceAnalysisError(
            "Não encontrei visualizações, alcance ou impressões. "
            "O CSV precisa de pelo menos uma métrica de exposição."
        )
    return mapped


def _rank_percentiles(values: list[float]) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [1.0]
    order = sorted(range(len(values)), key=lambda idx: values[idx])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + end - 1) / 2
        percentile = average_rank / (len(values) - 1)
        for pos in range(start, end):
            result[order[pos]] = percentile
        start = end
    return result


def _metric_value(row: dict[str, str], mapped: dict[str, str], key: str) -> float:
    column = mapped.get(key)
    return _parse_number(row.get(column, "")) if column else 0.0


def _rank_rows(rows: list[dict[str, str]], mapped: dict[str, str]) -> list[RankedContent]:
    content_col = mapped.get("content") or mapped["theme"]
    theme_col = mapped.get("theme")
    exposure_key = next(key for key in ("views", "reach", "impressions") if key in mapped)
    prepared: list[dict[str, object]] = []
    for row in rows:
        label = str(row.get(content_col, "")).strip()
        theme = str(row.get(theme_col, "")).strip() if theme_col else ""
        views = _metric_value(row, mapped, exposure_key)
        if not label or views <= 0:
            continue
        likes = _metric_value(row, mapped, "likes")
        comments = _metric_value(row, mapped, "comments")
        shares = _metric_value(row, mapped, "shares")
        saves = _metric_value(row, mapped, "saves")
        followers = _metric_value(row, mapped, "followers")
        engagement_rate = (likes + comments * 2 + shares * 3 + saves * 2.5) / views
        completion = _metric_value(row, mapped, "completion")
        if completion > 1:
            completion /= 100.0
        avg_watch = _parse_duration_seconds(row.get(mapped.get("avg_watch", ""), ""))
        duration = _parse_duration_seconds(row.get(mapped.get("duration", ""), ""))
        retention: float | None = None
        if completion > 0:
            retention = min(completion, 1.5)
        elif avg_watch > 0 and duration > 0:
            retention = min(avg_watch / duration, 1.5)
        elif avg_watch > 0:
            retention = avg_watch
        prepared.append(
            {
                "label": label,
                "theme": theme or label,
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "saves": saves,
                "followers": followers,
                "engagement_rate": engagement_rate,
                "retention": retention,
                "follower_rate": followers / views,
            }
        )
    if not prepared:
        raise PerformanceAnalysisError(
            "Nenhuma linha possui ao mesmo tempo conteúdo identificável e exposição maior que zero."
        )

    exposure_rank = _rank_percentiles([math.log1p(float(item["views"])) for item in prepared])
    engagement_available = any(
        key in mapped for key in ("likes", "comments", "shares", "saves")
    )
    engagement_rank = _rank_percentiles(
        [float(item["engagement_rate"]) for item in prepared]
    )
    retention_available = any(item["retention"] is not None for item in prepared)
    retention_rank = _rank_percentiles(
        [float(item["retention"] or 0.0) for item in prepared]
    )
    followers_available = "followers" in mapped
    followers_rank = _rank_percentiles([float(item["follower_rate"]) for item in prepared])

    weights: list[tuple[list[float], float]] = [(exposure_rank, 0.35)]
    if engagement_available:
        weights.append((engagement_rank, 0.35))
    if retention_available:
        weights.append((retention_rank, 0.20))
    if followers_available:
        weights.append((followers_rank, 0.10))
    total_weight = sum(weight for _values, weight in weights)

    ranked: list[RankedContent] = []
    for index, item in enumerate(prepared):
        score = 100 * sum(values[index] * weight for values, weight in weights) / total_weight
        ranked.append(
            RankedContent(
                label=str(item["label"]),
                theme=str(item["theme"]),
                views=float(item["views"]),
                likes=float(item["likes"]),
                comments=float(item["comments"]),
                shares=float(item["shares"]),
                saves=float(item["saves"]),
                followers=float(item["followers"]),
                engagement_rate=float(item["engagement_rate"]),
                retention_value=(
                    float(item["retention"]) if item["retention"] is not None else None
                ),
                score=round(score, 1),
            )
        )
    return sorted(ranked, key=lambda item: (item.score, item.views), reverse=True)


def _format_count(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", ".")


def _short_label(value: str, *, max_words: int = 9) -> str:
    clean = re.sub(r"https?://\S+", "", value).strip(" -–—|:;,.\n\t")
    words = clean.split()
    return " ".join(words[:max_words]) or "Tema com melhor desempenho"


def _evidence(item: RankedContent) -> str:
    parts = [f"{_format_count(item.views)} visualizações"]
    if item.engagement_rate > 0:
        parts.append(f"{item.engagement_rate * 100:.1f}% de engajamento ponderado")
    if item.retention_value is not None:
        value = item.retention_value
        parts.append(f"{value * 100:.1f}% de retenção" if value <= 1.5 else f"{value:.1f}s médios")
    return " · ".join(parts)


def _fallback_recommendations(
    ranked: list[RankedContent], *, explicit_theme: bool
) -> list[ThemeRecommendation]:
    candidates: list[tuple[str, RankedContent, float]] = []
    if explicit_theme:
        grouped: dict[str, list[RankedContent]] = {}
        display: dict[str, str] = {}
        for item in ranked:
            key = _normalize_header(item.theme)
            grouped.setdefault(key, []).append(item)
            display.setdefault(key, item.theme)
        for key, items in grouped.items():
            best = max(items, key=lambda item: (item.score, item.views))
            mean_score = sum(item.score for item in items) / len(items)
            group_score = best.score * 0.65 + mean_score * 0.35
            candidates.append((display[key], best, group_score))
        candidates.sort(key=lambda value: (value[2], value[1].views), reverse=True)
    else:
        seen: set[str] = set()
        for item in ranked:
            theme = _short_label(item.theme)
            key = _normalize_header(theme)
            if key and key not in seen:
                candidates.append((theme, item, item.score))
                seen.add(key)
    recommendations: list[ThemeRecommendation] = []
    for theme, item, score in candidates[:3]:
        recommendations.append(
            ThemeRecommendation(
                theme=theme,
                why=(
                    f"O conteúdo «{_short_label(item.label, max_words=12)}» ficou entre os "
                    f"melhores do período (score {score:.0f}/100)."
                ),
                next_video=f"Crie uma nova abordagem de «{theme}», com gancho diferente nos primeiros segundos.",
                evidence=_evidence(item),
                score=round(score, 1),
            )
        )
    variants = ("curiosidades e fatos", "comparação e ranking", "erros e lições")
    base = recommendations[0] if recommendations else None
    while base is not None and len(recommendations) < 3:
        angle = variants[len(recommendations)]
        recommendations.append(
            ThemeRecommendation(
                theme=f"{base.theme}: {angle}",
                why="Variação do tema líder para testar um ângulo novo sem abandonar o sinal positivo.",
                next_video=f"Transforme «{base.theme}» em um vídeo de {angle}.",
                evidence=base.evidence,
                score=max(0.0, round(base.score - len(recommendations) * 5, 1)),
            )
        )
    return recommendations


def _extract_json_array(text: str) -> list[dict[str, object]]:
    raw = str(text or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("A IA não retornou uma lista JSON.")
    value = json.loads(raw[start : end + 1])
    if not isinstance(value, list):
        raise ValueError("A resposta da IA não é uma lista.")
    return [item for item in value if isinstance(item, dict)]


def _recommend_with_groq(
    ranked: list[RankedContent], fallback: list[ThemeRecommendation], *, explicit_theme: bool
) -> list[ThemeRecommendation]:
    rows = []
    for index, item in enumerate(ranked[:20], start=1):
        rows.append(
            {
                "id": index,
                "conteudo": item.label[:240],
                "tema_csv": item.theme[:120] if explicit_theme else None,
                "score": item.score,
                "visualizacoes": int(item.views),
                "engajamento_pct": round(item.engagement_rate * 100, 3),
                "retencao": round(item.retention_value, 4) if item.retention_value is not None else None,
            }
        )
    prompt = (
        "Você é estrategista de conteúdo para vídeos curtos. Use SOMENTE os dados fornecidos "
        "para recomendar os 3 temas que devem ser publicados nos próximos dias. "
        "Agrupe títulos/legendas semanticamente quando não houver tema explícito. Priorize sinais "
        "repetidos, visualizações, engajamento e retenção; não invente números. Os três temas devem "
        "ser distintos, específicos e escritos em português brasileiro.\n\n"
        f"Há coluna explícita de tema: {explicit_theme}.\n"
        f"Ranking calculado localmente:\n{json.dumps(rows, ensure_ascii=False)}\n\n"
        "Retorne APENAS um array JSON com exatamente 3 objetos neste formato:\n"
        '[{"theme":"tema curto","why":"motivo ancorado nos dados",'
        '"next_video":"ideia concreta para o próximo vídeo","evidence":"evidência curta"}]'
    )
    content = groq_user_message_text(
        prompt,
        temperature=0.2,
        max_tokens=900,
        none_as_empty=True,
        retry_label="análise de desempenho",
        bad_request_runtime=lambda exc: RuntimeError(f"CSV grande demais para a análise: {exc}"),
        rate_limit_message="Groq rate limit excedido ao analisar o desempenho.",
        model=GROQ_FAST_MODEL,
    )
    data = _extract_json_array(content or "")
    recommendations: list[ThemeRecommendation] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        theme = str(item.get("theme") or "").strip()
        why = str(item.get("why") or "").strip()
        next_video = str(item.get("next_video") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        key = _normalize_header(theme)
        if not key or key in seen or not why or not next_video:
            continue
        local = fallback[min(index, len(fallback) - 1)]
        recommendations.append(
            ThemeRecommendation(
                theme=theme[:100],
                why=why[:360],
                next_video=next_video[:300],
                evidence=(evidence or local.evidence)[:240],
                score=local.score,
            )
        )
        seen.add(key)
        if len(recommendations) == 3:
            break
    if len(recommendations) != 3:
        raise ValueError("A IA não retornou três recomendações válidas.")
    return recommendations


def analyze_performance_csv(path: str | Path, *, use_ai: bool = True) -> PerformanceAnalysis:
    """Analisa o CSV inteiro e devolve exatamente três recomendações editoriais."""
    source = Path(path).expanduser().resolve()
    headers, rows = _read_csv(source)
    mapped = _detect_columns(headers)
    ranked = _rank_rows(rows, mapped)
    explicit_theme = "theme" in mapped
    fallback = _fallback_recommendations(ranked, explicit_theme=explicit_theme)
    recommendations = fallback
    used_ai = False
    if use_ai:
        try:
            recommendations = _recommend_with_groq(
                ranked, fallback, explicit_theme=explicit_theme
            )
            used_ai = True
        except Exception as exc:  # a análise local deve continuar disponível offline
            _log.warning("Síntese semântica indisponível; usando ranking local: %s", exc)
    mapped_names = ", ".join(
        f"{key}={value}" for key, value in mapped.items() if key != "date"
    )
    summary = (
        f"{len(ranked)} de {len(rows)} linha(s) válidas · "
        f"colunas detectadas: {mapped_names} · "
        f"recomendação {'com síntese da IA' if used_ai else 'pelo ranking local'}"
    )
    return PerformanceAnalysis(
        source_path=source,
        row_count=len(rows),
        valid_row_count=len(ranked),
        mapped_columns=dict(mapped),
        recommendations=tuple(recommendations[:3]),
        used_ai=used_ai,
        summary=summary,
    )
