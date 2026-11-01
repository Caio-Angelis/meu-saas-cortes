"""Endpoints para os workspaces de criação da nova interface web."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.pipelines.batalha.batalha_pipeline import normalize_batalha_modo
from app.pipelines.quiz.quiz_pipeline import normalize_quiz_difficulty
from app.tts.tts_voices import default_voice_id
from app.web.hub import ProgressHub
from app.web.queue_backend import enqueue_studio_job, redis_available
from app.web.schemas import JobCreatedResponse

router = APIRouter(tags=["studio"])


class StudioJobRequest(BaseModel):
    job_type: Literal["quiz", "batalha", "historia", "tts"]
    theme: str = ""
    count: int = Field(default=5, ge=1, le=10)
    timer_sec: int = Field(default=5, ge=3, le=10)
    tts_voice: str = ""
    difficulty: str = "variado"
    cor_fundo: str = "#1A1A1A"
    modo: str = "tamanho"
    story_text: str = ""
    text: str = ""


class TtsPreviewRequest(BaseModel):
    text: str = ""
    tts_voice: str = ""


def _color(value: str, default: str) -> str:
    raw = (value or "").strip()
    if not raw.startswith("#"):
        raw = "#" + raw
    return raw.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw) else default


@router.post("/api/studio/jobs", response_model=JobCreatedResponse)
async def create_studio_job(body: StudioJobRequest) -> JobCreatedResponse:
    if ProgressHub.get().is_running():
        raise HTTPException(status_code=409, detail="Já existe um processamento em andamento.")

    payload: dict[str, Any] = (
        body.model_dump() if hasattr(body, "model_dump") else body.dict()
    )
    if not payload.get("tts_voice"):
        payload["tts_voice"] = default_voice_id()
    payload["cor_fundo"] = _color(str(payload.get("cor_fundo") or ""), "#1A1A1A")

    if body.job_type in ("quiz", "batalha") and not body.theme.strip():
        raise HTTPException(status_code=400, detail="Informe um tema para continuar.")
    if body.job_type == "historia" and len(body.story_text.strip()) < 20:
        raise HTTPException(status_code=400, detail="Escreva uma história com pelo menos 20 caracteres.")
    if body.job_type == "tts" and not body.text.strip():
        raise HTTPException(status_code=400, detail="Informe o texto da locução.")

    if body.job_type == "quiz":
        payload["difficulty"] = normalize_quiz_difficulty(body.difficulty)
    if body.job_type == "batalha":
        payload["modo"] = normalize_batalha_modo(body.modo)

    job_id = f"studio-{uuid.uuid4().hex}"
    queued_id = enqueue_studio_job(job_id, payload)
    provider = "Redis/RQ" if redis_available() else "thread local"
    label = {
        "quiz": "Quiz",
        "batalha": "Batalha",
        "historia": "História",
        "tts": "TTS",
    }[body.job_type]
    return JobCreatedResponse(
        message=f"{label} enfileirado ({provider}).",
        job_id=queued_id,
    )


@router.post("/api/studio/tts/preview")
async def preview_tts(body: TtsPreviewRequest) -> dict[str, str]:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Digite algum texto para ouvir a amostra.")
    from app.tts.tts_standalone import synthesize_tts_preview

    path = await asyncio.to_thread(
        synthesize_tts_preview,
        body.text,
        body.tts_voice,
    )
    from app.core.config import TEMP_DIR

    relative = path.removeprefix(str(TEMP_DIR.resolve())).lstrip("/")
    return {"url": "/preview-media/" + relative.replace("\\", "/")}
