"""Publicação de um lote diário no YouTube pela API oficial."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.core.config import TEMP_DIR
from app.publishing.youtube_schedule import build_daily_publish_times, youtube_publish_at
from app.publishing.youtube_uploader import (
    YouTubeUploadError,
    build_youtube_client,
    get_existing_youtube_credentials,
    select_upload_batch,
    validate_client_secrets_file,
    verify_youtube_channel,
)
from app.publishing.youtube_workflow import (
    YouTubeIdempotencyConflict,
    YouTubeWorkflowConflict,
    YouTubeWorkflowError,
    YouTubeWorkflowNotFound,
    YouTubeWorkflowStore,
    reconcile_youtube_workflow_upload,
    validate_short_description_before_relation,
    workflow_fingerprint,
)
from app.web.hub import ProgressHub
from app.web.queue_backend import enqueue_studio_job, redis_available
from app.web.schemas import JobCreatedResponse

router = APIRouter(prefix="/api/publish", tags=["publish"])


async def _save_batch_file(directory: Path, upload: UploadFile) -> Path | None:
    if not upload.filename:
        return None
    content = await upload.read()
    if not content:
        return None
    target = directory / Path(upload.filename).name
    target.write_bytes(content)
    return target.resolve()


@router.post("/youtube", response_model=JobCreatedResponse)
async def publish_youtube(
    files: list[UploadFile] = File(default=[]),  # noqa: B008
    client_secrets: UploadFile | None = File(default=None),  # noqa: B008
    schedule_time: str = Form("07:00"),
    timezone: str = Form("America/Campo_Grande"),
    made_for_kids: bool = Form(False),
) -> JobCreatedResponse:
    if ProgressHub.get().is_running():
        raise HTTPException(status_code=409, detail="Já existe um processamento em andamento.")
    if not client_secrets or not client_secrets.filename:
        raise HTTPException(status_code=400, detail="Envie a credencial OAuth JSON.")

    batch_dir = TEMP_DIR / f"web_youtube_{uuid.uuid4().hex}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for upload in files:
        path = await _save_batch_file(batch_dir, upload)
        if path:
            saved.append(str(path))
    secrets_path = await _save_batch_file(batch_dir, client_secrets)
    if secrets_path is None:
        raise HTTPException(status_code=400, detail="A credencial OAuth está vazia.")

    try:
        selected = select_upload_batch(saved)
        valid_secrets = validate_client_secrets_file(secrets_path)
        dates = build_daily_publish_times(timezone_name=timezone, time_text=schedule_time, count=5)
    except (ValueError, YouTubeUploadError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = {
        "job_type": "youtube",
        "credentials_path": str(valid_secrets),
        "token_path": str((Path.cwd() / "token.json").resolve()),
        "made_for_kids": made_for_kids,
        "selected": [
            {
                "video_path": str(item.video_path),
                "caption_path": str(item.caption_path),
                "publish_at": youtube_publish_at(date),
            }
            for item, date in zip(selected, dates, strict=True)
        ],
    }
    job_id = f"youtube-{uuid.uuid4().hex}"
    queued_id = await asyncio.to_thread(enqueue_studio_job, job_id, payload)
    provider = "Redis/RQ" if redis_available() else "thread local"
    return JobCreatedResponse(
        message=f"Lote do YouTube enfileirado ({provider}).",
        job_id=queued_id,
    )


_WORKFLOW_STORE: YouTubeWorkflowStore | None = None


def _workflow_store() -> YouTubeWorkflowStore:
    global _WORKFLOW_STORE
    if _WORKFLOW_STORE is None:
        _WORKFLOW_STORE = YouTubeWorkflowStore()
    return _WORKFLOW_STORE


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise HTTPException(status_code=403, detail="Esta rota aceita somente conexões locais.")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Esta rota aceita somente conexões locais.") from exc


def _existing_youtube_client():
    token_path = (Path.cwd() / "token.json").resolve()
    try:
        credentials = get_existing_youtube_credentials(token_path)
        return build_youtube_client(credentials)
    except YouTubeUploadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _stage_mp4(upload: UploadFile, store: YouTubeWorkflowStore, stage: str) -> tuple[Path, str, Path]:
    if not upload.filename or Path(upload.filename).suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail=f"Envie um arquivo MP4 para a etapa {stage}.")
    request_dir = store.upload_dir / f"request_{uuid.uuid4().hex}"
    request_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    target = request_dir / f"{stage}.mp4"
    digest = hashlib.sha256()
    total = 0
    try:
        with target.open("xb") as output:
            while chunk := await upload.read(8 * 1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        target.chmod(0o600)
        if total == 0:
            shutil.rmtree(request_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=f"O arquivo MP4 da etapa {stage} está vazio.")
        return target.resolve(), digest.hexdigest(), request_dir
    except HTTPException:
        raise
    except Exception:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise


def _validate_metadata(title: str, description: str) -> tuple[str, str]:
    from app.publishing.youtube_uploader import validate_explicit_video_metadata

    try:
        return validate_explicit_video_metadata(title, description)
    except YouTubeUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _map_workflow_error(exc: Exception) -> HTTPException:
    if isinstance(exc, YouTubeUploadError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, YouTubeWorkflowNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, YouTubeIdempotencyConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, YouTubeWorkflowConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, YouTubeWorkflowError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="Falha no fluxo local de publicação.")


class ConfirmRequest(BaseModel):
    confirm: bool = False


class ReconcileRequest(BaseModel):
    stage: str
    video_id: str | None = None
    confirm_absent_in_studio: bool = False
    confirm_no_active_upload: bool = False


class RelatedVideoConfirmRequest(BaseModel):
    short_video_id: str
    related_video_id: str
    verified_in_studio: bool = False


@router.get("/youtube/channels")
async def list_youtube_channels(request: Request) -> dict:
    """Lista canais do token já existente; não inicia OAuth nem amplia escopos."""
    _require_loopback(request)
    client = _existing_youtube_client()
    try:
        response = client.channels().list(part="id,snippet", mine=True).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                "O token existente não permite consultar os canais. Nenhum consentimento foi iniciado; "
                "a publicação exige que o canal seja confirmado."
            ),
        ) from exc
    channels = [
        {"channel_id": str(item.get("id") or ""), "title": str((item.get("snippet") or {}).get("title") or "")}
        for item in (response or {}).get("items", [])
        if item.get("id")
    ]
    return {"channels": channels}


@router.post("/youtube/individual", status_code=201)
async def prepare_youtube_individual(
    request: Request,
    video: UploadFile = File(...),  # noqa: B008
    target_channel_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    privacy_status: str = Form("public"),
    contains_synthetic_media: bool = Form(...),
    made_for_kids: bool = Form(False),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict:
    _require_loopback(request)
    store = _workflow_store()
    clean_title, clean_description = _validate_metadata(title, description)
    staged_path, digest, request_dir = await _stage_mp4(video, store, "video")
    item = {
        "stage": "video",
        "path": str(staged_path),
        "sha256": digest,
        "title": clean_title,
        "description": clean_description,
        "privacy_status": privacy_status.strip().lower(),
        "contains_synthetic_media": contains_synthetic_media,
        "made_for_kids": made_for_kids,
    }
    fingerprint = workflow_fingerprint(
        mode="individual", target_channel_id=target_channel_id, items=[item]
    )
    try:
        workflow, created = store.create(
            idempotency_key=idempotency_key,
            mode="individual",
            target_channel_id=target_channel_id,
            items=[item],
            fingerprint=fingerprint,
        )
    except Exception as exc:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise _map_workflow_error(exc) from exc
    if not created:
        shutil.rmtree(request_dir, ignore_errors=True)
    return {"created": created, "workflow": store.public_view(workflow)}


@router.post("/youtube/sequence", status_code=201)
async def prepare_youtube_sequence(
    request: Request,
    long_video: UploadFile = File(...),  # noqa: B008
    short_video: UploadFile = File(...),  # noqa: B008
    target_channel_id: str = Form(...),
    long_title: str = Form(...),
    long_description: str = Form(...),
    long_privacy_status: str = Form("public"),
    long_contains_synthetic_media: bool = Form(...),
    long_made_for_kids: bool = Form(False),
    short_title: str = Form(...),
    short_description: str = Form(...),
    short_privacy_status: str = Form("public"),
    short_contains_synthetic_media: bool = Form(...),
    short_made_for_kids: bool = Form(False),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict:
    _require_loopback(request)
    store = _workflow_store()
    long_title_clean, long_description_clean = _validate_metadata(long_title, long_description)
    short_title_clean, short_description_clean = _validate_metadata(short_title, short_description)
    try:
        validate_short_description_before_relation(short_description_clean)
    except YouTubeWorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    long_path, long_hash, long_dir = await _stage_mp4(long_video, store, "long")
    try:
        short_path, short_hash, short_dir = await _stage_mp4(short_video, store, "short")
    except Exception:
        shutil.rmtree(long_dir, ignore_errors=True)
        raise
    items = [
        {
            "stage": "long", "path": str(long_path), "sha256": long_hash,
            "title": long_title_clean, "description": long_description_clean,
            "privacy_status": long_privacy_status.strip().lower(),
            "contains_synthetic_media": long_contains_synthetic_media,
            "made_for_kids": long_made_for_kids,
        },
        {
            "stage": "short", "path": str(short_path), "sha256": short_hash,
            "title": short_title_clean, "description": short_description_clean,
            "privacy_status": short_privacy_status.strip().lower(),
            "contains_synthetic_media": short_contains_synthetic_media,
            "made_for_kids": short_made_for_kids,
        },
    ]
    fingerprint = workflow_fingerprint(
        mode="sequence", target_channel_id=target_channel_id, items=items
    )
    try:
        workflow, created = store.create(
            idempotency_key=idempotency_key, mode="sequence",
            target_channel_id=target_channel_id, items=items, fingerprint=fingerprint,
        )
    except Exception as exc:
        shutil.rmtree(long_dir, ignore_errors=True)
        shutil.rmtree(short_dir, ignore_errors=True)
        raise _map_workflow_error(exc) from exc
    if not created:
        shutil.rmtree(long_dir, ignore_errors=True)
        shutil.rmtree(short_dir, ignore_errors=True)
    return {"created": created, "workflow": store.public_view(workflow)}


@router.get("/youtube/workflows/by-idempotency-key/{key}")
async def get_youtube_workflow_by_key(request: Request, key: str) -> dict:
    _require_loopback(request)
    store = _workflow_store()
    try:
        return {"workflow": store.public_view(store.get_by_idempotency_key(key))}
    except Exception as exc:
        raise _map_workflow_error(exc) from exc


@router.get("/youtube/workflows/{workflow_id}")
async def get_youtube_workflow(request: Request, workflow_id: str) -> dict:
    _require_loopback(request)
    store = _workflow_store()
    try:
        return {"workflow": store.public_view(store.get(workflow_id))}
    except Exception as exc:
        raise _map_workflow_error(exc) from exc


async def _enqueue_workflow(workflow_id: str, store: YouTubeWorkflowStore, *, resume: bool) -> dict:
    from app.web.hub import ProgressHub

    hub = ProgressHub.get()
    if hub.is_running():
        raise HTTPException(status_code=409, detail="Já existe um processamento em andamento.")
    job_id = f"youtube-workflow-{workflow_id}-{uuid.uuid4().hex[:10]}"
    try:
        should_enqueue, workflow = (
            store.mark_resumed(workflow_id, job_id)
            if resume
            else store.mark_queued(workflow_id, job_id)
        )
    except Exception as exc:
        raise _map_workflow_error(exc) from exc
    if not should_enqueue:
        return {"enqueued": False, "workflow": store.public_view(workflow)}
    payload = {"job_type": "youtube_workflow", "workflow_id": workflow_id}
    try:
        queue_job_id = await asyncio.to_thread(enqueue_studio_job, job_id, payload)
    except Exception as exc:
        store.mark_blocked(workflow_id, "queue_failed", "A fila local não aceitou o job.")
        raise HTTPException(status_code=503, detail="A fila local não aceitou o job.") from exc
    workflow = store.set_job_id(workflow_id, str(queue_job_id))
    return {"enqueued": True, "job_id": str(queue_job_id), "workflow": store.public_view(workflow)}


@router.post("/youtube/workflows/{workflow_id}/publish")
async def publish_youtube_workflow(
    request: Request, workflow_id: str, confirmation: ConfirmRequest = Body(...),  # noqa: B008
) -> dict:
    _require_loopback(request)
    if not confirmation.confirm:
        raise HTTPException(status_code=400, detail="Envie confirm=true para enfileirar os uploads.")
    return await _enqueue_workflow(workflow_id, _workflow_store(), resume=False)


@router.post("/youtube/workflows/{workflow_id}/resume")
async def resume_youtube_workflow(
    request: Request, workflow_id: str, confirmation: ConfirmRequest = Body(...),  # noqa: B008
) -> dict:
    _require_loopback(request)
    if not confirmation.confirm:
        raise HTTPException(status_code=400, detail="Envie confirm=true para retomar as etapas pendentes.")
    return await _enqueue_workflow(workflow_id, _workflow_store(), resume=True)


@router.post("/youtube/workflows/{workflow_id}/reconcile")
async def reconcile_youtube_workflow(
    request: Request, workflow_id: str, reconciliation: ReconcileRequest = Body(...),  # noqa: B008
) -> dict:
    _require_loopback(request)
    store = _workflow_store()
    try:
        if reconciliation.video_id:
            client = _existing_youtube_client()
            workflow = store.get(workflow_id)
            verify_youtube_channel(client, workflow["payload"]["target_channel_id"])
            result = reconcile_youtube_workflow_upload(
                workflow_id, reconciliation.stage, reconciliation.video_id,
                store=store, youtube_client=client,
            )
        elif reconciliation.confirm_absent_in_studio:
            result = store.confirm_stage_absent_in_studio(
                workflow_id,
                reconciliation.stage,
                no_active_upload_confirmed=reconciliation.confirm_no_active_upload,
            )
            result = store.public_view(result)
        else:
            raise HTTPException(
                status_code=400,
                detail="Informe video_id para conferência pela API ou confirme a ausência no Studio.",
            )
        return {"workflow": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_workflow_error(exc) from exc


@router.post("/youtube/workflows/{workflow_id}/related-video/confirm")
async def confirm_youtube_related_video(
    request: Request, workflow_id: str, confirmation: RelatedVideoConfirmRequest = Body(...),  # noqa: B008
) -> dict:
    _require_loopback(request)
    store = _workflow_store()
    try:
        workflow = store.confirm_related_video(
            workflow_id, short_video_id=confirmation.short_video_id,
            related_video_id=confirmation.related_video_id,
            verified_in_studio=confirmation.verified_in_studio,
        )
        return {"workflow": store.public_view(workflow)}
    except Exception as exc:
        raise _map_workflow_error(exc) from exc
