import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
# Gemini (TTS realista na aba Text-to-Speech — vozes como Achernar, Leda)
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_TTS_MODEL: str = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_VOICE_PT: str = os.getenv("GEMINI_TTS_VOICE_PT", "Achernar")
GEMINI_HTTP_TIMEOUT_SEC: float = float(os.getenv("GEMINI_HTTP_TIMEOUT_SEC", "180"))
# TTS local (Kokoro — GPU/CPU, pt-BR). Instale com scripts/install_local_tts.sh
LOCAL_TTS_DEVICE: str = os.getenv("LOCAL_TTS_DEVICE", "auto").strip().lower()
LOCAL_TTS_VOICE_PT: str = os.getenv("LOCAL_TTS_VOICE_PT", "pf_dora").strip()
LOCAL_TTS_SPEED: float = float(os.getenv("LOCAL_TTS_SPEED", "1.0"))
LOCAL_TTS_PREFERRED: bool = os.getenv("LOCAL_TTS_PREFERRED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Whisper (Groq): uma única requisição com áudio longo costuma truncar segmentos (~1 min).
# Áudio mais longo que este limiar é transcrito em fatias e os timestamps são unidos.
GROQ_TRANSCRIBE_CHUNK_SEC: float = float(os.getenv("GROQ_TRANSCRIBE_CHUNK_SEC", "42"))
GROQ_TRANSCRIBE_SINGLE_MAX_SEC: float = float(
    os.getenv("GROQ_TRANSCRIBE_SINGLE_MAX_SEC", "42")
)
# Transcrições em fatias: quantas chamadas Groq paralelas no máximo (parede menor em áudio longo).
# Default 2: reduz o tempo de transcrição em ~50% sem disparar 429 (groq_limiter já protege).
GROQ_TRANSCRIBE_MAX_WORKERS: int = max(
    1, min(16, int(os.getenv("GROQ_TRANSCRIBE_MAX_WORKERS", "2")))
)
# Tradução: 1=texto agrupado (menos HTTP); 0=comportamento atual (segmento a segmento).
TRANSLATE_BATCH: bool = os.getenv("TRANSLATE_BATCH", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TRANSLATE_BATCH_MAX_CHARS: int = max(512, int(os.getenv("TRANSLATE_BATCH_MAX_CHARS", "3800")))
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "resultados"))
TEMP_DIR: Path = Path(os.getenv("TEMP_DIR", "temp"))
CLIP_DURATION: int = int(os.getenv("CLIP_DURATION", "50"))
VIRAL_CLIPS_COUNT: int = int(os.getenv("VIRAL_CLIPS_COUNT", "5"))
CLIP_SPEED_UP_PERCENT: float = float(os.getenv("CLIP_SPEED_UP_PERCENT", "2"))

# Saída vertical 9:16 (TikTok / Reels / Shorts)
OUTPUT_VIDEO_WIDTH: int = int(os.getenv("OUTPUT_VIDEO_WIDTH", "1080"))
OUTPUT_VIDEO_HEIGHT: int = int(os.getenv("OUTPUT_VIDEO_HEIGHT", "1920"))
# Legendas no rodapé, compactas (PlayRes no ASS; evitar SRT+force_style que estoura fonte / sobe texto)
TIKTOK_SUBTITLE_FONT_SIZE: int = int(os.getenv("TIKTOK_SUBTITLE_FONT_SIZE", "40"))
TIKTOK_SUBTITLE_MARGIN_V: int = int(os.getenv("TIKTOK_SUBTITLE_MARGIN_V", "88"))
TIKTOK_SUBTITLE_MARGIN_LR: int = int(os.getenv("TIKTOK_SUBTITLE_MARGIN_LR", "56"))

