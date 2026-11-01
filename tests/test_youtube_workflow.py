from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.publishing.youtube_uploader import YouTubeUploadResult
from app.publishing.youtube_workflow import (
    YouTubeIdempotencyConflict,
    YouTubeWorkflowConflict,
    YouTubeWorkflowError,
    YouTubeWorkflowStore,
    execute_youtube_workflow,
    workflow_fingerprint,
)


class _Request:
    def __init__(self, body):
        self.body = body

    def execute(self):
        return self.body


class _Channels:
    def __init__(self, channel_id="channel-1"):
        self.channel_id = channel_id

    def list(self, **_kwargs):
        return _Request(
            {"items": [{"id": self.channel_id, "snippet": {"title": "Canal de oração"}}]}
        )


class _YouTube:
    def __init__(self, channel_id="channel-1"):
        self._channels = _Channels(channel_id)

    def channels(self):
        return self._channels


def _prepared_sequence(tmp_path: Path, store: YouTubeWorkflowStore, key="sequence-1"):
    items = []
    for stage in ("long", "short"):
        video = tmp_path / f"{stage}.mp4"
        video.write_bytes(stage.encode())
        items.append(
            {
                "stage": stage,
                "path": str(video),
                "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
                "title": f"Oração da noite · {stage}",
                "description": f"Reflexão e oração da noite ({stage}).",
                "privacy_status": "public",
                "contains_synthetic_media": True,
                "made_for_kids": False,
            }
        )
    fingerprint = workflow_fingerprint(
        mode="sequence", target_channel_id="channel-1", items=items
    )
    workflow, created = store.create(
        idempotency_key=key,
        mode="sequence",
        target_channel_id="channel-1",
        items=items,
        fingerprint=fingerprint,
    )
    assert created
    return workflow, items, fingerprint


def test_sequence_uploads_long_before_short_and_waits_for_studio(tmp_path: Path):
    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    workflow, _items, _fingerprint = _prepared_sequence(tmp_path, store)
    store.mark_queued(workflow["workflow_id"], "job-1")
    calls = []

    def upload(path, caption, **kwargs):
        stage = Path(path).stem
        calls.append(stage)
        assert caption is None
        assert kwargs["contains_synthetic_media"] is True
        return YouTubeUploadResult(
            video_id=f"id-{stage}",
            url=f"https://www.youtube.com/watch?v=id-{stage}",
            title=kwargs["title"],
            privacy_status=kwargs["privacy_status"],
            channel_id="channel-1",
        )

    result = execute_youtube_workflow(
        workflow["workflow_id"], store=store, youtube_client=_YouTube(), upload_function=upload
    )

    assert calls == ["long", "short"]
    assert result["status"] == "awaiting_studio"
    assert result["related_video"]["status"] == "pending_studio"
    assert [video["video_id"] for video in result["videos"]] == ["id-long", "id-short"]


def test_wrong_authenticated_channel_blocks_before_any_upload(tmp_path: Path):
    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    workflow, _items, _fingerprint = _prepared_sequence(tmp_path, store)
    store.mark_queued(workflow["workflow_id"], "job-1")
    calls = []

    def upload(*_args, **_kwargs):
        calls.append("insert")
        raise AssertionError("upload must not start")

    with pytest.raises(YouTubeWorkflowError, match="envio foi bloqueado"):
        execute_youtube_workflow(
            workflow["workflow_id"],
            store=store,
            youtube_client=_YouTube("different-channel"),
            upload_function=upload,
        )

    saved = store.get(workflow["workflow_id"])
    assert saved["state"]["status"] == "blocked_channel_verification"
    assert calls == []


def test_upload_error_is_uncertain_and_resume_is_blocked(tmp_path: Path):
    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    workflow, _items, _fingerprint = _prepared_sequence(tmp_path, store)
    store.mark_queued(workflow["workflow_id"], "job-1")

    def upload(*_args, **_kwargs):
        raise TimeoutError("connection interrupted")

    with pytest.raises(Exception, match="resultado do upload de long é incerto"):
        execute_youtube_workflow(
            workflow["workflow_id"], store=store, youtube_client=_YouTube(), upload_function=upload
        )

    saved = store.get(workflow["workflow_id"])
    assert saved["state"]["status"] == "upload_uncertain"
    assert saved["state"]["stage_states"]["long"]["status"] == "uncertain"
    with pytest.raises(YouTubeWorkflowConflict, match="sem resultado confirmado"):
        store.mark_resumed(workflow["workflow_id"], "job-2")


def test_idempotency_key_returns_same_workflow_and_rejects_changed_input(tmp_path: Path):
    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    workflow, items, fingerprint = _prepared_sequence(tmp_path, store, key="same-key")
    again, created = store.create(
        idempotency_key="same-key",
        mode="sequence",
        target_channel_id="channel-1",
        items=items,
        fingerprint=fingerprint,
    )
    assert created is False
    assert again["workflow_id"] == workflow["workflow_id"]

    with pytest.raises(YouTubeIdempotencyConflict):
        store.create(
            idempotency_key="same-key",
            mode="sequence",
            target_channel_id="channel-1",
            items=items,
            fingerprint="different-fingerprint",
        )


