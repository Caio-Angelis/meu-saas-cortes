"""Histórico local das fontes de vídeo já baixadas pelo projeto.

O projeto é usado localmente por uma única pessoa, então SQLite é suficiente.
Além de persistir entre execuções, o ``claim`` usa uma transação curta para
impedir que duas tarefas baixem a mesma fonte ao mesmo tempo.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.config import SOURCE_HISTORY_DB

_USED_STATUSES = ("claimed", "downloaded")
_YOUTUBE_HOSTS = {"youtube.com", "youtu.be"}
_TRACKING_QUERY_KEYS = {
    "ab_channel",
    "feature",
    "fbclid",
    "gclid",
    "index",
    "list",
    "pp",
    "si",
    "start",
    "start_radio",
    "t",
}


class DuplicateSourceError(RuntimeError):
    """Indica que a fonte já foi baixada ou está sendo baixada."""

    def __init__(self, source_url: str) -> None:
        super().__init__(f"Vídeo já registrado no histórico: {source_url}")
        self.source_url = source_url


@dataclass(frozen=True)
class DownloadedSource:
    """Arquivo local associado a uma fonte que já foi baixada."""

    path: str
    channel: str | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _youtube_host(host: str) -> bool:
    return host in _YOUTUBE_HOSTS or host.endswith(".youtube.com")


def _youtube_video_id(parts) -> str | None:
    host = (parts.hostname or "").lower().rstrip(".")
    path_parts = [part for part in parts.path.split("/") if part]
    query = {
        key.lower(): value
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    }

    if host == "youtu.be" and path_parts:
        return path_parts[0]
    if path_parts and path_parts[0].lower() in {"shorts", "embed", "live"}:
        return path_parts[1] if len(path_parts) > 1 else None
    if path_parts and path_parts[0].lower() == "watch":
        return query.get("v") or None
    return query.get("v") or None


def canonical_source_key(url: str) -> str:
    """Retorna uma identidade estável para uma URL de vídeo.

    URLs equivalentes do YouTube (``watch``, ``youtu.be``, ``shorts`` e
    parâmetros de rastreamento) são reduzidas ao ID do vídeo. Para outras
    fontes, o esquema, host, caminho e query relevante são normalizados.
    """

    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL da fonte vazia.")
    if raw.startswith("//"):
        raw = "https:" + raw
    elif "://" not in raw:
        raw = "https://" + raw

    parts = urlsplit(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if _youtube_host(host):
        video_id = _youtube_video_id(parts)
        if video_id:
            return f"youtube:{video_id}"

    scheme = (parts.scheme or "https").lower()
    netloc = host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
        and not key.lower().startswith("utm_")
    ]
    query = urlencode(sorted(query_pairs))
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    return f"url:{normalized}"


def _key_from_value(source_or_key: str) -> str:
    value = (source_or_key or "").strip()
    if value.startswith(("youtube:", "url:")):
        return value
    return canonical_source_key(value)


def _downloaded_file_available(path: str | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    try:
        return candidate.is_file() and candidate.stat().st_size > 0
    except OSError:
        return False


class SourceHistory:
    """Persistência mínima para fontes baixadas."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        configured = (os.getenv("SOURCE_HISTORY_DB") or "").strip()
        default_path = Path(configured).expanduser() if configured else SOURCE_HISTORY_DB
        self._path = Path(db_path or default_path)
        self._lock = threading.Lock()
        self._init_db()

    @property
    def db_path(self) -> Path:
        return self._path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS video_sources (
                    source_key TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    downloaded_path TEXT,
                    channel TEXT,
                    error_message TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_video_sources_status
                    ON video_sources(status);
                """
            )

    def claim(self, source_url: str) -> str:
        """Reserva uma fonte para uma tarefa de download.

        Registros com falha podem ser tentados novamente; ``claimed`` e
        ``downloaded`` são considerados fontes já usadas.
        """

        key = canonical_source_key(source_url)
        source_url = (source_url or "").strip()
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT status, downloaded_path FROM video_sources WHERE source_key = ?",
                    (key,),
                ).fetchone()
                if row is not None and row["status"] in _USED_STATUSES:
                    can_recover_missing_file = (
                        row["status"] == "downloaded"
                        and not _downloaded_file_available(row["downloaded_path"])
                    )
                    if not can_recover_missing_file:
                        raise DuplicateSourceError(source_url)
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO video_sources (
                            source_key, source_url, status, first_seen_at, last_seen_at
                        ) VALUES (?, ?, 'claimed', ?, ?)
                        """,
                        (key, source_url, now, now),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE video_sources
                        SET source_url = ?, status = 'claimed', error_message = NULL,
                            downloaded_path = NULL, last_seen_at = ?
                        WHERE source_key = ?
                        """,
                        (source_url, now, key),
                    )
        return key

    def get_downloaded(self, source_url: str) -> DownloadedSource | None:
        """Retorna um download existente somente se o arquivo ainda estiver disponível."""

        key = canonical_source_key(source_url)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT downloaded_path, channel
                FROM video_sources
                WHERE source_key = ? AND status = 'downloaded'
                """,
                (key,),
            ).fetchone()
        if row is None or not _downloaded_file_available(row["downloaded_path"]):
            return None
        return DownloadedSource(path=str(row["downloaded_path"]), channel=row["channel"])

    def mark_downloaded(
        self,
        source_or_key: str,
        *,
        downloaded_path: str | None = None,
        channel: str | None = None,
    ) -> None:
        key = _key_from_value(source_or_key)
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE video_sources
                    SET status = 'downloaded', downloaded_path = ?, channel = ?,
                        error_message = NULL, last_seen_at = ?
                    WHERE source_key = ?
                    """,
                    (downloaded_path, channel, now, key),
                )

    def mark_failed(self, source_or_key: str, error_message: str | None = None) -> None:
        key = _key_from_value(source_or_key)
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE video_sources
                    SET status = 'failed', error_message = ?, last_seen_at = ?
                    WHERE source_key = ?
                    """,
                    ((error_message or "")[:1000] or None, now, key),
                )

    def is_used(self, source_url: str) -> bool:
        key = canonical_source_key(source_url)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM video_sources WHERE source_key = ?",
                (key,),
            ).fetchone()
        return row is not None and row["status"] in _USED_STATUSES

    def used_keys(self) -> set[str]:
        placeholders = ", ".join("?" for _ in _USED_STATUSES)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT source_key FROM video_sources WHERE status IN ({placeholders})",
                _USED_STATUSES,
            ).fetchall()
        return {str(row["source_key"]) for row in rows}


_history_instance: SourceHistory | None = None
_history_instance_lock = threading.Lock()


def get_source_history() -> SourceHistory:
    """Devolve a instância compartilhada pelo processo."""

    global _history_instance
    if _history_instance is None:
        with _history_instance_lock:
            if _history_instance is None:
                _history_instance = SourceHistory()
    return _history_instance
