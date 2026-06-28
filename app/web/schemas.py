"""Modelos de resposta da API web."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.web.store import PlaylistItem


class JobCreatedResponse(BaseModel):
    ok: bool = True
    message: str = "Job iniciado."
    job_id: str | None = None
    playlist_item_id: int | None = None


class RunItem(BaseModel):
    name: str
    path: str
    duration: str = "—"


class RunsResponse(BaseModel):
    items: list[RunItem] = Field(default_factory=list)


class PlaylistItemResponse(BaseModel):
    id: int
    source: str
    source_type: str
    title: str | None = None
    workflow_status: str
    pipeline_status: str
    progress: float
    error_message: str | None = None
    outputs: list[str] = Field(default_factory=list)
    can_process: bool = False
    can_mark_workflow: bool = False
    created_at: str
    updated_at: str

    @classmethod
    def from_item(cls, item: PlaylistItem) -> PlaylistItemResponse:
        return cls(
            id=item.id,
            source=item.source,
            source_type=item.source_type,
            title=item.title,
            workflow_status=item.workflow_status,
            pipeline_status=item.pipeline_status,
            progress=item.progress,
            error_message=item.error_message,
            outputs=item.outputs,
            can_process=item.can_process,
            can_mark_workflow=item.can_mark_workflow,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class PlaylistListResponse(BaseModel):
    items: list[PlaylistItemResponse] = Field(default_factory=list)
    redis: bool = False
    message: str | None = None


class PlaylistProcessResponse(BaseModel):
    ok: bool = True
    enqueued: int = 0
    job_ids: list[str] = Field(default_factory=list)
    message: str = ""
    redis: bool = False
