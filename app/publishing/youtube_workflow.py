"""Fluxo durável e idempotente de publicação individual/encadeada no YouTube."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import OUTPUT_DIR
from app.publishing.youtube_uploader import (
    YouTubeUploadResult,
    build_youtube_client,
    get_existing_youtube_credentials,
    upload_video_to_youtube,
    verify_existing_youtube_video,
    verify_youtube_channel,
)

_DATA_DIR = OUTPUT_DIR.parent / "data"
_DEFAULT_WORKFLOW_DIR = _DATA_DIR / "youtube_workflow"
_DEFAULT_DB = _DEFAULT_WORKFLOW_DIR / "state.sqlite"
_DEFAULT_UPLOAD_DIR = _DEFAULT_WORKFLOW_DIR / "uploads"
_VALID_MODES = {"individual", "sequence"}
_VALID_PRIVACY = {"private", "unlisted", "public"}
_ACTIVE_UPLOAD_STATES = {"uploading", "uncertain"}


class YouTubeWorkflowError(RuntimeError):
    """Erro de estado ou validação do fluxo, sem dados de credencial."""


class YouTubeWorkflowConflict(YouTubeWorkflowError):
    """A operação já avançou ou exige reconciliação manual."""


class YouTubeIdempotencyConflict(YouTubeWorkflowConflict):
    """Uma chave idempotente foi reutilizada com outro conteúdo."""


class YouTubeWorkflowNotFound(YouTubeWorkflowError):
    """O identificador do fluxo não existe."""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def workflow_fingerprint(
    *,
    mode: str,
    target_channel_id: str,
    items: list[dict[str, Any]],
) -> str:
    canonical_items = [
        {
            "stage": str(item["stage"]),
            "sha256": str(item["sha256"]),
            "title": str(item["title"]),
            "description": str(item["description"]),
            "privacy_status": str(item["privacy_status"]),
            "contains_synthetic_media": bool(item["contains_synthetic_media"]),
            "made_for_kids": bool(item.get("made_for_kids", False)),
        }
        for item in items
    ]
    canonical = {
        "mode": mode,
        "target_channel_id": target_channel_id,
        "items": canonical_items,
    }
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class YouTubeWorkflowStore:
    """SQLite durável para metadados, estados e IDs retornados pelo YouTube."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        upload_dir: str | Path | None = None,
    ) -> None:
        self.db_path = Path(db_path or _DEFAULT_DB).expanduser().resolve()
        self.upload_dir = Path(upload_dir or _DEFAULT_UPLOAD_DIR).expanduser().resolve()
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._init_db()
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS youtube_workflows (
                    workflow_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_youtube_workflows_updated "
                "ON youtube_workflows(updated_at)"
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "workflow_id": str(row["workflow_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "fingerprint": str(row["fingerprint"]),
            "payload": json.loads(row["payload_json"]),
            "state": json.loads(row["state_json"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _read_one(self, *, workflow_id: str | None = None, idempotency_key: str | None = None):
        if (workflow_id is None) == (idempotency_key is None):
            raise ValueError("Informe exatamente um identificador.")
        column = "workflow_id" if workflow_id is not None else "idempotency_key"
        value = workflow_id if workflow_id is not None else idempotency_key
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM youtube_workflows WHERE {column} = ?", (value,)
            ).fetchone()
        return self._decode(row) if row else None

    def get(self, workflow_id: str) -> dict[str, Any]:
        row = self._read_one(workflow_id=workflow_id)
        if row is None:
            raise YouTubeWorkflowNotFound("Fluxo YouTube não encontrado.")
        return row

    def get_by_idempotency_key(self, key: str) -> dict[str, Any]:
        row = self._read_one(idempotency_key=key)
        if row is None:
            raise YouTubeWorkflowNotFound("Fluxo YouTube não encontrado para essa chave.")
        return row

    def public_view(self, workflow: dict[str, Any]) -> dict[str, Any]:
        payload = workflow["payload"]
        state = workflow["state"]
        videos = []
        stage_states = state["stage_states"]
        for item in payload["items"]:
            stage_state = stage_states[item["stage"]]
            videos.append(
                {
                    "stage": item["stage"],
                    "title": item["title"],
                    "description": item["description"],
                    "privacy_requested": item["privacy_status"],
                    "contains_synthetic_media": item["contains_synthetic_media"],
                    "made_for_kids": item.get("made_for_kids", False),
                    "status": stage_state["status"],
                    "video_id": stage_state.get("video_id"),
                    "url": stage_state.get("url"),
                    "privacy_actual": stage_state.get("privacy_actual"),
                    "channel_id_actual": stage_state.get("channel_id_actual"),
                    "verified": bool(stage_state.get("verified", False)),
                    "error": stage_state.get("error"),
                }
            )
        return {
            "workflow_id": workflow["workflow_id"],
            "idempotency_key": workflow["idempotency_key"],
            "mode": payload["mode"],
            "status": state["status"],
            "target_channel_id": payload["target_channel_id"],
            "target_channel_title": state.get("target_channel_title"),
            "job_id": state.get("job_id"),
            "videos": videos,
            "related_video": state.get("related_video"),
            "error": state.get("error"),
            "history": list(state.get("history") or []),
            "created_at": workflow["created_at"],
            "updated_at": workflow["updated_at"],
        }

    def create(
        self,
        *,
        idempotency_key: str,
        mode: str,
        target_channel_id: str,
        items: list[dict[str, Any]],
        fingerprint: str,
    ) -> tuple[dict[str, Any], bool]:
        key = idempotency_key.strip()
        channel_id = target_channel_id.strip()
        if not key or len(key) > 128:
            raise YouTubeWorkflowError("Idempotency-Key precisa ter de 1 a 128 caracteres.")
        if not channel_id or len(channel_id) > 128:
            raise YouTubeWorkflowError("Informe o ID exato do canal de destino.")
        if mode not in _VALID_MODES:
            raise YouTubeWorkflowError("Modo precisa ser individual ou sequence.")
        expected_stages = ["video"] if mode == "individual" else ["long", "short"]
        if [item.get("stage") for item in items] != expected_stages:
            raise YouTubeWorkflowError("A ordem dos vídeos no fluxo está inválida.")
        if mode == "sequence" and items[0].get("privacy_status") not in {"public", "unlisted"}:
            raise YouTubeWorkflowError(
                "O vídeo longo precisa ser público ou não listado para poder ser relacionado ao Short."
            )
        for item in items:
            if item.get("privacy_status") not in _VALID_PRIVACY:
                raise YouTubeWorkflowError("Visibilidade inválida.")
            if not item.get("title") or not item.get("description"):
                raise YouTubeWorkflowError("Título e descrição são obrigatórios.")
            if not isinstance(item.get("contains_synthetic_media"), bool):
                raise YouTubeWorkflowError("Informe a declaração de mídia sintética para cada vídeo.")
            path = Path(item["path"]).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() != ".mp4":
                raise YouTubeWorkflowError("O arquivo MP4 preparado não está disponível.")
            item["path"] = str(path)

        workflow_id = uuid.uuid4().hex
        created = utc_now()
        payload = {
            "mode": mode,
            "target_channel_id": channel_id,
            "items": items,
        }
        state = {
            "status": "prepared",
            "job_id": None,
            "target_channel_title": None,
            "stage_states": {
                stage: {
                    "status": "pending",
                    "video_id": None,
                    "url": None,
                    "privacy_actual": None,
                    "channel_id_actual": None,
                    "verified": False,
                    "error": None,
                }
                for stage in expected_stages
            },
            "related_video": {
                "status": "not_ready" if mode == "sequence" else "not_applicable",
                "short_video_id": None,
                "related_video_id": None,
                "verified_at": None,
                "verification_method": None,
                "note": (
                    "Depois dos uploads, selecione o longo em YouTube Studio > Conteúdo > Short > "
                    "Vídeo relacionado e confirme visualmente."
                    if mode == "sequence"
                    else None
                ),
            },
            "error": None,
            "history": [{"at": created, "event": "prepared"}],
        }

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM youtube_workflows WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing:
                workflow = self._decode(existing)
                conn.rollback()
                if workflow["fingerprint"] != fingerprint:
                    raise YouTubeIdempotencyConflict(
                        "A mesma Idempotency-Key foi usada com arquivos ou metadados diferentes."
                    )
                return workflow, False
            conn.execute(
                """
                INSERT INTO youtube_workflows (
                    workflow_id, idempotency_key, fingerprint, payload_json,
                    state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    key,
                    fingerprint,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(state, ensure_ascii=False),
                    created,
                    created,
                ),
            )
            conn.commit()
        return self.get(workflow_id), True

    def _mutate(self, workflow_id: str, mutator: Callable[[dict[str, Any], dict[str, Any]], Any]):
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM youtube_workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise YouTubeWorkflowNotFound("Fluxo YouTube não encontrado.")
            payload = json.loads(row["payload_json"])
            state = json.loads(row["state_json"])
            result = mutator(payload, state)
            updated = utc_now()
            state["history"] = list(state.get("history") or [])
            state["history"].append({"at": updated, "event": "state_updated"})
            conn.execute(
                """
                UPDATE youtube_workflows
                SET payload_json = ?, state_json = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(state, ensure_ascii=False),
                    updated,
                    workflow_id,
                ),
            )
            conn.commit()
        return result, self.get(workflow_id)

    @staticmethod
    def _event(state: dict[str, Any], event: str, detail: str | None = None) -> None:
        entry: dict[str, Any] = {"at": utc_now(), "event": event}
        if detail:
            entry["detail"] = detail
        state.setdefault("history", []).append(entry)

    def mark_queued(self, workflow_id: str, job_id: str) -> tuple[bool, dict[str, Any]]:
        def mutate(_payload, state):
            if state["status"] in {"queued", "running", "upload_uncertain", "awaiting_studio", "complete"}:
                return False
            if any(s["status"] in _ACTIVE_UPLOAD_STATES for s in state["stage_states"].values()):
                raise YouTubeWorkflowConflict(
                    "Há um upload em andamento ou incerto; reconcilie antes de tentar novamente."
                )
            if state["status"] != "prepared":
                raise YouTubeWorkflowConflict(
                    "Este fluxo não está pronto para um primeiro envio; use a recuperação segura."
                )
            state["status"] = "queued"
            state["job_id"] = job_id
            state["error"] = None
            self._event(state, "queued")
            return True

        return self._mutate(workflow_id, mutate)

    def mark_resumed(self, workflow_id: str, job_id: str) -> tuple[bool, dict[str, Any]]:
        def mutate(_payload, state):
            if state["status"] in {"awaiting_studio", "complete"}:
                return False
            if any(s["status"] in _ACTIVE_UPLOAD_STATES for s in state["stage_states"].values()):
                raise YouTubeWorkflowConflict(
                    "Existe uma etapa sem resultado confirmado. Reconcilie-a antes de retomar."
                )
            resumable_statuses = {
                "prepared", "partial", "running", "blocked_auth",
                "blocked_channel_verification", "queue_failed",
            }
            if state["status"] not in resumable_statuses:
                raise YouTubeWorkflowConflict(
                    "Este fluxo está bloqueado por uma divergência de canal ou visibilidade."
                )
            if not any(s["status"] == "pending" for s in state["stage_states"].values()):
                return False
            state["status"] = "queued"
            state["job_id"] = job_id
            state["error"] = None
            self._event(state, "resume_queued")
            return True

        return self._mutate(workflow_id, mutate)

    def set_job_id(self, workflow_id: str, job_id: str) -> dict[str, Any]:
        def mutate(_payload, state):
            state["job_id"] = job_id
            self._event(state, "queue_id_recorded")

        return self._mutate(workflow_id, mutate)[1]

    def mark_running(self, workflow_id: str) -> dict[str, Any]:
        def mutate(_payload, state):
            if state["status"] == "queued":
                state["status"] = "running"
                self._event(state, "running")

        return self._mutate(workflow_id, mutate)[1]

    def mark_channel_verified(self, workflow_id: str, channel_title: str) -> dict[str, Any]:
        def mutate(_payload, state):
            state["target_channel_title"] = channel_title
            state["error"] = None
            self._event(state, "target_channel_verified")

        return self._mutate(workflow_id, mutate)[1]

    def mark_blocked(self, workflow_id: str, status: str, message: str) -> dict[str, Any]:
        def mutate(_payload, state):
            state["status"] = status
            state["error"] = message
            self._event(state, status, message)

        return self._mutate(workflow_id, mutate)[1]

    def mark_stage_started(self, workflow_id: str, stage: str) -> bool:
        def mutate(_payload, state):
            stage_state = state["stage_states"].get(stage)
            if stage_state is None:
                raise YouTubeWorkflowError("Etapa de upload inválida.")
            if stage_state["status"] == "uploaded":
                return False
            if stage_state["status"] != "pending":
                raise YouTubeWorkflowConflict(
                    "O resultado da etapa não está confirmado; nenhum novo upload será iniciado."
                )
            stage_state["status"] = "uploading"
            stage_state["error"] = None
            state["status"] = "running"
            self._event(state, f"{stage}_upload_started")
            return True

        return self._mutate(workflow_id, mutate)[0]

    def mark_stage_uncertain(
        self, workflow_id: str, stage: str, error_type: str
    ) -> dict[str, Any]:
        message = (
            "O envio começou, mas o resultado não foi confirmado. Verifique o canal no Studio "
            "e reconcilie esta etapa antes de qualquer nova tentativa."
        )

        def mutate(_payload, state):
            stage_state = state["stage_states"][stage]
            if stage_state["status"] == "uploading":
                stage_state["status"] = "uncertain"
                stage_state["error"] = error_type
                state["status"] = "upload_uncertain"
                state["error"] = message
                self._event(state, f"{stage}_upload_uncertain", error_type)

        return self._mutate(workflow_id, mutate)[1]

    def record_stage_uploaded(
        self,
        workflow_id: str,
        stage: str,
        result: YouTubeUploadResult,
    ) -> dict[str, Any]:
        def mutate(payload, state):
            stage_state = state["stage_states"][stage]
            if stage_state["status"] not in {"uploading", "uncertain"}:
                if stage_state.get("video_id") == result.video_id:
                    return
                raise YouTubeWorkflowConflict("A etapa não está aguardando confirmação de upload.")
            item = next(item for item in payload["items"] if item["stage"] == stage)
            stage_state.update(
                {
                    "status": "uploaded",
                    "video_id": result.video_id,
                    "url": result.url,
                    "privacy_actual": result.privacy_status,
                    "channel_id_actual": result.channel_id,
                    "verified": bool(result.channel_id and result.privacy_status != "unknown"),
                    "error": None,
                }
            )
            if result.channel_id and result.channel_id != payload["target_channel_id"]:
                state["status"] = "post_upload_channel_mismatch"
                state["error"] = "O YouTube informou um canal diferente após o envio; a próxima etapa foi bloqueada."
            elif result.privacy_status != item["privacy_status"]:
                state["status"] = "visibility_mismatch"
                state["error"] = "A visibilidade retornada pelo YouTube difere da solicitada; a próxima etapa foi bloqueada."
            elif not stage_state["verified"]:
                state["status"] = "post_upload_verification_required"
                state["error"] = "O envio retornou um ID, mas canal/visibilidade precisam ser confirmados pela API."
            else:
                all_uploaded = all(
                    value["status"] == "uploaded" and value["verified"]
                    for value in state["stage_states"].values()
                )
                if all_uploaded:
                    if payload["mode"] == "sequence":
                        state["status"] = "awaiting_studio"
                        state["related_video"]["status"] = "pending_studio"
                    else:
                        state["status"] = "complete"
                    state["error"] = None
                else:
                    state["status"] = "running"
                    state["error"] = None
            self._event(state, f"{stage}_uploaded", result.video_id)

        return self._mutate(workflow_id, mutate)[1]

    def record_reconciled_upload(
        self,
        workflow_id: str,
        stage: str,
        video: dict[str, Any],
    ) -> dict[str, Any]:
        def mutate(payload, state):
            stage_state = state["stage_states"].get(stage)
            if stage_state is None:
                raise YouTubeWorkflowError("Etapa de upload inválida.")
            existing_id = stage_state.get("video_id")
            if existing_id and existing_id != video["video_id"]:
                raise YouTubeWorkflowConflict(
                    "O ID informado difere do ID já persistido; nada foi sobrescrito."
                )
            item = next(item for item in payload["items"] if item["stage"] == stage)
            stage_state.update(
                {
                    "status": "uploaded",
                    "video_id": video["video_id"],
                    "url": video["url"],
                    "privacy_actual": video["privacy_status"],
                    "channel_id_actual": video["channel_id"],
                    "verified": True,
                    "error": None,
                }
            )
            state["error"] = None
            if video["channel_id"] != payload["target_channel_id"]:
                state["status"] = "post_upload_channel_mismatch"
                state["error"] = "O ID verificado pertence a outro canal."
            elif video["privacy_status"] != item["privacy_status"]:
                state["status"] = "visibility_mismatch"
                state["error"] = "A visibilidade verificada difere da solicitada."
            elif all(
                value["status"] == "uploaded" and value["verified"]
                for value in state["stage_states"].values()
            ):
                state["status"] = "awaiting_studio" if payload["mode"] == "sequence" else "complete"
                if payload["mode"] == "sequence":
                    state["related_video"]["status"] = "pending_studio"
            else:
                state["status"] = "partial"
            self._event(state, f"{stage}_reconciled", video["video_id"])

        return self._mutate(workflow_id, mutate)[1]

    def confirm_stage_absent_in_studio(
        self,
        workflow_id: str,
        stage: str,
        *,
        no_active_upload_confirmed: bool = False,
    ) -> dict[str, Any]:
        def mutate(_payload, state):
            stage_state = state["stage_states"].get(stage)
            if stage_state is None:
                raise YouTubeWorkflowError("Etapa de upload inválida.")
            if stage_state.get("video_id") or stage_state["status"] not in {"uploading", "uncertain"}:
                raise YouTubeWorkflowConflict(
                    "Só uma etapa sem ID e com resultado incerto pode ser liberada após conferência manual."
                )
            if stage_state["status"] == "uploading" and not no_active_upload_confirmed:
                raise YouTubeWorkflowConflict(
                    "Confirme também que o processo de upload não está mais em execução."
                )
            stage_state.update({"status": "pending", "error": None})
            state["status"] = (
                "partial"
                if any(s["status"] == "uploaded" for s in state["stage_states"].values())
                else "prepared"
            )
            state["error"] = None
            self._event(
                state,
                f"{stage}_confirmed_absent_in_studio",
                "liberação manual; operador conferiu ausência no canal e fim do upload quando necessário",
            )

        return self._mutate(workflow_id, mutate)[1]

    def confirm_related_video(
        self,
        workflow_id: str,
        *,
        short_video_id: str,
        related_video_id: str,
        verified_in_studio: bool,
    ) -> dict[str, Any]:
        def mutate(payload, state):
            if payload["mode"] != "sequence":
                raise YouTubeWorkflowConflict("Este fluxo não contém um Short relacionado.")
            if state["status"] != "awaiting_studio":
                raise YouTubeWorkflowConflict("Os dois vídeos ainda não foram confirmados.")
            long_id = state["stage_states"]["long"].get("video_id")
            short_id = state["stage_states"]["short"].get("video_id")
            if not verified_in_studio:
                raise YouTubeWorkflowError("Confirme somente depois de verificar o campo no YouTube Studio.")
            if related_video_id != long_id or short_video_id != short_id:
                raise YouTubeWorkflowConflict("Os IDs não correspondem aos vídeos deste fluxo.")
            state["related_video"] = {
                "status": "verified_in_studio",
                "short_video_id": short_id,
                "related_video_id": long_id,
                "verified_at": utc_now(),
                "verification_method": "operator_checked_in_youtube_studio",
                "note": None,
            }
            state["status"] = "complete"
            state["error"] = None
            self._event(state, "related_video_verified_in_studio")

        return self._mutate(workflow_id, mutate)[1]


def validate_short_description_before_relation(description: str) -> None:
    lowered = description.casefold()
    phrases = ("vídeo relacionado", "video relacionado", "related video")
    if any(phrase in lowered for phrase in phrases):
        raise YouTubeWorkflowError(
            "A descrição do Short não pode afirmar que o vídeo relacionado já existe. "
            "A associação é configurada e confirmada no YouTube Studio depois dos uploads."
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute_youtube_workflow(
    workflow_id: str,
    *,
    store: YouTubeWorkflowStore,
    youtube_client: Any | None = None,
    credential_loader: Callable[[Path], Any] = get_existing_youtube_credentials,
    client_factory: Callable[[Any], Any] = build_youtube_client,
    upload_function: Callable[..., YouTubeUploadResult] = upload_video_to_youtube,
    progress: Callable[[float, str], None] | None = None,
    token_path: str | Path | None = None,
) -> dict[str, Any]:
    workflow = store.get(workflow_id)
    payload = workflow["payload"]
    state = workflow["state"]
    if state["status"] in {"awaiting_studio", "complete"}:
        return store.public_view(workflow)
    if state["status"] in {
        "visibility_mismatch", "post_upload_channel_mismatch",
        "post_upload_verification_required",
    }:
        raise YouTubeWorkflowConflict(
            "O fluxo está bloqueado por uma verificação pendente; reconcilie antes de continuar."
        )
    if any(stage["status"] in _ACTIVE_UPLOAD_STATES for stage in state["stage_states"].values()):
        raise YouTubeWorkflowConflict(
            "Existe uma etapa em andamento ou incerta; nenhum upload será repetido automaticamente."
        )
    for item in payload["items"]:
        if state["stage_states"][item["stage"]]["status"] != "pending":
            continue
        try:
            actual_hash = _sha256_file(Path(item["path"]))
        except OSError as exc:
            store.mark_blocked(
                workflow_id, "staged_file_invalid",
                "Um arquivo preparado não está disponível; nenhum novo upload foi iniciado.",
            )
            raise YouTubeWorkflowError(
                "Um arquivo preparado não está disponível; o upload foi bloqueado."
            ) from exc
        if actual_hash != item["sha256"]:
            store.mark_blocked(
                workflow_id, "staged_file_invalid",
                "Um arquivo preparado mudou após a validação; nenhum novo upload foi iniciado.",
            )
            raise YouTubeWorkflowError(
                "Um arquivo preparado mudou após a validação; o upload foi bloqueado."
            )

    if youtube_client is None:
        token = Path(token_path or (Path.cwd() / "token.json")).expanduser()
        try:
            credentials = credential_loader(token)
            youtube_client = client_factory(credentials)
        except Exception as exc:
            store.mark_blocked(
                workflow_id,
                "blocked_auth",
                "Não foi possível carregar uma credencial OAuth já autorizada. Nenhum token foi criado.",
            )
            raise YouTubeWorkflowError(
                "Credencial OAuth existente inválida ou ausente; nenhum upload foi iniciado."
            ) from exc

    try:
        channel = verify_youtube_channel(youtube_client, payload["target_channel_id"])
    except Exception as exc:
        store.mark_blocked(
            workflow_id,
            "blocked_channel_verification",
            "O canal autenticado não pôde ser confirmado; nenhum upload foi iniciado.",
        )
        raise YouTubeWorkflowError(
            "A verificação do canal falhou. O envio foi bloqueado e o escopo OAuth não foi ampliado."
        ) from exc

    store.mark_channel_verified(workflow_id, str(channel.get("title") or ""))
    store.mark_running(workflow_id)
    total = max(1, len(payload["items"]))

    for index, item in enumerate(payload["items"]):
        current = store.get(workflow_id)
        stage_state = current["state"]["stage_states"][item["stage"]]
        if stage_state["status"] == "uploaded":
            if not stage_state.get("verified"):
                raise YouTubeWorkflowConflict(
                    "Um upload tem ID, mas ainda precisa de reconciliação; não será repetido."
                )
            continue
        if not store.mark_stage_started(workflow_id, item["stage"]):
            continue

        def report(fraction: float, *, item_index: int = index, label: str = item["stage"]) -> None:
            if progress:
                overall = (item_index + max(0.0, min(1.0, float(fraction)))) / total
                progress(overall, f"YouTube · enviando {label}")

        try:
            result = upload_function(
                item["path"],
                None,
                client_secrets_path=None,
                token_path=None,
                title=item["title"],
                description=item["description"],
                privacy_status=item["privacy_status"],
                made_for_kids=item.get("made_for_kids", False),
                contains_synthetic_media=item["contains_synthetic_media"],
                youtube_client=youtube_client,
                progress=report,
            )
        except Exception as exc:
            store.mark_stage_uncertain(workflow_id, item["stage"], type(exc).__name__)
            raise YouTubeWorkflowError(
                f"O resultado do upload de {item['stage']} é incerto. Verifique o canal e reconcilie antes de retentar."
            ) from exc

        store.record_stage_uploaded(workflow_id, item["stage"], result)
        latest = store.get(workflow_id)
        stage_state = latest["state"]["stage_states"][item["stage"]]
        if not stage_state.get("verified"):
            raise YouTubeWorkflowConflict(
                "O ID foi salvo, mas canal/visibilidade precisam ser verificados antes da próxima etapa."
            )
        if stage_state["channel_id_actual"] != payload["target_channel_id"]:
            raise YouTubeWorkflowConflict(
                "O ID retornado não corresponde ao canal-alvo; a próxima etapa foi bloqueada."
            )
        if stage_state["privacy_actual"] != item["privacy_status"]:
            raise YouTubeWorkflowConflict(
                "A visibilidade retornada difere da solicitada; a próxima etapa foi bloqueada."
            )

    return store.public_view(store.get(workflow_id))


def reconcile_youtube_workflow_upload(
    workflow_id: str,
    stage: str,
    video_id: str,
    *,
    store: YouTubeWorkflowStore,
    youtube_client: Any,
) -> dict[str, Any]:
    workflow = store.get(workflow_id)
    item = next(
        (candidate for candidate in workflow["payload"]["items"] if candidate["stage"] == stage),
        None,
    )
    if item is None:
        raise YouTubeWorkflowError("Etapa de upload inválida.")
    video = verify_existing_youtube_video(
        youtube_client,
        video_id=video_id,
        target_channel_id=workflow["payload"]["target_channel_id"],
        expected_title=item["title"],
    )
    store.record_reconciled_upload(workflow_id, stage, video)
    return store.public_view(store.get(workflow_id))
