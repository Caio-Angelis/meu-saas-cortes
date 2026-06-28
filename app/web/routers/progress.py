"""GET /api/progress — Server-Sent Events do pipeline."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.web.hub import ProgressHub

router = APIRouter(tags=["progress"])


@router.get("/api/progress")
async def progress_stream() -> StreamingResponse:
    hub = ProgressHub.get()

    async def event_generator():
        q = hub.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield hub.sse_payload(event)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/progress/snapshot")
async def progress_snapshot() -> dict:
    snap = ProgressHub.get().snapshot
    return {
        "status": snap.status,
        "progress": snap.progress,
        "message": snap.message,
        "outputs": snap.outputs,
        "error": snap.error,
        "active_item_id": snap.active_item_id,
        "active_source": snap.active_source,
    }
