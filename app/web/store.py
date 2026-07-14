"""Persistência SQLite — fila de playlist e estado de workflow (pendente/publicado/descartado)."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from app.core.config import OUTPUT_DIR

WORKFLOW_PENDING = "pendente"
WORKFLOW_PUBLISHED = "publicado"
WORKFLOW_DISCARDED = "descartado"
WORKFLOW_VALUES = (WORKFLOW_PENDING, WORKFLOW_PUBLISHED, WORKFLOW_DISCARDED)

PIPELINE_IDLE = "idle"
PIPELINE_QUEUED = "queued"
PIPELINE_RUNNING = "running"
PIPELINE_DONE = "done"
PIPELINE_ERROR = "error"
PIPELINE_VALUES = (
    PIPELINE_IDLE,
    PIPELINE_QUEUED,
    PIPELINE_RUNNING,
    PIPELINE_DONE,
    PIPELINE_ERROR,
)

_DB_DIR = OUTPUT_DIR.parent / "data"
_DEFAULT_DB = _DB_DIR / "web_jobs.sqlite"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class PlaylistItem:
    id: int
    source: str
    source_type: str
    title: str | None
    workflow_status: str
    pipeline_status: str
    progress: float
    error_message: str | None
    outputs: list[str]
    rq_job_id: str | None
    created_at: str
    updated_at: str

    @property
    def can_process(self) -> bool:
        return (
            self.workflow_status == WORKFLOW_PENDING
            and self.pipeline_status in (PIPELINE_IDLE, PIPELINE_ERROR)
        )

    @property
    def can_mark_workflow(self) -> bool:
        return self.pipeline_status == PIPELINE_DONE and self.workflow_status == WORKFLOW_PENDING


def _row_to_item(row: sqlite3.Row) -> PlaylistItem:
    raw = row["outputs_json"]
    outputs: list[str] = []
    if raw:
        try:
            outputs = json.loads(raw)
        except json.JSONDecodeError:
            outputs = []
    return PlaylistItem(
        id=int(row["id"]),
        source=row["source"],
        source_type=row["source_type"],
        title=row["title"],
        workflow_status=row["workflow_status"],
        pipeline_status=row["pipeline_status"],
        progress=float(row["progress"] or 0),
        error_message=row["error_message"],
        outputs=outputs,
        rq_job_id=row["rq_job_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class JobStore:
    """Acesso thread-safe ao SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = Path(db_path or _DEFAULT_DB)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'url',
                    title TEXT,
                    workflow_status TEXT NOT NULL DEFAULT 'pendente',
                    pipeline_status TEXT NOT NULL DEFAULT 'idle',
                    progress REAL NOT NULL DEFAULT 0,
                    error_message TEXT,
                    outputs_json TEXT,
                    rq_job_id TEXT,
                    pipeline_options_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_playlist_workflow
                    ON playlist_items(workflow_status);
                CREATE INDEX IF NOT EXISTS idx_playlist_pipeline
                    ON playlist_items(pipeline_status);
                """
            )

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

    def add_urls(self, urls: list[str], *, pipeline_options: dict[str, Any] | None = None) -> list[int]:
        opts = json.dumps(pipeline_options or {}, ensure_ascii=False)
        now = _utc_now()
        ids: list[int] = []
        with self._lock:
            with self._connect() as conn:
                for url in urls:
                    cur = conn.execute(
                        """
                        INSERT INTO playlist_items (
                            source, source_type, workflow_status, pipeline_status,
                            pipeline_options_json, created_at, updated_at
                        ) VALUES (?, 'url', ?, ?, ?, ?, ?)
                        """,
                        (url, WORKFLOW_PENDING, PIPELINE_IDLE, opts, now, now),
                    )
                    ids.append(int(cur.lastrowid))
        return ids

    def add_local_paths(self, paths: list[str], *, pipeline_options: dict[str, Any] | None = None) -> list[int]:
        opts = json.dumps(pipeline_options or {}, ensure_ascii=False)
        now = _utc_now()
        ids: list[int] = []
        with self._lock:
            with self._connect() as conn:
                for p in paths:
                    cur = conn.execute(
                        """
                        INSERT INTO playlist_items (
                            source, source_type, workflow_status, pipeline_status,
                            pipeline_options_json, created_at, updated_at
                        ) VALUES (?, 'file', ?, ?, ?, ?, ?)
                        """,
                        (p, WORKFLOW_PENDING, PIPELINE_IDLE, opts, now, now),
                    )
                    ids.append(int(cur.lastrowid))
        return ids

    def get(self, item_id: int) -> PlaylistItem | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM playlist_items WHERE id = ?", (item_id,)
                ).fetchone()
        return _row_to_item(row) if row else None

    def list_items(
        self,
        *,
        workflow: str | None = None,
        limit: int = 200,
    ) -> list[PlaylistItem]:
        q = "SELECT * FROM playlist_items"
        params: list[Any] = []
        if workflow:
            q += " WHERE workflow_status = ?"
            params.append(workflow)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(q, params).fetchall()
        return [_row_to_item(r) for r in rows]

    def list_processable(self, *, item_ids: list[int] | None = None) -> list[PlaylistItem]:
        with self._lock:
            with self._connect() as conn:
                if item_ids:
                    placeholders = ",".join("?" * len(item_ids))
                    rows = conn.execute(
                        f"""
                        SELECT * FROM playlist_items
                        WHERE id IN ({placeholders})
                          AND workflow_status = ?
                          AND pipeline_status IN (?, ?)
                        ORDER BY id ASC
                        """,
                        (*item_ids, WORKFLOW_PENDING, PIPELINE_IDLE, PIPELINE_ERROR),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM playlist_items
                        WHERE workflow_status = ?
                          AND pipeline_status IN (?, ?)
                        ORDER BY id ASC
                        """,
                        (WORKFLOW_PENDING, PIPELINE_IDLE, PIPELINE_ERROR),
                    ).fetchall()
        return [_row_to_item(r) for r in rows]

    def mark_queued(self, item_id: int, rq_job_id: str | None) -> None:
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE playlist_items
                    SET pipeline_status = ?, rq_job_id = ?, progress = 0,
                        error_message = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (PIPELINE_QUEUED, rq_job_id, now, item_id),
                )

    def mark_running(self, item_id: int, message: str | None = None) -> None:
        now = _utc_now()
        title = message
        with self._lock:
            with self._connect() as conn:
                if title:
                    conn.execute(
                        """
                        UPDATE playlist_items
                        SET pipeline_status = ?, title = COALESCE(?, title), updated_at = ?
                        WHERE id = ?
                        """,
                        (PIPELINE_RUNNING, title, now, item_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE playlist_items
                        SET pipeline_status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (PIPELINE_RUNNING, now, item_id),
                    )

    def update_progress(self, item_id: int, frac: float) -> None:
        frac = max(0.0, min(1.0, float(frac)))
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE playlist_items
                    SET progress = ?, pipeline_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (frac, PIPELINE_RUNNING, now, item_id),
                )

    def mark_pipeline_done(self, item_id: int, outputs: list[str]) -> None:
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE playlist_items
                    SET pipeline_status = ?, progress = 1, outputs_json = ?,
                        error_message = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (PIPELINE_DONE, json.dumps(outputs, ensure_ascii=False), now, item_id),
                )

    def mark_pipeline_error(self, item_id: int, error: str) -> None:
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE playlist_items
                    SET pipeline_status = ?, error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (PIPELINE_ERROR, error[:2000], now, item_id),
                )

    def set_workflow(
        self,
        item_id: int,
        status: Literal["pendente", "publicado", "descartado"],
    ) -> PlaylistItem | None:
        if status not in WORKFLOW_VALUES:
            raise ValueError(f"workflow inválido: {status}")
        now = _utc_now()
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM playlist_items WHERE id = ?", (item_id,)
                ).fetchone()
                if not row:
                    return None
                item = _row_to_item(row)
                if status != WORKFLOW_PENDING and not item.can_mark_workflow:
                    raise ValueError(
                        "Só é possível marcar publicado/descartado após o pipeline concluir "
                        "e enquanto o item estiver pendente."
                    )
                conn.execute(
                    """
                    UPDATE playlist_items
                    SET workflow_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, now, item_id),
                )
                row = conn.execute(
                    "SELECT * FROM playlist_items WHERE id = ?", (item_id,)
                ).fetchone()
        return _row_to_item(row) if row else None

    def get_pipeline_options(self, item_id: int) -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT pipeline_options_json FROM playlist_items WHERE id = ?",
                    (item_id,),
                ).fetchone()
        if not row or not row["pipeline_options_json"]:
            return {}
        try:
            return json.loads(row["pipeline_options_json"])
        except json.JSONDecodeError:
            return {}

    def delete_item(self, item_id: int) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM playlist_items WHERE id = ?", (item_id,))
        return cur.rowcount > 0


_store: JobStore | None = None
_store_lock = threading.Lock()


def get_store() -> JobStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = JobStore()
        return _store
