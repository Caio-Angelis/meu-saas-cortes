"""Estado do job em execução e fan-out de eventos para SSE."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobSnapshot:
    status: str = "idle"  # idle | running | done | error
    progress: float = 0.0
    message: str = ""
    outputs: list[str] = field(default_factory=list)
    error: str | None = None
    active_item_id: int | None = None
    active_source: str | None = None


class ProgressHub:
    """Singleton thread-safe: worker publica; corrotinas SSE consomem."""

    _instance: ProgressHub | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.snapshot = JobSnapshot()
        self._subscribers: set[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop]] = (
            set()
        )
        self._hub_lock = threading.Lock()

    @classmethod
    def get(cls) -> ProgressHub:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def is_running(self) -> bool:
        with self._hub_lock:
            return self.snapshot.status == "running"

    def _emit_threadsafe(self, event: dict[str, Any]) -> None:
        for q, loop in list(self._subscribers):
            if not loop.is_running():
                continue
            loop.call_soon_threadsafe(self._put_on_queue, q, event)

    @staticmethod
    def _put_on_queue(q: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add((q, loop))
        with self._hub_lock:
            snap = self.snapshot
        if snap.status != "idle":
            q.put_nowait(self._snapshot_event(snap))
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers = {(qq, lp) for qq, lp in self._subscribers if qq is not q}

    @staticmethod
    def _snapshot_event(snap: JobSnapshot) -> dict[str, Any]:
        if snap.status == "running":
            return {"type": "progress", "frac": snap.progress, "message": snap.message}
        if snap.status == "done":
            return {
                "type": "done",
                "frac": 1.0,
                "outputs": snap.outputs,
                "message": snap.message,
            }
        if snap.status == "error":
            return {"type": "error", "message": snap.error or snap.message}
        return {"type": "idle"}

    def set_active_item(self, item_id: int, source: str) -> None:
        with self._hub_lock:
            self.snapshot.active_item_id = item_id
            self.snapshot.active_source = source

    def clear_active_item(self) -> None:
        with self._hub_lock:
            self.snapshot.active_item_id = None
            self.snapshot.active_source = None

    def mark_running(self, message: str = "Iniciando…") -> None:
        with self._hub_lock:
            prev_id = self.snapshot.active_item_id
            prev_src = self.snapshot.active_source
            self.snapshot = JobSnapshot(
                status="running",
                progress=0.0,
                message=message,
                active_item_id=prev_id,
                active_source=prev_src,
            )
        self._emit_threadsafe(
            {
                "type": "status",
                "status": "running",
                "message": message,
                "frac": 0.0,
                "item_id": prev_id,
            }
        )

    def publish_progress(self, frac: float, message: str | None = None) -> None:
        frac = max(0.0, min(1.0, float(frac)))
        with self._hub_lock:
            self.snapshot.progress = frac
            if message:
                self.snapshot.message = message
            msg = self.snapshot.message
        self._emit_threadsafe(
            {"type": "progress", "frac": frac, "message": msg}
        )

    def publish_log(self, message: str) -> None:
        with self._hub_lock:
            self.snapshot.message = message
        self._emit_threadsafe({"type": "log", "message": message})

    def mark_done(self, outputs: list[str], message: str = "Concluído.") -> None:
        with self._hub_lock:
            self.snapshot = JobSnapshot(
                status="done",
                progress=1.0,
                message=message,
                outputs=list(outputs),
            )
        self._emit_threadsafe(
            {
                "type": "done",
                "frac": 1.0,
                "outputs": outputs,
                "message": message,
            }
        )

    def mark_error(self, error: str) -> None:
        with self._hub_lock:
            self.snapshot = JobSnapshot(status="error", error=error, message=error)
        self._emit_threadsafe({"type": "error", "message": error})

    def reset_idle(self) -> None:
        with self._hub_lock:
            self.snapshot = JobSnapshot()
        self._emit_threadsafe({"type": "idle"})

    def notify_playlist_update(self) -> None:
        self._emit_threadsafe({"type": "playlist_refresh"})

    @staticmethod
    def sse_payload(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
