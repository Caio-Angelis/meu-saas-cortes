import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from groq import Groq, RateLimitError

from app.core.config import (
    FFMPEG_PATH,
    FFPROBE_PATH,
    GROQ_API_KEY,
    GROQ_TRANSCRIBE_CHUNK_SEC,
    GROQ_TRANSCRIBE_MAX_WORKERS,
    GROQ_TRANSCRIBE_SINGLE_MAX_SEC,
)
from app.core.limits import GROQ_MAX_IN_FLIGHT, groq_limiter
from app.core.subprocess_utils import run_cancelable

_log = logging.getLogger("transcriber")

def _groq_timeout_sec() -> float:
    raw = (os.getenv("GROQ_HTTP_TIMEOUT_SEC") or "").strip()
    if not raw:
        return 600.0
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 600.0


_client = Groq(api_key=GROQ_API_KEY, timeout=_groq_timeout_sec())


def _extract_segment(seg) -> dict:
    if isinstance(seg, dict):
        return {
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": str(seg["text"]).strip(),
        }
    return {
        "start": float(seg.start),
        "end": float(seg.end),
        "text": str(seg.text).strip(),
    }


def _probe_duration_seconds(path: str) -> float:
    r = run_cancelable(
        [
            FFPROBE_PATH,
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def _extract_audio_chunk(source: str, dest: str, start: float, duration: float, *, from_video: bool = False) -> None:
    """Fatia áudio com cópia de stream quando possível (evita re-encode mp3→mp3).
    Quando from_video=True, re-encoda para MP3 16kHz mono (compatível com Whisper)."""
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start),
        "-i",
        source,
        "-t",
        str(duration),
        "-vn",
    ]
    if from_video:
        cmd.extend([
            "-acodec", "libmp3lame",
            "-ar", "16000",
            "-ac", "1",
            "-b:a", "32k",
        ])
    else:
        cmd.extend(["-c:a", "copy"])
    cmd.append(dest)
    run_cancelable(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )


def _transcribe_file(path: str, language: str | None, *, retries: int = 4) -> list[dict]:
    for attempt in range(retries):
        try:
            with open(path, "rb") as f:
                data = f.read()
            options: dict = {
                "file": (path, data),
                "model": "whisper-large-v3",
                "response_format": "verbose_json",
            }
            if language:
                options["language"] = language
            with groq_limiter.acquire():
                transcription = _client.audio.transcriptions.create(**options)
            segs = transcription.segments
            if not segs:
                return []
            return [_extract_segment(s) for s in segs]
        except RateLimitError:
            if attempt + 1 >= retries:
                raise
            time.sleep(30)
    return []


def _segments_cover_audio(segments: list[dict], duration: float, *, slack_sec: float = 2.5) -> bool:
    if duration <= slack_sec:
        return True
    if not segments:
        return False
    return float(segments[-1]["end"]) >= duration - slack_sec


def _transcribe_chunk_at(
    audio_path: str, start: float, piece_dur: float, language: str | None,
    *, from_video: bool = False,
) -> tuple[float, list[dict]]:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        _extract_audio_chunk(audio_path, tmp_path, start, piece_dur, from_video=from_video)
        part = _transcribe_file(tmp_path, language)
        out: list[dict] = []
        for s in part:
            ss = dict(s)
            ss["start"] = float(ss["start"]) + start
            ss["end"] = float(ss["end"]) + start
            out.append(ss)
        return (start, out)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _transcribe_chunks_parallel(
    audio_path: str,
    chunk_ranges: list[tuple[float, float]],
    language: str | None,
    max_workers: int,
    *, from_video: bool = False,
) -> list[dict]:
    if not chunk_ranges:
        return []

    # Respeita GROQ_MAX_IN_FLIGHT (.env): vários chunks em paralelo sem limite → 429 no Whisper.
    workers = max(
        1,
        min(int(max_workers), len(chunk_ranges), max(1, GROQ_MAX_IN_FLIGHT)),
    )

    if workers == 1:
        all_segments: list[dict] = []
        n_total = len(chunk_ranges)
        log_every = max(1, n_total // 8)
        for i, (start, piece_dur) in enumerate(chunk_ranges, start=1):
            if i == 1 or i % log_every == 0 or i == n_total:
                _log.info(
                    "Transcrição: fatia %s/%s (~%.0f–%.0fs).",
                    i,
                    n_total,
                    start,
                    start + piece_dur,
                )
            _, part = _transcribe_chunk_at(audio_path, start, piece_dur, language, from_video=from_video)
            all_segments.extend(part)
        all_segments.sort(key=lambda x: float(x["start"]))
        return all_segments

    by_start: dict[float, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [
            ex.submit(_transcribe_chunk_at, audio_path, start, piece_dur, language, from_video=from_video)
            for start, piece_dur in chunk_ranges
        ]
        for fut in as_completed(futs):
            st, part = fut.result()
            by_start[st] = part

    all_segments: list[dict] = []
    for start, _dur in chunk_ranges:
        all_segments.extend(by_start.get(start, []))

    all_segments.sort(key=lambda x: (float(x["start"]), float(x["end"]), x.get("text", "")))
    return all_segments


def transcribe_audio(audio_path: str, language: str = None, *, source_video_path: str | None = None) -> list[dict]:
    """
    Transcreve o áudio inteiro com timestamps. Áudio longo é fatiado: uma única chamada
    ao Whisper costuma devolver só os primeiros ~1 min de segmentos.
    Quando source_video_path é fornecido e o áudio é longo, fatia diretamente do vídeo
    (pula a extração de MP3 intermediário).
    """
    from app.core.config import TRANSCRIBE_BACKEND
    if TRANSCRIBE_BACKEND == "local":
        try:
            from app.ai_integrations.local_whisper import local_whisper_available, transcribe_local
            if local_whisper_available():
                _log.info("Transcrição LOCAL (faster-whisper na GPU).")
                return transcribe_local(audio_path, language)
            _log.warning("faster-whisper indisponível; caindo para Groq.")
        except Exception as e:
            _log.warning("Transcrição local falhou (%s); caindo para Groq.", e)
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Áudio não encontrado: {audio_path}")

    duration = _probe_duration_seconds(audio_path)
    chunk_sec = max(20.0, float(GROQ_TRANSCRIBE_CHUNK_SEC))
    single_max = max(20.0, float(GROQ_TRANSCRIBE_SINGLE_MAX_SEC))

    use_chunks = duration > single_max
    if not use_chunks:
        segments = _transcribe_file(audio_path, language)
        if _segments_cover_audio(segments, duration):
            return segments
        _log.warning(
            "Transcrição única não cobre o áudio inteiro; refazendo em fatias (Whisper em áudio longo)."
        )

    n_chunks = max(1, int((duration + chunk_sec - 1e-6) // chunk_sec))
    if duration > single_max:
        eff = max(
            1,
            min(GROQ_TRANSCRIBE_MAX_WORKERS, GROQ_MAX_IN_FLIGHT),
        )
        _log.info(
            "Transcrevendo em fatias de ~%ss (%s trecho(s); até %s chamada(s) Groq em paralelo; áudio %.1fs).",
            f"{chunk_sec:.0f}",
            n_chunks,
            eff,
            duration,
        )

    chunk_ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < duration - 1e-6:
        piece_dur = min(chunk_sec, duration - start)
        if piece_dur < 0.04:
            break
        chunk_ranges.append((start, piece_dur))
        start += chunk_sec

    return _transcribe_chunks_parallel(
        audio_path,
        chunk_ranges,
        language,
        GROQ_TRANSCRIBE_MAX_WORKERS,
        from_video=source_video_path is not None,
    )
