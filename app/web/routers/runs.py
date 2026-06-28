"""GET /api/runs — listagem de clipes recentes em OUTPUT_DIR."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app.core.config import OUTPUT_DIR
from app.gui.gui_export import ffprobe_duration_seconds, format_duration_hms
from app.web.schemas import RunItem, RunsResponse

router = APIRouter(tags=["runs"])


def _list_runs_sync(limit: int = 30) -> list[RunItem]:
    if not OUTPUT_DIR.is_dir():
        return []
    mp4s = sorted(
        OUTPUT_DIR.glob("*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    items: list[RunItem] = []
    for p in mp4s:
        dur = format_duration_hms(ffprobe_duration_seconds(str(p)))
        items.append(RunItem(name=p.name, path=str(p.resolve()), duration=dur))
    return items


@router.get("/api/runs", response_model=RunsResponse)
async def list_runs(limit: int = 30) -> RunsResponse:
    items = await asyncio.to_thread(_list_runs_sync, min(max(limit, 1), 100))
    return RunsResponse(items=items)
