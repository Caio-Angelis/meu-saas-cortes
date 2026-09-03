import json
from pathlib import Path

import pytest

from app.publishing import youtube_uploader as uploader


def test_select_upload_files_accepts_one_mp4_and_one_txt(tmp_path: Path):
    video = tmp_path / "corte.mp4"
    caption = tmp_path / "corte.txt"
    video.write_bytes(b"video")
    caption.write_text("Legenda", encoding="utf-8")

    selected = uploader.select_upload_files([str(caption), str(video)])

    assert selected.video_path == video
    assert selected.caption_path == caption


@pytest.mark.parametrize(
    "names",
    [
        ("um.mp4",),
        ("um.mp4", "dois.mp4"),
        ("um.txt", "dois.txt"),
        ("um.mp4", "um.txt", "extra.txt"),
    ],
)
def test_select_upload_files_requires_exact_pair(tmp_path: Path, names: tuple[str, ...]):
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(str(path))

    with pytest.raises(uploader.YouTubeUploadError, match="exatamente 1"):
        uploader.select_upload_files(paths)


def test_select_upload_batch_pairs_five_files_by_stem_in_natural_order(tmp_path: Path):
    paths = []
    for index in (10, 2, 1, 4, 3):
        for suffix in (".txt", ".mp4"):
            path = tmp_path / f"{index}_corte{suffix}"
            path.write_bytes(b"x")
            paths.append(str(path))

    selected = uploader.select_upload_batch(paths)

    assert [item.video_path.stem for item in selected] == [
        "1_corte",
        "2_corte",
        "3_corte",
        "4_corte",
        "10_corte",
    ]
    assert all(item.video_path.stem == item.caption_path.stem for item in selected)


def test_select_upload_batch_rejects_unmatched_caption(tmp_path: Path):
    paths = []
    for index in range(1, 6):
        video = tmp_path / f"{index}_corte.mp4"
        caption = tmp_path / f"{index if index < 5 else 99}_corte.txt"
        video.write_bytes(b"v")
        caption.write_text("c", encoding="utf-8")
        paths.extend((str(video), str(caption)))

    with pytest.raises(uploader.YouTubeUploadError, match="mesmo nome-base"):
        uploader.select_upload_batch(paths)


def test_build_video_metadata_uses_first_line_and_api_limits():
    caption = (
        "Título <forte>\nhttps://www.youtube.com/watch?v=nao-publicar\n"
        + ("á" * 3000)
    )

    title, description = uploader.build_video_metadata("meu_corte.mp4", caption)

    assert title == "Título ‹forte›"
    assert "<" not in description and ">" not in description
    assert "youtube.com" not in description
    assert len(description.encode("utf-8")) <= 5000
    assert description.encode("utf-8").decode("utf-8") == description


def test_validate_client_secrets_requires_desktop_credentials(tmp_path: Path):
    web_secret = tmp_path / "web.json"
    web_secret.write_text(json.dumps({"web": {"client_id": "x"}}), encoding="utf-8")
    with pytest.raises(uploader.YouTubeUploadError, match="Aplicativo para computador"):
        uploader.validate_client_secrets_file(web_secret)

    desktop_secret = tmp_path / "desktop.json"
    desktop_secret.write_text(
        json.dumps({"installed": {"client_id": "id", "client_secret": "secret"}}),
        encoding="utf-8",
    )
    assert uploader.validate_client_secrets_file(desktop_secret) == desktop_secret


def test_first_oauth_uses_local_browser_and_saves_token(tmp_path: Path, monkeypatch):
    secret = tmp_path / "client_secret.json"
    secret.write_text(
        json.dumps({"installed": {"client_id": "id", "client_secret": "secret"}}),
        encoding="utf-8",
    )
    token = tmp_path / "token.json"
    captured = {}

    class FakeAuthorizedCredentials:
        valid = True
        expired = False
        refresh_token = "refresh"

        def to_json(self):
            return '{"refresh_token":"saved"}'

    class FakeCredentials:
        @staticmethod
        def from_authorized_user_file(*args, **kwargs):
            raise AssertionError("não deve carregar token inexistente")

    class FakeFlowInstance:
        def run_local_server(self, **kwargs):
            captured["run_local_server"] = kwargs
            return FakeAuthorizedCredentials()

    class FakeFlow:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            captured["secret_path"] = path
            captured["scopes"] = scopes
            return FakeFlowInstance()

    monkeypatch.setattr(
        uploader,
        "_google_dependencies",
        lambda: (FakeCredentials, object, FakeFlow, object, object, (object, object)),
    )

    credentials = uploader.get_youtube_credentials(secret, token)

    assert isinstance(credentials, FakeAuthorizedCredentials)
    assert captured["scopes"] == [uploader.YOUTUBE_UPLOAD_SCOPE]
    assert captured["run_local_server"]["port"] == 0
    assert captured["run_local_server"]["open_browser"] is True
    assert token.read_text(encoding="utf-8") == '{"refresh_token":"saved"}'


def test_upload_video_builds_official_videos_insert_request(tmp_path: Path, monkeypatch):
    video = tmp_path / "clip.mp4"
    caption = tmp_path / "clip.txt"
    video.write_bytes(b"fake mp4")
    caption.write_text("Meu título\nDescrição #shorts", encoding="utf-8")
    captured = {}

    class FakeStatus:
        def progress(self):
            return 0.5

    class FakeRequest:
        calls = 0

        def next_chunk(self):
            self.calls += 1
            if self.calls == 1:
                return FakeStatus(), None
            return None, {
                "id": "abc123",
                "status": {
                    "privacyStatus": "private",
                    "publishAt": "2026-08-09T11:00:00Z",
                },
            }

    class FakeVideos:
        def insert(self, **kwargs):
            captured.update(kwargs)
            return FakeRequest()

    class FakeYouTube:
        def videos(self):
            return FakeVideos()

    class FakeHttpError(Exception):
        pass

    class FakeHttpLib2:
        class HttpLib2Error(Exception):
            pass

    def fake_build(*args, **kwargs):
        captured["build"] = (args, kwargs)
        return FakeYouTube()

    def fake_media(*args, **kwargs):
        captured["media"] = (args, kwargs)
        return object()

    monkeypatch.setattr(uploader, "get_youtube_credentials", lambda *args: object())
    monkeypatch.setattr(
        uploader,
        "_google_dependencies",
        lambda: (object, object, object, fake_build, FakeHttpError, (fake_media, FakeHttpLib2)),
    )
    progress = []

    result = uploader.upload_video_to_youtube(
        video,
        caption,
        client_secrets_path=tmp_path / "unused.json",
        token_path=tmp_path / "token.json",
        privacy_status="public",
        publish_at="2026-08-09T11:00:00Z",
        progress=progress.append,
    )

    assert captured["build"][0] == ("youtube", "v3")
    assert captured["part"] == "snippet,status"
    assert captured["body"]["snippet"]["title"] == "Meu título"
    assert captured["body"]["snippet"]["description"] == "Meu título\nDescrição #shorts"
    assert captured["body"]["status"]["privacyStatus"] == "private"
    assert captured["body"]["status"]["publishAt"] == "2026-08-09T11:00:00Z"
    assert captured["body"]["status"]["selfDeclaredMadeForKids"] is False
    assert result.video_id == "abc123"
    assert result.url.endswith("abc123")
    assert result.privacy_status == "private"
    assert result.publish_at == "2026-08-09T11:00:00Z"
    assert progress == [0.5, 1.0]
