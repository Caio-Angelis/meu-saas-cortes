"""Upload de um MP4 para o YouTube pela YouTube Data API v3 oficial."""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.caption_text import remove_links_from_caption

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
VALID_PRIVACY_STATUSES = frozenset({"public", "unlisted", "private"})
_RETRIABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
_MAX_UPLOAD_RETRIES = 8
_DESCRIPTION_MAX_BYTES = 5000
_TITLE_MAX_CHARS = 100


class YouTubeUploadError(RuntimeError):
    """Erro de configuração, autenticação ou upload apresentável na GUI."""


@dataclass(frozen=True)
class YouTubeUploadResult:
    video_id: str
    url: str
    title: str
    privacy_status: str
    publish_at: str | None = None


@dataclass(frozen=True)
class YouTubeUploadFiles:
    video_path: Path
    caption_path: Path


def select_upload_files(paths: list[str] | tuple[str, ...]) -> YouTubeUploadFiles:
    """Valida a seleção exigida pela GUI: exatamente um MP4 e um TXT."""
    selected = [Path(path).expanduser().resolve() for path in paths]
    mp4s = [path for path in selected if path.suffix.lower() == ".mp4"]
    captions = [path for path in selected if path.suffix.lower() == ".txt"]
    if len(selected) != 2 or len(mp4s) != 1 or len(captions) != 1:
        raise YouTubeUploadError("Selecione exatamente 1 arquivo MP4 e 1 arquivo TXT.")
    missing = [path for path in selected if not path.is_file()]
    if missing:
        raise YouTubeUploadError(f"Arquivo não encontrado: {missing[0]}")
    return YouTubeUploadFiles(video_path=mp4s[0], caption_path=captions[0])


def _natural_stem_key(path: Path) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.stem)
    )


def select_upload_batch(
    paths: list[str] | tuple[str, ...],
    *,
    expected_count: int = 5,
) -> tuple[YouTubeUploadFiles, ...]:
    """Valida e pareia N MP4/TXT pelo nome-base, em ordem natural."""
    selected = [Path(path).expanduser().resolve() for path in paths]
    mp4s = [path for path in selected if path.suffix.lower() == ".mp4"]
    captions = [path for path in selected if path.suffix.lower() == ".txt"]
    if (
        len(selected) != expected_count * 2
        or len(mp4s) != expected_count
        or len(captions) != expected_count
    ):
        raise YouTubeUploadError(
            f"Selecione exatamente {expected_count} arquivos MP4 e {expected_count} arquivos TXT."
        )
    missing = [path for path in selected if not path.is_file()]
    if missing:
        raise YouTubeUploadError(f"Arquivo não encontrado: {missing[0]}")

    videos_by_stem = {path.stem.casefold(): path for path in mp4s}
    captions_by_stem = {path.stem.casefold(): path for path in captions}
    if len(videos_by_stem) != expected_count or len(captions_by_stem) != expected_count:
        raise YouTubeUploadError("Existem arquivos repetidos com o mesmo nome-base.")
    if videos_by_stem.keys() != captions_by_stem.keys():
        missing_txt = sorted(videos_by_stem.keys() - captions_by_stem.keys())
        missing_mp4 = sorted(captions_by_stem.keys() - videos_by_stem.keys())
        details: list[str] = []
        if missing_txt:
            details.append("sem TXT: " + ", ".join(missing_txt))
        if missing_mp4:
            details.append("sem MP4: " + ", ".join(missing_mp4))
        raise YouTubeUploadError(
            "Cada MP4 precisa de um TXT com o mesmo nome-base (" + "; ".join(details) + ")."
        )

    ordered_videos = sorted(mp4s, key=_natural_stem_key)
    return tuple(
        YouTubeUploadFiles(
            video_path=video,
            caption_path=captions_by_stem[video.stem.casefold()],
        )
        for video in ordered_videos
    )


def _without_forbidden_angle_brackets(value: str) -> str:
    # A API não aceita < nem > em title/description. Os glifos abaixo preservam a leitura.
    return value.replace("<", "‹").replace(">", "›")


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def build_video_metadata(video_path: str | Path, caption_text: str) -> tuple[str, str]:
    """Deriva título e descrição dentro dos limites oficiais do YouTube."""
    video = Path(video_path)
    description = _without_forbidden_angle_brackets(remove_links_from_caption(caption_text))
    if not description:
        raise YouTubeUploadError("O arquivo TXT está vazio.")

    first_line = next((line.strip() for line in description.splitlines() if line.strip()), "")
    title = first_line or video.stem.replace("_", " ").strip()
    title = _without_forbidden_angle_brackets(title)[:_TITLE_MAX_CHARS].strip()
    if not title:
        title = video.stem[:_TITLE_MAX_CHARS] or "Vídeo"

    return title, _truncate_utf8(description, _DESCRIPTION_MAX_BYTES)


def validate_client_secrets_file(path: str | Path) -> Path:
    """Confere se o JSON é uma credencial OAuth do tipo aplicativo para computador."""
    secrets_path = Path(path).expanduser().resolve()
    if not secrets_path.is_file():
        raise YouTubeUploadError(f"Credencial OAuth não encontrada: {secrets_path}")
    try:
        body = json.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise YouTubeUploadError("O arquivo de credenciais OAuth não é um JSON válido.") from exc
    if not isinstance(body, dict) or not isinstance(body.get("installed"), dict):
        raise YouTubeUploadError(
            "Use uma credencial OAuth do tipo 'Aplicativo para computador' (chave 'installed')."
        )
    installed = body["installed"]
    if not installed.get("client_id") or not installed.get("client_secret"):
        raise YouTubeUploadError("O JSON OAuth não contém client_id/client_secret.")
    return secrets_path