def test_related_video_requires_explicit_studio_confirmation(tmp_path: Path):
    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    workflow, _items, _fingerprint = _prepared_sequence(tmp_path, store)
    store.mark_queued(workflow["workflow_id"], "job-1")

    def upload(path, _caption, **kwargs):
        stage = Path(path).stem
        return YouTubeUploadResult(
            video_id=f"id-{stage}",
            url=f"https://www.youtube.com/watch?v=id-{stage}",
            title=kwargs["title"],
            privacy_status="public",
            channel_id="channel-1",
        )

    execute_youtube_workflow(
        workflow["workflow_id"], store=store, youtube_client=_YouTube(), upload_function=upload
    )
    with pytest.raises(Exception, match="Confirme somente"):
        store.confirm_related_video(
            workflow["workflow_id"],
            short_video_id="id-short",
            related_video_id="id-long",
            verified_in_studio=False,
        )

    completed = store.confirm_related_video(
        workflow["workflow_id"],
        short_video_id="id-short",
        related_video_id="id-long",
        verified_in_studio=True,
    )
    assert store.public_view(completed)["related_video"]["status"] == "verified_in_studio"


def test_visibility_mismatch_stops_before_short_and_cannot_resume(tmp_path: Path):
    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    workflow, _items, _fingerprint = _prepared_sequence(tmp_path, store)
    store.mark_queued(workflow["workflow_id"], "job-1")
    calls = []

    def upload(path, _caption, **kwargs):
        stage = Path(path).stem
        calls.append(stage)
        return YouTubeUploadResult(
            video_id=f"id-{stage}",
            url=f"https://www.youtube.com/watch?v=id-{stage}",
            title=kwargs["title"],
            privacy_status="private" if stage == "long" else "public",
            channel_id="channel-1",
        )

    with pytest.raises(YouTubeWorkflowConflict, match="visibilidade retornada difere"):
        execute_youtube_workflow(
            workflow["workflow_id"], store=store, youtube_client=_YouTube(), upload_function=upload
        )

    assert calls == ["long"]
    with pytest.raises(YouTubeWorkflowConflict, match="bloqueado por uma divergência"):
        store.mark_resumed(workflow["workflow_id"], "job-2")


def test_changed_staged_file_is_blocked_before_channel_check_or_upload(tmp_path: Path):
    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    workflow, items, _fingerprint = _prepared_sequence(tmp_path, store)
    Path(items[0]["path"]).write_bytes(b"changed")
    store.mark_queued(workflow["workflow_id"], "job-1")
    channel_calls = []

    class TrackingYouTube(_YouTube):
        def channels(self):
            channel_calls.append("list")
            return super().channels()

    with pytest.raises(YouTubeWorkflowError, match="mudou após a validação"):
        execute_youtube_workflow(
            workflow["workflow_id"],
            store=store,
            youtube_client=TrackingYouTube(),
            upload_function=lambda *_args, **_kwargs: pytest.fail("upload must not start"),
        )

    assert channel_calls == []
    assert store.get(workflow["workflow_id"])["state"]["status"] == "staged_file_invalid"


def test_local_individual_route_stages_mp4_idempotently(tmp_path: Path, monkeypatch):
    import asyncio
    import tempfile

    from starlette.datastructures import UploadFile
    from starlette.requests import Request

    from app.web.routers import publish as publish_router

    store = YouTubeWorkflowStore(tmp_path / "state.sqlite", tmp_path / "uploads")
    monkeypatch.setattr(publish_router, "_WORKFLOW_STORE", store)
    request = Request(
        {
            "type": "http", "method": "POST", "path": "/api/publish/youtube/individual",
            "headers": [], "query_string": b"", "server": ("127.0.0.1", 8765),
            "client": ("127.0.0.1", 6543), "scheme": "http", "http_version": "1.1",
        }
    )
    form = {
        "target_channel_id": "channel-1",
        "title": "Oração da noite",
        "description": "Uma oração para dormir em paz.",
        "privacy_status": "public",
        "contains_synthetic_media": True,
        "made_for_kids": False,
        "idempotency_key": "local-test-key",
    }

    def upload_file():
        file_obj = tempfile.SpooledTemporaryFile()
        file_obj.write(b"mock-mp4")
        file_obj.seek(0)
        return UploadFile(filename="noite.mp4", file=file_obj)

    async def prepare():
        first = await publish_router.prepare_youtube_individual(
            request=request, video=upload_file(), **form
        )
        duplicate = await publish_router.prepare_youtube_individual(
            request=request, video=upload_file(), **form
        )
        return first, duplicate

    first, duplicate = asyncio.run(prepare())
    workflow_id = first["workflow"]["workflow_id"]
    assert first["created"] is True
    assert first["workflow"]["status"] == "prepared"
    assert first["workflow"]["videos"][0]["contains_synthetic_media"] is True
    assert duplicate["created"] is False
    assert duplicate["workflow"]["workflow_id"] == workflow_id