# Crop 9:16 guiado por rosto (desloca o recorte horizontal/vertical em relação ao centro)
SMART_CROP_ENABLED: bool = os.getenv("SMART_CROP_ENABLED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SMART_CROP_FRAME_SAMPLES: int = int(os.getenv("SMART_CROP_FRAME_SAMPLES", "12"))
# Com 2+ rostos: amostras por segundo para estimar boca em movimento (falante).
SMART_CROP_SPEAKER_FPS: float = float(os.getenv("SMART_CROP_SPEAKER_FPS", "4"))
# Mínimo de segundos entre uma mudança de crop e outra (evita alternar foco o tempo todo).
SMART_CROP_MIN_CHANGE_INTERVAL_SEC: float = float(
    os.getenv("SMART_CROP_MIN_CHANGE_INTERVAL_SEC", "3")
)


@lru_cache(maxsize=1)
def _resolve_ffmpeg() -> str:
    def has_filter(ffmpeg_path: str, filter_name: str) -> bool:
        try:
            p = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return False
        out = (p.stdout or "") + (p.stderr or "")
        return filter_name in out

    # Ordem de preferência:
    # - o ffmpeg do PATH
    # - /usr/bin/ffmpeg (Linux)
    # Mas se o ffmpeg "encontrado" não suportar drawtext (hook/CTA), tenta um alternativo.
    found = shutil.which("ffmpeg")
    if found and has_filter(found, "drawtext"):
        return found
    if platform.system() != "Windows":
        system_ffmpeg = "/usr/bin/ffmpeg"
        if os.path.exists(system_ffmpeg) and has_filter(system_ffmpeg, "drawtext"):
            return system_ffmpeg
    if found:
        return found
    return "ffmpeg"


FFMPEG_PATH: str = _resolve_ffmpeg()


def _resolve_ffprobe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    if platform.system() != "Windows":
        return "/usr/bin/ffprobe"
    return "ffprobe"


FFPROBE_PATH: str = _resolve_ffprobe()


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def linux_has_nvidia_driver() -> bool:
    """True quando o driver NVIDIA do kernel está carregado (Linux)."""
    return _is_linux() and Path("/proc/driver/nvidia/version").is_file()


@lru_cache(maxsize=1)
def _linux_h264_hw_encoders_in_ffmpeg() -> frozenset[str]:
    """Encoders H.264 acelerados listados pelo FFmpeg do sistema (Linux)."""
    if not _is_linux():
        return frozenset()
    try:
        p = subprocess.run(
            [FFMPEG_PATH, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    blob = (p.stdout or "") + (p.stderr or "")
    return frozenset(n for n in ("h264_vaapi", "h264_amf", "h264_nvenc", "h264_qsv") if n in blob)


def _resolved_clip_gpu_encoder() -> str:
    """
    Encoder de vídeo para clipes quando CLIP_GPU_ENCODER não está definido no ambiente.
    Linux + NVIDIA (ex.: RTX 5060 Ti): NVENC quando o FFmpeg listar h264_nvenc.
    Linux + Mesa/AMD (ex.: RX 5500 XT): VA-API antes de AMF.
    """
    raw = (os.getenv("CLIP_GPU_ENCODER") or "").strip().lower()
    if raw:
        return raw
    if _is_linux():
        avail = _linux_h264_hw_encoders_in_ffmpeg()
        if linux_has_nvidia_driver() and "h264_nvenc" in avail:
            return "h264_nvenc"
        if "h264_vaapi" in avail:
            return "h264_vaapi"
        if "h264_amf" in avail:
            return "h264_amf"
        if "h264_nvenc" in avail:
            return "h264_nvenc"
    return "h264_amf"


def _default_clip_encode_parallel_gpu() -> int:
    """Mais encodes NVENC em paralelo quando há GPU NVIDIA dedicada (ex.: 16 GB VRAM)."""
    if linux_has_nvidia_driver():
        return 3
    return 2


# Paralelismo: primeiros clipes em libx264 (CPU), últimos na GPU.
# Linux + NVIDIA: padrão h264_nvenc; Linux + Mesa/AMD: h264_vaapi quando disponível.
# Windows / outros: AMF (h264_amf), NVIDIA → h264_nvenc, Intel → h264_qsv (via .env).
# Defaults pensados para ~6c/12t (ex.: Ryzen 5600G): menos x264 simultâneo.
CLIP_ENCODE_PARALLEL_CPU: int = int(os.getenv("CLIP_ENCODE_PARALLEL_CPU", "2"))
CLIP_ENCODE_PARALLEL_GPU: int = int(
    os.getenv("CLIP_ENCODE_PARALLEL_GPU", str(_default_clip_encode_parallel_gpu()))
)
USE_GPU_CLIP_ENCODE: bool = os.getenv("USE_GPU_CLIP_ENCODE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
CLIP_GPU_ENCODER: str = _resolved_clip_gpu_encoder()


@lru_cache(maxsize=1)
def _default_vaapi_render_node_linux() -> str:
    """
    Com APU + GPU dedicada (ex.: 5600G + RX 5500 XT), o Linux costuma expor renderD128 para a
    integrada e renderD129 (ou maior) para a PCIe — preferimos o maior índice para ignorar a iGPU.
    Com um só nó, usamos esse (ex.: iGPU desativada na BIOS).
    """
    dri = Path("/dev/dri")
    if not dri.is_dir():
        return "/dev/dri/renderD128"
    scored: list[tuple[int, str]] = []
    for p in dri.glob("renderD*"):
        if not p.exists():
            continue
        try:
            n = int(p.name.removeprefix("renderD"))
        except ValueError:
            continue
        try:
            if not p.is_char_device():
                continue
        except OSError:
            continue
        scored.append((n, str(p.resolve())))
    if not scored:
        return "/dev/dri/renderD128"
    scored.sort(key=lambda t: t[0])
    if len(scored) >= 2:
        return scored[-1][1]
    return scored[0][1]


def _resolved_vaapi_render_node() -> str:
    raw = (os.getenv("VAAPI_RENDER_NODE") or "").strip()
    if raw:
        return raw
    if _is_linux():
        return _default_vaapi_render_node_linux()
    return "/dev/dri/renderD128"


# Nó DRM para VA-API; sem VAAPI_RENDER_NODE no .env, no Linux escolhe o renderD* de maior índice
# quando há 2+ (típico: ignorar Vega integrada, usar RX 5500 XT).
VAAPI_RENDER_NODE: str = _resolved_vaapi_render_node()

# Paralelismo do ThreadPoolExecutor em pipeline.run_pipeline (clipes ao mesmo tempo).
# Sem teto, N clipes = N FFmpeg/libx264 simultâneos e uso de CPU perto de 100%.
# PIPELINE_MAX_WORKERS: inteiro >0 — força o teto (ex.: 2 para máquina mais fraca).
# Senão: floor(cpu_count * PIPELINE_CPU_FRACTION / PIPELINE_CPU_PER_CLIP_ESTIMATE).
# Fração mais baixa + custo por clipe maior ≈ menos clipes FFmpeg ao mesmo tempo em CPUs 6–8c.
PIPELINE_CPU_FRACTION: float = float(os.getenv("PIPELINE_CPU_FRACTION", "0.65"))
PIPELINE_CPU_PER_CLIP_ESTIMATE: float = float(
    os.getenv("PIPELINE_CPU_PER_CLIP_ESTIMATE", "5")
)


def pipeline_thread_pool_max_workers() -> int:
    """Máximo de clipes processados em paralelo; limita carga de CPU no conjunto."""
    raw = os.getenv("PIPELINE_MAX_WORKERS", "").strip()
    if raw:
        return max(1, int(raw))
    cpus = os.cpu_count() or 8
    denom = max(0.5, PIPELINE_CPU_PER_CLIP_ESTIMATE)
    return max(1, int(cpus * PIPELINE_CPU_FRACTION / denom))


def clip_gpu_uses_vaapi() -> bool:
    return CLIP_GPU_ENCODER in ("h264_vaapi", "vaapi")


def ffmpeg_vaapi_hwdevice_args() -> list[str]:
    """Argumentos globais antes de -i / -ss quando o encode de vídeo é VA-API."""
    if not clip_gpu_uses_vaapi():
        return []
    dev = VAAPI_RENDER_NODE
    if not dev.startswith("/"):
        dev = "/" + dev
    return ["-init_hw_device", f"vaapi=va:{dev}", "-filter_hw_device", "va"]


def ffmpeg_vaapi_vf_hwupload_suffix(*, use_gpu_encoder: bool) -> str:
    """Sufixo de filtro: envia quadros em software para a superfície VA-API antes do encoder."""
    if not use_gpu_encoder or not clip_gpu_uses_vaapi():
        return ""
    return ",format=nv12,hwupload=derive_device=va:extra_hw_frames=64"


def gpu_clip_encoder_ffmpeg_args() -> list[str]:
    """Argumentos FFmpeg para o encoder de vídeo da GPU (clipes paralelos)."""
    enc = CLIP_GPU_ENCODER
    if enc in ("h264_nvenc", "nvenc"):
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "21",
            "-spatial_aq",
            "1",
            "-pix_fmt",
            "yuv420p",
        ]
    if enc in ("h264_vaapi", "vaapi"):
        return [
            "-c:v",
            "h264_vaapi",
            "-qp",
            "23",
            "-bf",
            "0",
        ]
    if enc in ("h264_amf", "amf", "amd"):
        return [
            "-c:v",
            "h264_amf",
            "-quality",
            "balanced",
            "-rc",
            "cqp",
            "-qp_i",
            "22",
            "-qp_p",
            "22",
            "-qp_b",
            "22",
            "-pix_fmt",
            "yuv420p",
        ]
    if enc in ("h264_qsv", "qsv"):
        return [
            "-c:v",
            "h264_qsv",
            "-preset",
            "medium",
            "-global_quality",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
    return ["-c:v", "libx264", "-pix_fmt", "yuv420p"]


def clip_ffmpeg_threads_args(*, use_gpu_encoder: bool) -> list[str]:
    """
    Limita threads do FFmpeg por processo em encode CPU (vários libx264 concorrentes).
    Em GPU o gargalo tende a ser o encoder de vídeo, não o slice de threading global.
    """
    if use_gpu_encoder:
        return []
    cpus = os.cpu_count() or 8
    denom = max(1, CLIP_ENCODE_PARALLEL_CPU)
    n = max(1, min(32, cpus // denom))
    return ["-threads", str(n)]

# Voz Edge-TTS para dublagem em inglês (ex.: en-US-AriaNeural, en-GB-SoniaNeural)
EDGE_TTS_VOICE: str = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")

# Voz Edge-TTS para dublagem em português (ex.: pt-BR-AntonioNeural, pt-BR-FranciscaNeural)
EDGE_TTS_VOICE_PT: str = os.getenv("EDGE_TTS_VOICE_PT", "pt-BR-AntonioNeural")

# Bot Telegram (`telegram_bot.py`) — controle remoto do pipeline no PC local
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ALLOWED_USER_ID: int = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0") or "0")

# Dublagem: opcionalmente remove do vídeo trechos onde o áudio dublado fica em silêncio longo
# (concatena partes e pode gerar "pulo" de imagem; desligue se priorizar continuidade visual)
DUB_TRIM_SILENCE: bool = os.getenv("DUB_TRIM_SILENCE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
DUB_SILENCE_CUT_MIN_SEC: float = float(os.getenv("DUB_SILENCE_CUT_MIN_SEC", "0.85"))

# Timeout por requisição Edge-TTS (evita travar o pipeline se a Microsoft não responder).
EDGE_TTS_REQUEST_TIMEOUT_SEC: float = float(os.getenv("EDGE_TTS_REQUEST_TIMEOUT_SEC", "180"))
# Máximo de sínteses Edge-TTS em paralelo (muitas WS de uma vez → 403 / handshake).
EDGE_TTS_MAX_CONCURRENT: int = max(1, min(8, int(os.getenv("EDGE_TTS_MAX_CONCURRENT", "2"))))
# Retentativas por trecho em falhas transitórias (403, handshake).
EDGE_TTS_RETRIES: int = max(1, min(12, int(os.getenv("EDGE_TTS_RETRIES", "6"))))
DUB_SILENCE_DETECT_DB: float = float(os.getenv("DUB_SILENCE_DETECT_DB", "-40"))

# TTS mais longo que o slot do Whisper: acelerar até este fator (atempo) antes de cortar o resto
DUB_MAX_TTS_SPEEDUP: float = float(os.getenv("DUB_MAX_TTS_SPEEDUP", "4.0"))

# Downloads paralelos com yt-dlp (web, GUI, Telegram). Default 3; ajuste via env.
DOWNLOAD_MAX_WORKERS: int = max(1, min(10, int(os.getenv("DOWNLOAD_MAX_WORKERS", "3"))))

# Transcrição: "local" (faster-whisper na GPU) ou "groq" (nuvem, comportamento antigo).
TRANSCRIBE_BACKEND: str = os.getenv("TRANSCRIBE_BACKEND", "local").strip().lower()
# Modelo do faster-whisper (large-v3 = melhor; medium = mais rápido/menos VRAM).
LOCAL_WHISPER_MODEL: str = os.getenv("LOCAL_WHISPER_MODEL", "large-v3").strip()
LOCAL_WHISPER_COMPUTE: str = os.getenv("LOCAL_WHISPER_COMPUTE", "float16").strip()

# Nome interno da fonte da legenda (deve bater com o "name" do TTF em assets/fonts/)
TIKTOK_SUBTITLE_FONT: str = os.getenv("TIKTOK_SUBTITLE_FONT", "Montserrat").strip()
FONTS_DIR: str = str((Path(__file__).resolve().parents[2] / "assets" / "fonts"))