def _google_dependencies() -> tuple[object, object, object, object, object, object]:
    try:
        import httplib2
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise YouTubeUploadError(
            "Dependências do YouTube ausentes. Execute: pip install -r requirements.txt"
        ) from exc
    return Credentials, Request, InstalledAppFlow, build, HttpError, (MediaFileUpload, httplib2)


def _save_credentials(credentials: object, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = token_path.with_suffix(token_path.suffix + ".tmp")
    temp_path.write_text(credentials.to_json(), encoding="utf-8")  # type: ignore[attr-defined]
    try:
        temp_path.chmod(0o600)
    except OSError:
        pass
    temp_path.replace(token_path)


def get_youtube_credentials(client_secrets_path: str | Path, token_path: str | Path) -> object:
    """Carrega/renova o token; abre o OAuth no navegador somente quando necessário."""
    secrets_path = validate_client_secrets_file(client_secrets_path)
    token = Path(token_path).expanduser().resolve()
    Credentials, Request, InstalledAppFlow, _build, _HttpError, _media = _google_dependencies()

    credentials = None
    if token.is_file():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(token), [YOUTUBE_UPLOAD_SCOPE]
            )
        except (OSError, ValueError, json.JSONDecodeError):
            credentials = None

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            # Token revogado/expirado: refaz o consentimento no navegador.
            credentials = None

    if not credentials or not credentials.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(secrets_path), scopes=[YOUTUBE_UPLOAD_SCOPE]
            )
            credentials = flow.run_local_server(
                port=0,
                open_browser=True,
                access_type="offline",
                prompt="consent",
                authorization_prompt_message=(
                    "Autorize o Studio Cortes no navegador para publicar no YouTube."
                ),
                success_message=(
                    "Autorização concluída. Pode fechar esta aba e voltar ao Studio Cortes."
                ),
            )
        except Exception as exc:
            raise YouTubeUploadError(f"Não foi possível autorizar a conta do YouTube: {exc}") from exc
    _save_credentials(credentials, token)
    return credentials


def _http_error_message(exc: BaseException) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    reason = str(exc)
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        try:
            payload = json.loads(content.decode("utf-8", errors="replace"))
            reason = str(payload.get("error", {}).get("message") or reason)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    prefix = f"YouTube respondeu HTTP {status}" if status else "Falha na API do YouTube"
    return f"{prefix}: {reason}"


def upload_video_to_youtube(
    video_path: str | Path,
    caption_path: str | Path,
    *,
    client_secrets_path: str | Path,
    token_path: str | Path,
    privacy_status: str = "public",
    made_for_kids: bool = False,
    publish_at: str | None = None,
    progress: Callable[[float], None] | None = None,
) -> YouTubeUploadResult:
    """Autentica, envia o MP4 de forma resumível e retorna o link publicado."""
    files = select_upload_files([str(video_path), str(caption_path)])
    privacy = (privacy_status or "").strip().lower()
    if privacy not in VALID_PRIVACY_STATUSES:
        raise YouTubeUploadError(f"Privacidade inválida: {privacy_status}")
    try:
        caption = files.caption_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise YouTubeUploadError(f"Não foi possível ler a legenda TXT: {exc}") from exc
    title, description = build_video_metadata(files.video_path, caption)
    scheduled_at = (publish_at or "").strip() or None
    if scheduled_at is not None:
        # Exigência da API: publishAt só pode ser definido enquanto o vídeo está privado.
        privacy = "private"

    credentials = get_youtube_credentials(client_secrets_path, token_path)
    _Credentials, _Request, _Flow, build, HttpError, media_deps = _google_dependencies()
    MediaFileUpload, httplib2 = media_deps
    try:
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        media = MediaFileUpload(
            str(files.video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
        )
        upload_status_body: dict[str, object] = {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        }
        if scheduled_at is not None:
            upload_status_body["publishAt"] = scheduled_at
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": "22",
                    "defaultLanguage": "pt-BR",
                },
                "status": upload_status_body,
            },
            media_body=media,
        )

        response = None
        retries = 0
        while response is None:
            try:
                upload_status, response = request.next_chunk()
                retries = 0
                if upload_status is not None and progress is not None:
                    progress(max(0.0, min(1.0, float(upload_status.progress()))))
            except HttpError as exc:
                status_code = getattr(getattr(exc, "resp", None), "status", None)
                if status_code not in _RETRIABLE_STATUS_CODES or retries >= _MAX_UPLOAD_RETRIES:
                    raise YouTubeUploadError(_http_error_message(exc)) from exc
                retries += 1
                time.sleep(random.uniform(0, min(2**retries, 32)))
            except (OSError, httplib2.HttpLib2Error) as exc:
                if retries >= _MAX_UPLOAD_RETRIES:
                    raise YouTubeUploadError(f"A conexão falhou durante o upload: {exc}") from exc
                retries += 1
                time.sleep(random.uniform(0, min(2**retries, 32)))
    except YouTubeUploadError:
        raise
    except Exception as exc:
        raise YouTubeUploadError(f"Falha ao enviar o vídeo para o YouTube: {exc}") from exc

    video_id = str((response or {}).get("id") or "").strip()
    if not video_id:
        raise YouTubeUploadError("O YouTube concluiu a requisição sem retornar o ID do vídeo.")
    response_status = (response or {}).get("status") or {}
    actual_privacy = str(response_status.get("privacyStatus") or privacy)
    actual_publish_at = str(response_status.get("publishAt") or scheduled_at or "").strip() or None
    if progress is not None:
        progress(1.0)
    return YouTubeUploadResult(
        video_id=video_id,
        url=YOUTUBE_WATCH_URL.format(video_id=video_id),
        title=title,
        privacy_status=actual_privacy,
        publish_at=actual_publish_at,
    )
