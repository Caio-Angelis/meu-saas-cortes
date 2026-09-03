"""Feedback loop de retenção/crescimento para o pipeline de cortes.

Consome o relatório JSON exportado do TikTok (mesmo formato de
``tiktok_report_*.json``) e transforma métricas reais de cada post em sinais
acionáveis: duração vencedora, janelas de publicação, padrões de legenda e
"ímãs de seguidores". O resultado pode ser persistido como
``data/growth_profile.json``, que o pipeline de cortes lê para ajustar a
duração-alvo dos próximos clipes quando ``CLIP_DURATION`` não foi fixado
manualmente.

Correlação histórica não é causalidade — os sinais são usados como prioridade
editorial, nunca como regra dura.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from app.analytics.content_profile import _GENERIC_TERMS, _normalize
from app.core.config import GROWTH_PROFILE_PATH

_log = logging.getLogger(__name__)

_MIN_GROUP = 3

_BUCKET_MID_SECONDS: dict[str, int] = {
    "0-30s": 25,
    "31-45s": 40,
    "46-60s": 53,
    "61-90s": 75,
    "91s+": 95,
}


class RetentionLoopError(ValueError):
    """Relatório ausente, ilegível ou sem vídeos com métricas suficientes."""


@dataclass(frozen=True)
class DurationSignal:
    bucket: str
    samples: int
    median_views: float
    median_engagement: float


@dataclass(frozen=True)
class PostWindowSignal:
    kind: str  # "weekday" | "hour"
    label: str
    samples: int
    median_views: float
    median_engagement: float


@dataclass(frozen=True)
class CaptionSignal:
    feature: str
    with_samples: int
    without_samples: int
    lift: float  # mediana de engajamento com / sem o recurso


@dataclass(frozen=True)
class FollowerMagnet:
    topic: str
    followers_gained: float
    samples: int


@dataclass(frozen=True)
class RetentionLoopInsight:
    source_path: str
    video_count: int
    duration_signals: tuple[DurationSignal, ...]
    post_windows: tuple[PostWindowSignal, ...]
    caption_signals: tuple[CaptionSignal, ...]
    follower_magnets: tuple[FollowerMagnet, ...]
    recommended_clip_duration_sec: int | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: object) -> float:
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _metric(row: dict[str, Any], key: str) -> float:
    nested = row.get("current_metrics")
    if isinstance(nested, dict) and nested.get(key) is not None:
        return _number(nested.get(key))
    return _number(row.get(key))


def _follower_gain(row: dict[str, Any]) -> float | None:
    after = row.get("account_followers_24h_after_publish")
    near = row.get("account_followers_near_publish")
    if after is None or near is None:
        return None
    return _number(after) - _number(near)


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


def _median(values: list[float]) -> float:
    return median(values) if values else 0.0


def _group_windows(
    rows: list[dict[str, Any]], key: str, kind: str, *, label_map: Any = None
) -> list[PostWindowSignal]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        raw = row.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        groups[str(raw)].append(row)
    signals = []
    for label, items in groups.items():
        if len(items) < _MIN_GROUP:
            continue
        signals.append(
            PostWindowSignal(
                kind=kind,
                label=label_map(label) if label_map else label,
                samples=len(items),
                median_views=_median([item["views"] for item in items]),
                median_engagement=_median([item["engagement"] for item in items]),
            )
        )
    signals.sort(key=lambda s: (s.median_views, s.median_engagement), reverse=True)
    return signals[:3]


def _caption_lift(
    rows: list[dict[str, Any]], feature: str, has: Any
) -> CaptionSignal | None:
    with_rows = [row for row in rows if has(row)]
    without_rows = [row for row in rows if not has(row)]
    if len(with_rows) < _MIN_GROUP or len(without_rows) < _MIN_GROUP:
        return None
    base = _median([row["engagement"] for row in without_rows])
    if base <= 0:
        return None
    lift = _median([row["engagement"] for row in with_rows]) / base
    return CaptionSignal(
        feature=feature,
        with_samples=len(with_rows),
        without_samples=len(without_rows),
        lift=round(lift, 2),
    )


def _topics_of(row: dict[str, Any]) -> list[str]:
    tags = row.get("hashtags")
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    for tag in tags:
        clean = str(tag).strip("# ")
        if not clean or _normalize(clean) in _GENERIC_TERMS:
            continue
        out.append(clean)
    description = str(row.get("description") or "")
    text = description.split("Review original:", 1)[0].strip()
    words = [w for w in text.split() if len(w) > 5 and w[0].isupper()]
    return out + words[:2]


def build_retention_insight(report: object, *, source_path: str = "") -> RetentionLoopInsight:
    """Reduz o relatório a sinais editoriais com amostra mínima e sem inventar métricas."""
    raw_rows = _rows_from_report(report)
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        views = _metric(row, "views")
        if views <= 0:
            continue
        engagement = _metric(row, "engagement_rate")
        if engagement <= 0:
            likes = _metric(row, "likes")
            comments = _metric(row, "comments")
            shares = _metric(row, "shares")
            engagement = (likes + comments * 2 + shares * 3) / views * 100.0
        rows.append(
            {
                "views": views,
                "engagement": engagement,
                "bucket": _duration_bucket(row),
                "weekday": row.get("publication_weekday"),
                "hour": row.get("publication_hour"),
                "question": bool(row.get("has_question_mark")),
                "exclamation": bool(row.get("has_exclamation_mark")),
                "emojis": bool(row.get("has_emojis")),
                "hashtags_count": int(_number(row.get("hashtags_count"))),
                "follower_gain": _follower_gain(row),
                "topics": _topics_of(row),
            }
        )
    if len(rows) < _MIN_GROUP:
        raise RetentionLoopError(
            "O relatório não tem vídeos suficientes com visualizações "
            f"(mínimo {_MIN_GROUP}; encontrados {len(rows)})."
        )

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["bucket"]:
            buckets[row["bucket"]].append(row)
    duration_signals = tuple(
        sorted(
            (
                DurationSignal(
                    bucket=bucket,
                    samples=len(items),
                    median_views=_median([i["views"] for i in items]),
                    median_engagement=_median([i["engagement"] for i in items]),
                )
                for bucket, items in buckets.items()
            ),
            key=lambda s: (s.median_views, s.median_engagement),
            reverse=True,
        )
    )

    post_windows = tuple(
        _group_windows(rows, "weekday", "weekday")
        + _group_windows(rows, "hour", "hour", label_map=lambda h: f"{int(h):02d}h")
    )

    caption_signals = tuple(
        signal
        for signal in (
            _caption_lift(rows, "pergunta na legenda", lambda r: r["question"]),
            _caption_lift(rows, "exclamação na legenda", lambda r: r["exclamation"]),
            _caption_lift(rows, "emojis na legenda", lambda r: r["emojis"]),
            _caption_lift(rows, "3 a 5 hashtags", lambda r: 3 <= r["hashtags_count"] <= 5),
            _caption_lift(rows, "6+ hashtags", lambda r: r["hashtags_count"] >= 6),
        )
        if signal is not None
    )

    magnet_totals: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        gain = row["follower_gain"]
        if gain is None:
            continue
        for topic in {t.lower() for t in row["topics"]}:
            magnet_totals[topic].append(gain)
    magnets = tuple(
        sorted(
            (
                FollowerMagnet(
                    topic=topic,
                    followers_gained=sum(gains),
                    samples=len(gains),
                )
                for topic, gains in magnet_totals.items()
                if len(gains) >= 2
            ),
            key=lambda m: (m.followers_gained, m.samples),
            reverse=True,
        )[:5]
    )

    recommended: int | None = None
    for signal in duration_signals:
        if signal.samples >= _MIN_GROUP:
            recommended = _BUCKET_MID_SECONDS.get(signal.bucket, 50)
            break

    draft = RetentionLoopInsight(
        source_path=source_path,
        video_count=len(rows),
        duration_signals=duration_signals,
        post_windows=post_windows,
        caption_signals=caption_signals,
        follower_magnets=magnets,
        recommended_clip_duration_sec=recommended,
        summary="",
    )
    return replace(draft, summary=_summarize(draft))


def _summarize(insight: RetentionLoopInsight) -> str:
    lines = [f"{insight.video_count} vídeos com métricas analisados."]
    solid = [s for s in insight.duration_signals if s.samples >= _MIN_GROUP]
    if solid:
        top = solid[0]
        lines.append(
            f"Duração vencedora: {top.bucket} (mediana {top.median_views:.0f} views, "
            f"{top.samples} vídeos)."
        )
    if insight.recommended_clip_duration_sec:
        lines.append(
            f"Duração-alvo sugerida para novos cortes: "
            f"{insight.recommended_clip_duration_sec}s."
        )
    windows = [w for w in insight.post_windows]
    if windows:
        pretty = ", ".join(f"{w.label} ({w.median_views:.0f} views)" for w in windows[:4])
        lines.append(f"Melhores janelas de publicação: {pretty}.")
    strong = [c for c in insight.caption_signals if c.lift >= 1.15]
    weak = [c for c in insight.caption_signals if c.lift <= 0.85]
    if strong:
        lines.append(
            "Legenda que performa: " + ", ".join(f"{c.feature} (×{c.lift})" for c in strong) + "."
        )
    if weak:
        lines.append(
            "Legenda que segura o alcance: "
            + ", ".join(f"{c.feature} (×{c.lift})" for c in weak)
            + "."
        )
    if insight.follower_magnets:
        magnets = ", ".join(
            f"#{m.topic} (+{m.followers_gained:.0f})" for m in insight.follower_magnets[:3]
        )
        lines.append(f"Ímãs de seguidores: {magnets}.")
    lines.append("Correlação histórica — use como prioridade editorial, não como regra dura.")
    return "\n".join(lines)


def analyze_retention_report_file(path: str | Path) -> RetentionLoopInsight:
    source = Path(path).expanduser()
    if not source.is_file():
        raise RetentionLoopError(f"Relatório não encontrado: {source}")
    try:
        report = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionLoopError(f"Não foi possível ler o relatório JSON: {exc}") from exc
    return build_retention_insight(report, source_path=str(source))


def save_growth_profile(insight: RetentionLoopInsight, path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else GROWTH_PROFILE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_path": insight.source_path,
        "video_count": insight.video_count,
        "recommended_clip_duration_sec": insight.recommended_clip_duration_sec,
        "best_post_windows": [asdict(w) for w in insight.post_windows[:4]],
        "caption_signals": [asdict(c) for c in insight.caption_signals],
        "follower_magnets": [asdict(m) for m in insight.follower_magnets[:5]],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_growth_profile(path: str | Path | None = None) -> dict[str, Any] | None:
    target = Path(path) if path is not None else GROWTH_PROFILE_PATH
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
