"""
Interface gráfica com workspace principal para cortes e ferramentas secundárias.

- Workspace «Cortes Virais»: pipeline de clipes (mesmas opções do main.py), com
  ação principal fixa e controles essenciais visíveis no primeiro enquadramento.
- Aba «Máquina de Quizzes»: geração de vídeo quiz via `app.pipelines.quiz.quiz_pipeline`.
- Aba «Batalha 1v1»: duelo por física 2D (`app.pipelines.batalha.batalha_pipeline`).
- Aba «História»: vídeo narrado com cenas IA (`app.pipelines.historia.historia_pipeline` + ComfyUI).
- Aba «Text-to-Speech»: texto → MP3 (local Kokoro / Gemini / Edge) com pré-ouvir a voz.
- Aba «Análise de desempenho»: CSV dos últimos 7 dias → 3 próximos temas recomendados.
- Log e tabela de resultados ficam em um painel de atividade recolhível.

Execute na raiz do projeto:
    python gui.py
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import _venv_reexec

_venv_reexec.ensure_venv(__file__)

from app.core.linux_desktop_bootstrap import apply_linux_desktop_defaults

apply_linux_desktop_defaults()

import asyncio
import io
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# Garante caminhos relativos (resultados/, temp/) a partir da pasta do projeto
_ROOT = Path(__file__).resolve().parent
os.chdir(_ROOT)
_YOUTUBE_CREDENTIALS_REFERENCE = _ROOT / "data" / "youtube_client_secrets_path.txt"


def _initial_youtube_client_secrets_path() -> str:
    configured = (os.getenv("YOUTUBE_CLIENT_SECRETS_FILE") or "").strip()
    if configured:
        return configured
    try:
        return _YOUTUBE_CREDENTIALS_REFERENCE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

import app.core.config as _cfg
from app.analytics.performance import (
    PerformanceAnalysis,
    PerformanceAnalysisError,
    analyze_performance_csv,
)
from app.analytics.retention_loop import (
    analyze_retention_report_file,
    save_growth_profile,
)
from app.core.config import (
    DOWNLOAD_MAX_WORKERS,
    OUTPUT_DIR,
    TEMP_DIR,
    TIKTOK_UPLOAD_URL,
)
from app.core.logging_setup import gui_pipeline_log_redirect, setup_logging
from app.download.ytdlp_download import (
    VideoSourceAttribution,
    collect_urls_from_lines,
    download_video,
    resolve_ytdlp_executable,
    search_youtube_top_by_views,
)
from app.gui.gui_export import (
    desktop_notify,
    export_cortes_zip,
    ffprobe_duration_seconds,
    format_duration_hms,
)
from app.gui.studio_motion import (
    Motion,
    ease_out_back,
    ease_out_cubic,
    ease_out_quint,
    lerp,
    lerp_color,
    sample_gradient,
)
from app.gui.studio_theme import (
    AQUA,
    EDGE,
    INK,
    INK2,
    MOSS,
    MOSS_DK,
    MOSS_HI,
    MUTED,
    MUTED_SOFT,
    PANEL,
    PANEL2,
    STATUS_BG,
    SUCCESS,
    TEXT,
    StudioTheme,
    configure_studio_theme,
    configure_ui_fonts,
)
from app.pipelines.batalha.batalha_pipeline import (
    normalize_batalha_modo,
    run_batalha_pipeline_from_payload,
)
from app.pipelines.cortes.pipeline import run_pipeline
from app.pipelines.historia.historia_pipeline import run_historia_pipeline
from app.pipelines.quiz.quiz_pipeline import normalize_quiz_difficulty, run_quiz_pipeline
from app.publishing.youtube_schedule import (
    DEFAULT_SCHEDULE_TIMEZONE,
    build_daily_publish_times,
    youtube_publish_at,
)
from app.publishing.youtube_uploader import (
    YouTubeUploadError,
    YouTubeUploadFiles,
    YouTubeUploadResult,
    select_upload_batch,
    upload_video_to_youtube,
    validate_client_secrets_file,
)
from app.tts.gemini_tts import gemini_tts_available
from app.tts.local_tts import local_tts_available
from app.tts.tts_standalone import play_audio_file, synthesize_tts_preview
from app.tts.tts_voices import (
    default_voice_label,
    gui_voice_labels,
    voice_id_from_label,
)

# Espaçamento visual (px): micro / próximo / seção / hero
_PX_MICRO = 6
_PX_NEAR = 14
_PX_SECTION = 24
_PX_HERO = 32

# Texto de placeholder da área de URLs (uma linha; cor aplicada via hint)
_URLS_PLACEHOLDER = (
    "Cole as URLs aqui — uma por linha. Ex.: https://www.youtube.com/watch?v=… "
    "(yt-dlp baixa para temp/ antes do pipeline.)"
)

# Texto longo (yt-dlp / cookies) exibido só via botão [?]
_URLS_HELP_TEXT = (
    "Cada URL é baixada com yt-dlp para a pasta temp/ antes do pipeline.\n\n"
    "Alternativa: preencha «Tema / busca YouTube» (ex.: Filme odisseia). "
    "O app busca o vídeo com ≥10 min e mais visualizações, baixa e gera os cortes. "
    "Tema e URLs/arquivos são exclusivos — use um ou outro.\n\n"
    "Se o YouTube responder 403: pip install -U \"yt-dlp[default]\" "
    "e configure cookies (veja .env.example)."
)

# Alias de tokens (compat com código legado da GUI)
_MUTED_NOTE_FG = MUTED_SOFT
_SIDEBAR_BG = INK2
_SIDEBAR_FG = MUTED
_STATUS_BG = STATUS_BG
_STATUS_FG = MUTED_SOFT

# Ícones monocromáticos: discretos, legíveis e sem dependência de emoji/sistema.
_IC_VIDEO = "◉"
_IC_SETTINGS = "✦"
_IC_MIC = "◌"
_IC_RUN = "↗"
_IC_CLIPBOARD = "▤"
_IC_LOG = "≡"
_IC_DONE = "✓"
_IC_FOLDER = "↗"
_IC_QUIZ = "◇"
_IC_SPEAKER = "◖"
_IC_BATALHA = "◇"
_IC_HISTORIA = "✎"
_IC_YOUTUBE = "▶"
_IC_ANALYTICS = "⌁"

# Rótulos de voz na GUI (Kokoro local + Gemini + Edge)
_GUI_TTS_VOICE_LABELS: tuple[str, ...] = gui_voice_labels()


class _QueueWriter:
    def __init__(self, q: queue.Queue) -> None:
        self._q = q

    def write(self, s: str) -> None:
        if s:
            self._q.put(s)

    def flush(self) -> None:
        pass


def _format_pipeline_error(exc: BaseException) -> str:
    """Resumo curto para o painel de log e messagebox."""
    return f"{type(exc).__name__}: {exc}"


def _pipeline_log_line(msg: str) -> None:
    """
    Linha de estado no painel de log durante o worker (stdout → fila → Text).
    Textos curtos em português para o utilizador acompanhar o que está em execução.
    """
    print(f"\n▸ {msg}\n", flush=True)


def _media_site_hint(url: str) -> str:
    u = url.strip().lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "tiktok.com" in u:
        return "TikTok"
    if "instagram.com" in u:
        return "Instagram"
    if "twitch.tv" in u:
        return "Twitch"
    return "site"


def _open_folder(path: Path) -> None:
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _validate_hex(name: str, value: str) -> str | None:
    v = (value or "").strip()
    if not v.startswith("#"):
        v = "#" + v
    if len(v) != 7:
        return f"{name}: use cor em hex com 6 dígitos (ex.: #FFFF00)."
    try:
        int(v[1:], 16)
    except ValueError:
        return f"{name}: hex inválido."
    return None


def _normalize_hex(value: str) -> str:
    v = (value or "").strip()
    if not v.startswith("#"):
        v = "#" + v
    return v.upper()


def _sort_clip_outputs(paths: list[str]) -> list[str]:
    """Ordena por índice numérico no prefixo do nome (ex.: 1_foo.mp4, 2_foo.mp4)."""

    def key(p: str) -> tuple:
        stem = Path(p).stem
        parts = stem.split("_", 1)
        try:
            return (0, int(parts[0]), stem.lower())
        except (ValueError, IndexError):
            return (1, 0, stem.lower())

    return sorted(paths, key=key)


_TELEGRAM_VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
_TELEGRAM_CAPTION_SUFFIXES = {".txt", ".srt", ".vtt", ".ass"}
_TELEGRAM_MAX_UPLOAD_BYTES = 49 * 1024 * 1024


def _telegram_result_paths(output_dir: Path) -> list[Path]:
    """Lista vídeos e arquivos de legenda de resultados/, ignorando JSONs."""
    if not output_dir.is_dir():
        return []
    suffixes = _TELEGRAM_VIDEO_SUFFIXES | _TELEGRAM_CAPTION_SUFFIXES
    return [
        Path(path)
        for path in _sort_clip_outputs(
            [str(path) for path in output_dir.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]
        )
    ]


def _compress_for_telegram(path: Path, work_dir: Path, index: int) -> Path:
    """Cria cópia temporária abaixo do limite do Bot API, sem tocar no original."""
    duration = ffprobe_duration_seconds(str(path)) or 50.0
    target_total_bps = int(_TELEGRAM_MAX_UPLOAD_BYTES * 8 * 0.82 / max(duration, 1.0))
    video_bps = max(500_000, target_total_bps - 96_000)
    output = work_dir / f"{index}_{path.stem}.mp4"
    proc = subprocess.run(
        [
            _cfg.FFMPEG_PATH,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            str(video_bps),
            "-maxrate",
            str(video_bps),
            "-bufsize",
            str(video_bps * 2),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if proc.returncode != 0 or not output.is_file():
        detail = (proc.stderr or "falha desconhecida").strip()[-800:]
        raise RuntimeError(f"FFmpeg não conseguiu compactar {path.name}: {detail}")
    if output.stat().st_size > _TELEGRAM_MAX_UPLOAD_BYTES:
        raise RuntimeError(
            f"A cópia compactada de {path.name} ainda ficou acima de 49 MB "
            f"({output.stat().st_size / (1024 * 1024):.1f} MB)."
        )
    return output


def _configure_modern_theme(root: tk.Tk) -> StudioTheme:
    """Tema Cortes Lab via app.gui.studio_theme."""
    return configure_studio_theme(root)


def _configure_ui_fonts(root: tk.Tk) -> None:
    configure_ui_fonts(root)


class CortesApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Cortes Lab — Creative Automation Studio")
        _configure_ui_fonts(self)
        self._ui_family = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self._theme = _configure_modern_theme(self)
        self._text_bg = self._theme.text_bg
        self._text_fg = self._theme.text_fg
        self._hint_fg = self._theme.hint_fg
        self._is_dark_theme = self._theme.is_dark
        self._log_fg = self._theme.log_fg
        self._urls_ph_visible = False
        self._sidebar_buttons: list[ttk.Button] = []
        self._photos: list[tk.PhotoImage] = []  # evita GC dos PhotoImage
        # Movimento: transições de página, indicador lateral e progresso animado.
        self._motion = Motion(self)
        # `None` representa o workspace principal de cortes, que não precisa de
        # rolagem: os controles essenciais ficam todos no primeiro enquadramento.
        self._tab_canvases: list[tk.Canvas | None] = []
        self._wheel_targets: list[tuple[tk.Canvas, tk.Misc]] = []
        self._page_chrome_labels: list[tuple[ttk.Label, str, int]] = []
        self._page_badge_lbl: ttk.Label | None = None
        self._page_cover: tk.Frame | None = None
        self._nav_indicator: tk.Frame | None = None
        self._cut_action_bar: tk.Frame | None = None
        self._secondary_nav_wrap: tk.Frame | None = None
        self._secondary_nav_toggle: ttk.Button | None = None
        self._secondary_nav_expanded = False
        self._workspace_area: tk.Frame | None = None
        self._run_surface_outer: tk.Frame | None = None
        self._activity_surface_outer: tk.Frame | None = None
        self._activity_expanded = False
        self._topbar: tk.Canvas | None = None
        self._status_dot: tk.Label | None = None
        self._status_pulse_gen = 0
        self._prog_current = 0.0
        self._status_var = tk.StringVar(value="Pronto")
        self.configure(background=INK)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        min_w = min(1080, max(960, screen_w - 60))
        min_h = min(720, max(640, screen_h - 80))
        self.minsize(min_w, min_h)
        default_w = min(1360, max(min_w, screen_w - 60))
        default_h = min(920, max(min_h, screen_h - 80))
        self.geometry(f"{default_w}x{default_h}")

        self._video_path = tk.StringVar(value="Nenhum vídeo selecionado")
        self._video_paths: list[str] = []
        self._search_theme = tk.StringVar()
        self._lang = tk.StringVar(value="pt")
        self._position = tk.StringVar(value="bottom")
        self._font = tk.StringVar(value=(_cfg.TIKTOK_SUBTITLE_FONT or "Montserrat").strip() or "Montserrat")
        self._color = tk.StringVar(value="#FFFF00")
        self._bg_color = tk.StringVar(value="#000000")
        self._opacity = tk.IntVar(value=75)
        self._dub_to = tk.StringVar(value="off")  # off | en | pt
        self._tts_voice = tk.StringVar(value="")
        # Melhorias do checklist (defaults = config/.env atual)
        _tb = (_cfg.TRANSCRIBE_BACKEND or "local").strip().lower()
        self._transcribe_backend = tk.StringVar(value=_tb if _tb in ("local", "groq") else "local")
        self._karaoke = tk.BooleanVar(value=bool(_cfg.SUBTITLE_KARAOKE))
        self._visual_grade = tk.BooleanVar(value=bool(_cfg.VISUAL_GRADE))
        self._visual_progress = tk.BooleanVar(value=bool(_cfg.VISUAL_PROGRESS_BAR))
        self._visual_watermark = tk.StringVar(value=str(_cfg.VISUAL_WATERMARK_TEXT or ""))
        self._prefer_local_tts = tk.BooleanVar(value=bool(_cfg.LOCAL_TTS_PREFERRED))
        self._smart_crop = tk.BooleanVar(value=bool(_cfg.SMART_CROP_ENABLED))
        self._use_gpu_encode = tk.BooleanVar(value=bool(_cfg.USE_GPU_CLIP_ENCODE))
        self._log_q: queue.Queue = queue.Queue()
        self._last_pipeline_error: str | None = None
        self._worker: threading.Thread | None = None
        self._pipeline_running = False
        self._idle_states: dict[tk.Widget, str] = {}
        self._last_outputs: list[str] = []
        self._tree_row_to_path: dict[str, str] = {}
        self._results_gen = 0
        self._open_results_when_done = tk.BooleanVar(value=True)
        self._notify_when_done = tk.BooleanVar(value=(sys.platform in ("linux", "darwin")))
        self._zip_when_done = tk.BooleanVar(value=False)
        self._telegram_sending = False

        # Aba YouTube (5 pares MP4/TXT → uma publicação por dia, a partir de amanhã)
        self._youtube_selected_batch: tuple[YouTubeUploadFiles, ...] = ()
        self._youtube_selection_label = tk.StringVar(value="Nenhum lote selecionado")
        self._youtube_schedule_time = tk.StringVar(value="07:00")
        self._youtube_schedule_timezone = (
            os.getenv("YOUTUBE_SCHEDULE_TIMEZONE") or DEFAULT_SCHEDULE_TIMEZONE
        ).strip()
        self._youtube_schedule_preview = tk.StringVar(value="")
        self._youtube_made_for_kids = tk.BooleanVar(value=False)
        self._youtube_client_secrets_path = tk.StringVar(
            value=_initial_youtube_client_secrets_path()
        )
        self._youtube_uploading = False
        self._youtube_upload_thread: threading.Thread | None = None

        # Aba Análise de desempenho (CSV dos últimos 7 dias → próximos 3 temas)
        self._performance_csv_path = tk.StringVar(value="")
        self._performance_csv_label = tk.StringVar(value="Nenhum CSV selecionado")
        self._performance_summary = tk.StringVar(
            value="Selecione um CSV para descobrir os três próximos temas."
        )
        self._performance_busy = False
        self._performance_last_analysis: PerformanceAnalysis | None = None
        self._performance_thread: threading.Thread | None = None

        # Loop de retenção: relatório JSON do TikTok → growth profile do pipeline.
        self._retention_report_path = tk.StringVar(value="")
        self._retention_label = tk.StringVar(value="Nenhum relatório JSON selecionado")
        self._retention_busy = False
        self._retention_thread: threading.Thread | None = None

        # Aba Máquina de Quizzes (projeto.md §13.3.1)
        self._quiz_theme = tk.StringVar(value="Conhecimentos gerais")
        self._quiz_count = tk.IntVar(value=5)
        self._quiz_timer_sec = tk.IntVar(value=5)
        self._quiz_difficulty = tk.StringVar(value="Variado")
        self._quiz_tts_voice = tk.StringVar(value=default_voice_label())
        self._quiz_bg_color = tk.StringVar(value="#1A1A1A")

        # Aba Batalha 1v1
        self._batalha_theme = tk.StringVar(value="Filmes de ficção científica")
        self._batalha_modo_label = tk.StringVar(value="Duelo de Tamanho (Agar.io)")
        self._batalha_tts_voice = tk.StringVar(value=default_voice_label())

        # Aba História (vídeo narrado — Groq + ComfyUI + TTS)
        self._historia_tts_voice = tk.StringVar(value=default_voice_label())

        # Aba Text-to-Speech
        self._standalone_tts_voice = tk.StringVar(value=default_voice_label())
        self._tts_preview_proc: subprocess.Popen[bytes] | None = None
        self._tts_preview_busy = False

        # Chrome da aplicação: a navegação continua usando o Notebook interno,
        # mas a pessoa vê workspaces com título, contexto e estado próprios.
        self._workspace_meta = (
            (
                "Cortes Virais",
                "WORKSPACE / CORTES VIRAIS",
                "Transforme vídeos longos em cortes que merecem parar o scroll.",
                "PIPELINE IA",
                _IC_VIDEO,
            ),
            (
                "Máquina de Quizzes",
                "WORKSPACE / QUIZZES",
                "Monte vídeos interativos com ritmo, voz e recompensa visual.",
                "GERADOR",
                _IC_QUIZ,
            ),
            (
                "Batalha 1v1",
                "WORKSPACE / BATALHA",
                "Escolha o duelo e deixe a física criar a disputa.",
                "SIMULAÇÃO",
                _IC_BATALHA,
            ),
            (
                "História",
                "WORKSPACE / HISTÓRIA",
                "Da ideia ao vídeo narrado, cena por cena.",
                "STORY MODE",
                _IC_HISTORIA,
            ),
            (
                "Text-to-Speech",
                "WORKSPACE / VOZ",
                "Teste uma locução e gere o áudio que acompanha seu conteúdo.",
                "AUDIO LAB",
                _IC_SPEAKER,
            ),
            (
                "Publicar no YouTube",
                "WORKSPACE / PUBLICAÇÃO",
                "Organize os pares MP4/TXT e agende sua sequência de uploads.",
                "YOUTUBE",
                _IC_YOUTUBE,
            ),
            (
                "Análise de desempenho",
                "WORKSPACE / ANALYTICS",
                "Leia o que performou e transforme sinais em próximos testes.",
                "INSIGHTS",
                _IC_ANALYTICS,
            ),
        )
        self._page_title_var = tk.StringVar(value=self._workspace_meta[0][0])
        self._page_kicker_var = tk.StringVar(value=self._workspace_meta[0][1])
        self._page_description_var = tk.StringVar(value=self._workspace_meta[0][2])
        self._page_badge_var = tk.StringVar(value=self._workspace_meta[0][3])

        self._build_ui()
        self.after(80, self._drain_log_queue)
        self.after(120, self._position_log_pane_sash)

    def _position_log_pane_sash(self) -> None:
        """Garante altura útil do painel de log após o primeiro layout (ttk sem minsize no Linux)."""
        if not self._activity_expanded:
            return
        try:
            pw = self._pw_results_log
            pw.update_idletasks()
            h = int(pw.winfo_height())
            if h < 100:
                self.after(180, self._position_log_pane_sash)
                return
            # Sash 0 = limite entre log (topo) e tabela de resultados
            pw.sashpos(0, max(130, min(int(h * 0.36), h - 260)))
        except (tk.TclError, AttributeError):
            pass

    def _remember_idle(self, w: tk.Widget, state: str) -> None:
        self._idle_states[w] = state

    def _keep_photo(self, img: tk.PhotoImage | None) -> tk.PhotoImage | None:
        if img is not None:
            self._photos.append(img)
        return img

    def _make_surface(
        self,
        parent: tk.Misc,
        *,
        expand: bool = False,
        layout: str = "pack",
        **layout_options: object,
    ) -> ttk.Frame:
        """Cria uma superfície com borda sutil e espaçamento consistente."""
        wrap = tk.Frame(parent, bg=EDGE, highlightthickness=0, bd=0)
        if layout == "grid":
            wrap.grid(**layout_options)
        else:
            wrap.pack(fill=tk.BOTH if expand else tk.X, expand=expand, pady=(0, _PX_NEAR))
        inner = ttk.Frame(
            wrap,
            style=self._theme.card_style,
            padding=(_PX_SECTION, _PX_SECTION, _PX_SECTION, _PX_SECTION - 2),
        )
        # padx/pady=1 deixa 1px do wrap (EDGE) à mostra: borda visível do card.
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        return inner

    def _make_section(
        self,
        parent: tk.Misc,
        title: str,
        icon: str,
        *,
        compact: bool = False,
    ) -> ttk.Frame:
        """Bloco interno: título curto, divisor e corpo; sem caixas aninhadas."""
        block = ttk.Frame(parent, style=self._theme.card_style)
        block.pack(fill=tk.X, pady=(0, _PX_NEAR if compact else _PX_SECTION - 6))
        head = ttk.Frame(block, style=self._theme.card_style)
        head.pack(fill=tk.X, pady=(0, _PX_MICRO))
        tk.Label(
            head,
            text=icon,
            bg=PANEL2,
            fg=MOSS_HI,
            font=(self._ui_family, 9, "bold"),
            padx=7,
            pady=4,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(
            head,
            text=title,
            style="Section.TLabel" if compact else "Heading.TLabel",
        ).pack(side=tk.LEFT, anchor=tk.W)
        if not compact:
            ttk.Separator(block, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, _PX_NEAR))
        body = ttk.Frame(block, style=self._theme.card_style)
        body.pack(fill=tk.BOTH, expand=True)
        return body

    def _on_opacity_scale(self, value: str | float) -> None:
        try:
            v = int(float(value))
        except (TypeError, ValueError):
            return
        v = max(0, min(100, v))
        if self._opacity.get() != v:
            self._opacity.set(v)
        self._sync_opacity_label()

    def _sync_opacity_label(self) -> None:
        if hasattr(self, "_lbl_opacity_pct"):
            self._lbl_opacity_pct.configure(text=f"{int(self._opacity.get())}%")

    def _urls_help_dialog(self) -> None:
        messagebox.showinfo("URLs e yt-dlp", _URLS_HELP_TEXT.strip(), parent=self)

    def _urls_focus_in(self, _event: tk.Event | None = None) -> None:
        if not getattr(self, "_urls_ph_visible", False):
            return
        self._txt_urls.delete("1.0", tk.END)
        self._urls_ph_visible = False
        self._txt_urls.configure(foreground=self._text_fg)

    def _urls_focus_out(self, _event: tk.Event | None = None) -> None:
        raw = self._txt_urls.get("1.0", "end-1c").strip()
        if raw:
            return
        self._txt_urls.insert("1.0", _URLS_PLACEHOLDER)
        self._urls_ph_visible = True
        self._txt_urls.configure(foreground=self._hint_fg)

    def _urls_text_for_pipeline(self) -> str:
        """
        Texto enviado ao yt-dlp. Não confiar só em `_urls_ph_visible`: no Linux é comum colar
        a URL sem o widget receber FocusIn antes — o flag continua True e o pipeline via "".
        """
        full = self._txt_urls.get("1.0", tk.END)
        body = full.strip()
        ph = _URLS_PLACEHOLDER.strip()
        if not body or body == ph:
            return ""
        return full

    def _set_status(self, text: str) -> None:
        self._status_var.set(text)

    def _toggle_secondary_nav(self) -> None:
        """Mostra ou recolhe as ferramentas que não fazem parte do fluxo principal."""
        wrap = self._secondary_nav_wrap
        toggle = self._secondary_nav_toggle
        if wrap is None or toggle is None:
            return
        self._secondary_nav_expanded = not self._secondary_nav_expanded
        if self._secondary_nav_expanded:
            wrap.pack(fill=tk.X, pady=(0, _PX_NEAR))
            toggle.configure(text="−  Outras ferramentas")
        else:
            wrap.pack_forget()
            toggle.configure(text="+  Outras ferramentas")
        self.after_idle(lambda: self._move_nav_indicator(self._notebook.index(self._notebook.select()), animate=False))

    def _ensure_secondary_nav_visible(self) -> None:
        if not self._secondary_nav_expanded:
            self._toggle_secondary_nav()

    def _sync_cut_action_bar(self, index: int) -> None:
        """Mantém a ação principal fixa somente no workspace de cortes."""
        bar = self._cut_action_bar
        if bar is None or not hasattr(self, "_notebook"):
            return
        if index == 0:
            if not bar.winfo_ismapped():
                before = self._workspace_area
                if before is not None:
                    bar.pack(fill=tk.X, pady=(0, _PX_NEAR), before=before)
                else:
                    bar.pack(fill=tk.X, pady=(0, _PX_NEAR))
        else:
            bar.pack_forget()

        self._sync_cut_action_controls()

    def _sync_cut_action_controls(self) -> None:
        run_button = getattr(self, "_btn_run_cortes", None)
        cancel_button = getattr(self, "_btn_cancel_cortes", None)
        if run_button is None or cancel_button is None:
            return
        try:
            if self._pipeline_running:
                run_button.pack_forget()
                if not cancel_button.winfo_ismapped():
                    cancel_button.pack(side=tk.LEFT, padx=(0, _PX_MICRO))
            else:
                cancel_button.pack_forget()
                if not run_button.winfo_ismapped():
                    run_button.pack(side=tk.LEFT)
        except tk.TclError:
            pass

    def _sync_run_surface(self, index: int) -> None:
        """Libera a altura da tela principal; outras telas mantêm os controles globais."""
        outer = self._run_surface_outer
        if outer is None:
            return
        if index == 0:
            outer.grid_forget()
        else:
            outer.grid(row=1, column=0, sticky=tk.EW, pady=(0, _PX_NEAR))

    def _toggle_activity_panel(self, expanded: bool | None = None) -> None:
        """Abre o log/resultados sob demanda para não roubar espaço do formulário."""
        outer = self._activity_surface_outer
        if outer is None:
            return
        if expanded is None:
            expanded = not self._activity_expanded
        self._activity_expanded = bool(expanded)
        area = self._workspace_area
        if self._activity_expanded:
            if area is not None:
                outer.configure(height=260)
                # A superfície usa pack para o card interno, então é o
                # pack_propagate que precisa ser desligado para respeitar o
                # tamanho compacto do painel de atividade.
                outer.pack_propagate(False)
                area.rowconfigure(2, minsize=260)
                outer.grid(row=2, column=0, sticky=tk.NSEW, pady=(0, _PX_NEAR))
                self.after(60, self._position_log_pane_sash)
            else:
                outer.pack(fill=tk.BOTH, expand=True, pady=(0, _PX_NEAR))
            if hasattr(self, "_btn_activity_toggle"):
                self._btn_activity_toggle.configure(text="Ocultar atividade")
            if hasattr(self, "_btn_cut_activity"):
                self._btn_cut_activity.configure(text="Ocultar atividade")
        else:
            if area is not None:
                outer.grid_forget()
                area.rowconfigure(2, minsize=0)
            else:
                outer.pack_forget()
            if hasattr(self, "_btn_activity_toggle"):
                self._btn_activity_toggle.configure(text="Mostrar atividade")
            if hasattr(self, "_btn_cut_activity"):
                self._btn_cut_activity.configure(text="Atividade")

    def _select_sidebar_tab(self, index: int) -> None:
        if index > 0:
            self._ensure_secondary_nav_visible()
        current = self._notebook.index(self._notebook.select())
        self._notebook.select(index)
        if current == index:
            # Re-clicar no workspace ativo também executa a transição de entrada.
            self._on_notebook_tab_changed()

    def _on_notebook_tab_changed(self, _event: tk.Event | None = None) -> None:
        try:
            idx = self._notebook.index(self._notebook.select())
        except (tk.TclError, ValueError):
            return
        for i, btn in enumerate(self._sidebar_buttons):
            btn.configure(style="SidebarActive.TButton" if i == idx else "Sidebar.TButton")
        if idx > 0:
            self._ensure_secondary_nav_visible()
        self._sync_cut_action_bar(idx)
        self._sync_run_surface(idx)
        self._move_nav_indicator(idx)
        self._transition_to_page(idx)

    def _transition_to_page(self, index: int) -> None:
        """Troca de workspace com wipe + cabeçalho em stagger (sem recriar telas)."""
        self._update_page_chrome(index, animate=True)
        self._animate_page_underline()
        if 0 <= index < len(self._tab_canvases):
            try:
                canvas = self._tab_canvases[index]
                if canvas is not None:
                    canvas.yview_moveto(0.0)
            except tk.TclError:
                pass
        self._wipe_page_cover()

    def _wipe_page_cover(self) -> None:
        """Cortina sólida revela o workspace da esquerda para a direita."""
        cover = self._page_cover
        if cover is None or not hasattr(self, "_notebook"):
            return
        self.update_idletasks()
        width = max(self._notebook.winfo_width(), 640)
        cover.place(in_=self._notebook, x=0, y=0, width=width, relheight=1.0)
        cover.lift()

        def step(t: float) -> None:
            cover.place_configure(x=int(lerp(0, width + 32, t)))

        self._motion.tween("page.wipe", 320, step, ease=ease_out_quint, done=cover.place_forget)

    def _update_page_chrome(self, index: int, *, animate: bool = False) -> None:
        """Atualiza o cabeçalho contextual sem recriar nenhuma tela ou callback."""
        if not 0 <= index < len(self._workspace_meta):
            return
        title, kicker, description, badge, _icon = self._workspace_meta[index]
        self._page_title_var.set(title)
        self._page_kicker_var.set(kicker)
        self._page_description_var.set(description)
        self._page_badge_var.set(badge)
        if not animate or not self._page_chrome_labels:
            return
        for i, (label, final_fg, base_y) in enumerate(self._page_chrome_labels):
            try:
                label.configure(foreground=INK)
                label.place_configure(y=base_y + 14)
            except tk.TclError:
                continue
            self._motion.tween(
                f"chrome.{i}",
                280,
                lambda t, lbl=label, fg=final_fg, y=base_y: self._chrome_reveal_step(lbl, fg, y, t),
                delay_ms=40 + i * 60,
                ease=ease_out_cubic,
            )
        if self._page_badge_lbl is not None:
            try:
                self._page_badge_lbl.configure(foreground=INK)
            except tk.TclError:
                pass
            else:
                self._motion.tween(
                    "chrome.badge",
                    300,
                    lambda t: self._page_badge_lbl.configure(
                        foreground=lerp_color(INK, MUTED, t)
                    ),
                    delay_ms=120,
                    ease=ease_out_cubic,
                )

    @staticmethod
    def _chrome_reveal_step(label: ttk.Label, final_fg: str, base_y: int, t: float) -> None:
        label.configure(foreground=lerp_color(INK, final_fg, t))
        label.place_configure(y=int(lerp(base_y + 14, base_y, t)))

    def _move_nav_indicator(self, index: int, *, animate: bool = True) -> None:
        """Desliza a barra de acento do sidebar até o workspace ativo."""
        bar = self._nav_indicator
        if bar is None or not 0 <= index < len(self._sidebar_buttons):
            return
        btn = self._sidebar_buttons[index]
        try:
            self.update_idletasks()
            target_y = max(0, btn.winfo_y() + 7)
            target_h = max(12, btn.winfo_height() - 14)
        except tk.TclError:
            return
        if not animate or not bar.winfo_ismapped():
            bar.place(x=4, y=target_y, width=3, height=target_h)
            return
        start_y = bar.winfo_y()
        start_h = max(1, bar.winfo_height())

        def step(t: float) -> None:
            bar.place(
                x=4,
                y=max(0, int(lerp(start_y, target_y, t))),
                width=3,
                height=max(4, int(lerp(start_h, target_h, t))),
            )

        bar.configure(bg=MOSS_HI)
        self._motion.tween("nav.indicator", 340, step, ease=ease_out_back)
        self._motion.tween(
            "nav.glow",
            380,
            lambda t: bar.configure(bg=lerp_color(MOSS_HI, MOSS, t)),
            ease=ease_out_cubic,
        )

    def _make_scrollable_tab(self, notebook: ttk.Notebook) -> tuple[ttk.Frame, ttk.Frame]:
        """Cria uma aba com rolagem para manter ações e resultados sempre acessíveis."""
        outer = ttk.Frame(notebook, style="Content.TFrame")
        canvas = tk.Canvas(
            outer,
            bg=INK,
            highlightthickness=0,
            bd=0,
            yscrollincrement=12,
        )
        self._tab_canvases.append(canvas)
        scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas, style="Content.TFrame", padding=(0, 0, 0, _PX_NEAR))
        window_id = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _sync_scroll_region(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_inner_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=max(1, event.width))

        inner.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _sync_inner_width)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._wheel_targets.append((canvas, inner))
        return outer, inner

    def _bind_tab_wheel(self, canvas: tk.Canvas, root_widget: tk.Misc) -> None:
        """Rolagem por scroll do mouse na área do workspace (menos em campos roláveis)."""

        def scroll(event: tk.Event) -> str | None:
            if event.num == 5 or event.delta < 0:
                canvas.yview_scroll(3, "units")
            elif event.num == 4 or event.delta > 0:
                canvas.yview_scroll(-3, "units")
            else:
                return None
            return "break"

        skip = (tk.Text, tk.Entry, tk.Listbox, ttk.Entry, ttk.Combobox, ttk.Spinbox, ttk.Treeview)
        stack: list[tk.Misc] = [root_widget, canvas]
        while stack:
            widget = stack.pop()
            if not isinstance(widget, skip):
                widget.bind("<Button-4>", scroll)
                widget.bind("<Button-5>", scroll)
                widget.bind("<MouseWheel>", scroll)
            stack.extend(widget.winfo_children())

    def _build_ui(self) -> None:
        sec = self._theme.secondary_button_style
        cb_st = self._theme.checkbutton_style
        base = tkfont.nametofont("TkDefaultFont").actual()

        # O chrome do produto é deliberadamente construído com superfícies sólidas:
        # fica nítido em monitores diferentes, não depende de assets rasterizados e
        # mantém a interface rápida mesmo durante renderizações pesadas.
        shell = tk.Frame(self, bg=INK, highlightthickness=0, bd=0)
        shell.place(x=0, y=0, relwidth=1, relheight=1)

        # --- Top bar ---
        header = tk.Frame(shell, height=84, bg=INK2, highlightthickness=0, bd=0)
        header.pack(side=tk.TOP, fill=tk.X)
        header.pack_propagate(False)

        brand_row = tk.Frame(header, bg=INK2)
        brand_row.pack(side=tk.LEFT, padx=28, pady=13)
        brand_mark = tk.Canvas(
            brand_row, width=44, height=44, bg=INK2, highlightthickness=0, bd=0
        )
        brand_mark.pack(side=tk.LEFT, padx=(0, 14))
        self._paint_brand_mark(brand_mark, base["family"])
        brand_txt = tk.Frame(brand_row, bg=INK2)
        brand_txt.pack(side=tk.LEFT)
        tk.Label(
            brand_txt,
            text="CORTES LAB",
            bg=INK2,
            fg=TEXT,
            font=(base["family"], 14, "bold"),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        tk.Label(
            brand_txt,
            text="creative automation studio",
            bg=INK2,
            fg=MUTED_SOFT,
            font=(base["family"], 8),
            anchor=tk.W,
        ).pack(anchor=tk.W)

        header_divider = tk.Frame(header, bg=EDGE, width=1)
        header_divider.pack(side=tk.LEFT, fill=tk.Y, pady=18, padx=(4, 22))
        tk.Label(
            header,
            text="LOCAL-FIRST  /  AI VIDEO WORKFLOW",
            bg=INK2,
            fg=MUTED_SOFT,
            font=(base["family"], 8, "bold"),
            anchor=tk.W,
        ).pack(side=tk.LEFT)

        header_actions = tk.Frame(header, bg=INK2)
        header_actions.pack(side=tk.RIGHT, padx=24)
        self._clock_var = tk.StringVar(value=datetime.now().strftime("%H:%M"))
        tk.Label(
            header_actions,
            textvariable=self._clock_var,
            bg=INK2,
            fg=MUTED,
            font=(base["family"], 11, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(header_actions, text="●  SISTEMA PRONTO", style="ChipAccent.TLabel").pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Button(
            header_actions,
            text=f"{_IC_FOLDER}  Abrir resultados",
            command=lambda: _open_folder(OUTPUT_DIR),
            style="Header.TButton",
        ).pack(side=tk.LEFT)
        self._topbar = tk.Canvas(header, height=2, bg=INK2, highlightthickness=0, bd=0)
        self._topbar.place(x=0, y=0, relwidth=1)
        self._topbar.bind("<Configure>", self._paint_top_gradient)

        # --- Body ---
        body = tk.Frame(shell, bg=INK, highlightthickness=0, bd=0)
        body.pack(fill=tk.BOTH, expand=True)

        # --- Navegação lateral ---
        sidebar = tk.Frame(body, width=252, bg=INK2, highlightthickness=0, bd=0)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        nav_wrap = tk.Frame(sidebar, bg=INK2)
        nav_wrap.pack(fill=tk.X, pady=(26, 0))
        tk.Label(
            nav_wrap,
            text="FLUXO PRINCIPAL",
            bg=INK2,
            fg=MUTED_SOFT,
            font=(base["family"], 8, "bold"),
            anchor=tk.W,
            padx=26,
        ).pack(fill=tk.X, pady=(0, 12))
        tk.Frame(nav_wrap, bg=EDGE, height=1).pack(fill=tk.X, padx=26, pady=(0, 10))

        # O gerador de cortes é a tela de trabalho do produto. As demais
        # ferramentas continuam disponíveis, mas não competem visualmente com
        # o fluxo que será usado na maioria das vezes.
        primary_label = self._workspace_meta[0][0]
        primary_icon = self._workspace_meta[0][4]
        primary_btn = ttk.Button(
            nav_wrap,
            text=f"{primary_icon}    {primary_label}",
            command=lambda: self._select_sidebar_tab(0),
            style="SidebarActive.TButton",
        )
        primary_btn.pack(fill=tk.X, padx=12, pady=2)
        self._sidebar_buttons.append(primary_btn)

        self._secondary_nav_toggle = ttk.Button(
            nav_wrap,
            text="+  Outras ferramentas",
            command=self._toggle_secondary_nav,
            style="Sidebar.TButton",
        )
        self._secondary_nav_toggle.pack(fill=tk.X, padx=12, pady=(16, 2))

        self._secondary_nav_wrap = tk.Frame(nav_wrap, bg=INK2)
        for idx, (label, _kicker, _description, _badge, icon) in enumerate(self._workspace_meta[1:], 1):
            btn = ttk.Button(
                self._secondary_nav_wrap,
                text=f"{icon}    {label}",
                command=lambda i=idx: self._select_sidebar_tab(i),
                style="Sidebar.TButton",
            )
            btn.pack(fill=tk.X, padx=12, pady=2)
            self._sidebar_buttons.append(btn)

        # Barra de acento que desliza até o workspace ativo (via place, por cima dos botões).
        self._nav_indicator = tk.Frame(nav_wrap, bg=MOSS, highlightthickness=0, bd=0)
        self.after_idle(lambda: self._move_nav_indicator(0, animate=False))

        tk.Frame(sidebar, bg=INK2).pack(fill=tk.BOTH, expand=True)
        bottom_wrap = tk.Frame(
            sidebar,
            bg=PANEL,
            highlightthickness=1,
            highlightbackground=EDGE,
            padx=14,
            pady=14,
        )
        bottom_wrap.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(
            bottom_wrap,
            text="RUN SPACE",
            bg=PANEL,
            fg=AQUA,
            font=(base["family"], 8, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X)
        tk.Label(
            bottom_wrap,
            text="Seu pipeline local está pronto.",
            bg=PANEL,
            fg=MUTED,
            font=(base["family"], 8),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(4, 12))
        ttk.Button(
            bottom_wrap,
            text="Abrir pasta de resultados",
            command=lambda: _open_folder(OUTPUT_DIR),
            style="Ghost.TButton",
        ).pack(fill=tk.X, pady=(0, _PX_MICRO))
        self._btn_send_telegram = ttk.Button(
            bottom_wrap,
            text="Enviar pacote para Telegram",
            command=self._send_all_to_telegram_clicked,
            style="AltAccent.TButton",
        )
        self._btn_send_telegram.pack(fill=tk.X, pady=(0, _PX_MICRO))
        ttk.Button(
            bottom_wrap,
            text="Limpar atividade",
            command=self._clear_log,
            style="Header.TButton",
        ).pack(fill=tk.X)

        # --- Workspace ---
        content_outer = tk.Frame(body, bg=INK, highlightthickness=0, bd=0)
        content_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = tk.Frame(content_outer, bg=INK, padx=30, pady=0, highlightthickness=0)
        content.pack(fill=tk.BOTH, expand=True)

        page_chrome = tk.Frame(content, bg=INK, height=92, highlightthickness=0)
        page_chrome.pack(fill=tk.X, pady=(16, 10))
        page_chrome.pack_propagate(False)
        page_main = tk.Frame(page_chrome, bg=INK)
        page_main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Labels posicionados com place para animar fade + subida a cada troca de página.
        kicker_lbl = ttk.Label(
            page_main, textvariable=self._page_kicker_var, style="Eyebrow.TLabel"
        )
        title_lbl = ttk.Label(page_main, textvariable=self._page_title_var, style="PageTitle.TLabel")
        desc_lbl = ttk.Label(
            page_main, textvariable=self._page_description_var, style="PageSubtitle.TLabel"
        )
        kicker_lbl.place(x=0, y=2, anchor=tk.NW)
        title_lbl.place(x=0, y=24, anchor=tk.NW)
        desc_lbl.place(x=0, y=62, anchor=tk.NW)
        self._page_chrome_labels = [
            (kicker_lbl, AQUA, 2),
            (title_lbl, TEXT, 24),
            (desc_lbl, MUTED, 62),
        ]
        for lbl, _fg, _y in self._page_chrome_labels:
            lbl.configure(foreground=INK)  # entrada revela o texto (sem flash)
        page_badge = tk.Frame(page_chrome, bg=INK, padx=4)
        page_badge.pack(side=tk.RIGHT, anchor=tk.N, pady=12)
        self._page_badge_lbl = ttk.Label(
            page_badge, textvariable=self._page_badge_var, style="Chip.TLabel"
        )
        self._page_badge_lbl.pack()
        # Underline gradiente que cresce a cada troca de workspace.
        self._page_underline = tk.Canvas(content, height=3, bg=INK, highlightthickness=0, bd=0)
        self._page_underline.pack(fill=tk.X, pady=(0, 10))

        # Ação principal fixa do produto. Ela fica fora do canvas de cada
        # workspace para que "Gerar clipes" nunca desapareça com a rolagem.
        self._cut_action_bar = tk.Frame(
            content,
            bg=EDGE,
            highlightthickness=0,
            bd=0,
        )
        self._cut_action_bar.pack(fill=tk.X, pady=(0, _PX_NEAR))
        cut_action_inner = ttk.Frame(
            self._cut_action_bar,
            style=self._theme.card_style,
            padding=(16, 10),
        )
        cut_action_inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        cut_action_inner.columnconfigure(0, weight=1)
        cut_action_copy = ttk.Frame(cut_action_inner, style=self._theme.card_style)
        cut_action_copy.grid(row=0, column=0, sticky=tk.EW)
        tk.Label(
            cut_action_copy,
            text="GERADOR PRINCIPAL",
            bg=PANEL,
            fg=AQUA,
            font=(self._ui_family, 8, "bold"),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        ttk.Label(
            cut_action_copy,
            text="Tudo pronto para transformar vídeo longo em cortes.",
            style="Subheading.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))
        cut_action_options = ttk.Frame(cut_action_copy, style=self._theme.card_style)
        cut_action_options.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(cut_action_options, text="Ao terminar:", style="Muted.TLabel").pack(
            side=tk.LEFT, padx=(0, 4)
        )
        for option_text, option_var in (
            ("Abrir pasta", self._open_results_when_done),
            ("Notificar", self._notify_when_done),
            ("Gerar .zip", self._zip_when_done),
        ):
            ttk.Checkbutton(
                cut_action_options,
                text=option_text,
                variable=option_var,
                style=cb_st,
            ).pack(side=tk.LEFT, padx=(0, 4))
        cut_action_buttons = ttk.Frame(cut_action_inner, style=self._theme.card_style)
        cut_action_buttons.grid(row=0, column=1, sticky=tk.E)
        self._btn_cut_activity = ttk.Button(
            cut_action_buttons,
            text="Atividade",
            command=self._toggle_activity_panel,
            style="Header.TButton",
        )
        self._btn_cut_activity.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        self._btn_cancel_cortes = ttk.Button(
            cut_action_buttons,
            text="Cancelar",
            command=self._cancel_pipeline,
            style="Danger.TButton",
        )
        self._btn_run_cortes = ttk.Button(
            cut_action_buttons,
            text=f"{_IC_RUN}  Gerar clipes",
            command=self._start_cortes_job,
            style="Accent.TButton",
        )
        self._remember_idle(self._btn_run_cortes, "normal")
        self._sync_cut_action_controls()

        self._workspace_area = tk.Frame(content, bg=INK, highlightthickness=0, bd=0)
        self._workspace_area.pack(fill=tk.BOTH, expand=True)
        self._workspace_area.columnconfigure(0, weight=1)
        self._workspace_area.rowconfigure(0, weight=1)
        self._notebook = ttk.Notebook(self._workspace_area, style="Hidden.TNotebook")
        self._notebook.grid(row=0, column=0, sticky=tk.NSEW)

        # A tela mais usada mantém a ação e o núcleo sempre no topo; a rolagem
        # fica disponível apenas para os recursos opcionais que vêm depois.
        tab_cortes, tab_cortes_body = self._make_scrollable_tab(self._notebook)
        tab_quiz, tab_quiz_body = self._make_scrollable_tab(self._notebook)
        tab_batalha, tab_batalha_body = self._make_scrollable_tab(self._notebook)
        tab_historia, tab_historia_body = self._make_scrollable_tab(self._notebook)
        tab_tts, tab_tts_body = self._make_scrollable_tab(self._notebook)
        tab_youtube, tab_youtube_body = self._make_scrollable_tab(self._notebook)
        tab_performance, tab_performance_body = self._make_scrollable_tab(self._notebook)
        self._notebook.add(tab_cortes, text="Cortes Virais")
        self._notebook.add(tab_quiz, text="Quiz")
        self._notebook.add(tab_batalha, text="Batalha")
        self._notebook.add(tab_historia, text="História")
        self._notebook.add(tab_tts, text="TTS")
        self._notebook.add(tab_youtube, text="YouTube")
        self._notebook.add(tab_performance, text="Desempenho")

        self._build_tab_cortes(tab_cortes_body, sec=sec, base=base)
        self._build_tab_quiz(tab_quiz_body, sec=sec, base=base)
        self._build_tab_batalha(tab_batalha_body, sec=sec, base=base)
        self._build_tab_historia(tab_historia_body, sec=sec, base=base)
        self._build_tab_tts(tab_tts_body, sec=sec, base=base)
        self._build_tab_youtube(tab_youtube_body, sec=sec, base=base)
        self._build_tab_performance(tab_performance_body, sec=sec, base=base)

        for canvas, inner in self._wheel_targets:
            self._bind_tab_wheel(canvas, inner)

        # Cortina usada nas transições de página (revela o workspace com wipe).
        self._page_cover = tk.Frame(content, bg=INK, highlightthickness=0, bd=0)
        tk.Frame(self._page_cover, bg=MOSS, width=2).place(x=0, y=0, relheight=1.0)

        self._notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        run_surface = self._make_surface(
            self._workspace_area,
            layout="grid",
            row=1,
            column=0,
            sticky=tk.EW,
            pady=(0, _PX_NEAR),
        )
        self._run_surface_outer = run_surface.master
        run_head = self._make_section(run_surface, "Execução e pós-processo", _IC_RUN, compact=True)
        f_primary = ttk.Frame(run_head, style=self._theme.card_style)
        f_primary.pack(fill=tk.X, pady=(0, _PX_MICRO))
        self._btn_cancel = ttk.Button(
            f_primary,
            text="Cancelar processamento",
            command=self._cancel_pipeline,
            state=tk.DISABLED,
            style="Danger.TButton",
        )
        self._btn_cancel.pack(side=tk.LEFT)
        f_opts = ttk.Frame(f_primary, style=self._theme.card_style)
        f_opts.pack(side=tk.LEFT, padx=(_PX_SECTION, 0))
        ttk.Checkbutton(
            f_opts,
            text="Abrir resultados",
            variable=self._open_results_when_done,
            style=cb_st,
        ).pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        ttk.Checkbutton(
            f_opts,
            text="Notificar",
            variable=self._notify_when_done,
            style=cb_st,
        ).pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        ttk.Checkbutton(
            f_opts,
            text="Exportar .zip",
            variable=self._zip_when_done,
            style=cb_st,
        ).pack(side=tk.LEFT)

        self._btn_activity_toggle = ttk.Button(
            f_primary,
            text="Mostrar atividade",
            command=self._toggle_activity_panel,
            style="Header.TButton",
        )
        self._btn_activity_toggle.pack(side=tk.RIGHT)

        prog_wrap = ttk.Frame(run_head, style=self._theme.card_style)
        prog_wrap.pack(fill=tk.X, pady=(_PX_MICRO, 0))
        self._prog_track = tk.Frame(
            prog_wrap,
            height=8,
            bg=PANEL2,
            highlightthickness=1,
            highlightbackground=EDGE,
        )
        self._prog_track.pack(fill=tk.X)
        self._prog_fill = tk.Frame(self._prog_track, height=8, bg=MOSS, highlightthickness=0)

        results_surface = self._make_surface(
            self._workspace_area,
            expand=True,
            layout="grid",
            row=2,
            column=0,
            sticky=tk.NSEW,
            pady=(0, _PX_NEAR),
        )
        self._activity_surface_outer = results_surface.master
        self._activity_surface_outer.grid_forget()
        self._pw_results_log = ttk.Panedwindow(results_surface, orient=tk.VERTICAL)
        self._pw_results_log.pack(fill=tk.BOTH, expand=True)

        lf = ttk.Frame(
            self._pw_results_log, style=self._theme.card_style, padding=(0, 0, 0, _PX_NEAR)
        )
        f_last = ttk.Frame(
            self._pw_results_log, style=self._theme.card_style, padding=(0, _PX_NEAR, 0, 0)
        )
        try:
            self._pw_results_log.add(lf, weight=2)
            self._pw_results_log.add(f_last, weight=3)
        except tk.TclError:
            self._pw_results_log.add(lf)
            self._pw_results_log.add(f_last)

        for pane, title, icon in (
            (lf, "Log", _IC_LOG),
            (f_last, "Resultados", _IC_CLIPBOARD),
        ):
            ph = ttk.Frame(pane, style=self._theme.card_style)
            ph.pack(fill=tk.X, pady=(0, _PX_MICRO))
            ttk.Label(ph, text=f"{icon}  {title}", style="Section.TLabel").pack(anchor=tk.W)

        log_row = ttk.Frame(lf, style=self._theme.card_style)
        log_row.pack(fill=tk.BOTH, expand=True)
        self._log = tk.Text(
            log_row,
            height=8,
            wrap=tk.WORD,
            font=tkfont.nametofont("TkFixedFont"),
            state=tk.NORMAL,
            relief=tk.FLAT,
            borderwidth=0,
            padx=14,
            pady=12,
            highlightthickness=1,
            highlightbackground=EDGE,
            highlightcolor=MOSS,
        )
        sy = ttk.Scrollbar(log_row, orient=tk.VERTICAL, command=self._log.yview)
        self._log.configure(yscrollcommand=sy.set)
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, _PX_MICRO))
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.insert(
            tk.END,
            "CORTES LAB  /  atividade do workspace\n"
            "Downloads, transcrição, IA e FFmpeg aparecem aqui em tempo real.\n"
            "Escolha um workspace e inicie uma ação para acompanhar o pipeline.\n\n",
        )
        self._log.tag_configure("error", foreground="#E06A5C")
        self._log.configure(state=tk.DISABLED)

        tree_row = ttk.Frame(f_last, style=self._theme.card_style)
        tree_row.pack(fill=tk.BOTH, expand=True)
        self._tree = ttk.Treeview(
            tree_row,
            columns=("dur",),
            show="tree headings",
            height=6,
            selectmode=tk.BROWSE,
        )
        self._tree.heading("#0", text="Arquivo", anchor=tk.W)
        self._tree.heading("dur", text="Duração", anchor=tk.E)
        self._tree.column("#0", width=560, stretch=True, anchor=tk.W)
        self._tree.column("dur", width=100, stretch=False, anchor=tk.E)
        sy_t = ttk.Scrollbar(tree_row, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sy_t.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, _PX_MICRO))
        sy_t.pack(side=tk.RIGHT, fill=tk.Y)

        f_btns = ttk.Frame(f_last, style=self._theme.card_style)
        f_btns.pack(fill=tk.X, pady=(_PX_SECTION, 0))
        r_a = ttk.Frame(f_btns, style=self._theme.card_style)
        r_a.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Button(
            r_a,
            text="Copiar legenda (selecionado)",
            command=self._copy_caption_selected,
            style=sec,
        ).pack(side=tk.LEFT, padx=(0, _PX_NEAR), pady=_PX_MICRO)
        ttk.Button(
            r_a,
            text="Copiar caminho (selecionado)",
            command=self._copy_path_selected,
            style=sec,
        ).pack(side=tk.LEFT, padx=(0, _PX_NEAR), pady=_PX_MICRO)

        ttk.Button(
            r_a,
            text=f"{_IC_VIDEO}  Postar no TikTok",
            command=self._post_to_tiktok_selected,
            style="AltAccent.TButton",
        ).pack(side=tk.LEFT, padx=(0, _PX_NEAR), pady=_PX_MICRO)

        r_b = ttk.Frame(f_btns, style=self._theme.card_style)
        r_b.pack(fill=tk.X)
        ttk.Button(
            r_b,
            text="Copiar todas as legendas",
            command=self._copy_all_captions,
            style=sec,
        ).pack(side=tk.LEFT, padx=(0, _PX_NEAR), pady=_PX_MICRO)
        ttk.Button(
            r_b,
            text="Exportar .zip do pacote",
            command=self._export_zip_clicked,
            style=sec,
        ).pack(side=tk.LEFT, padx=(0, _PX_NEAR), pady=_PX_MICRO)

        status_bar = ttk.Frame(shell, style="Status.TFrame")
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self._status_dot = tk.Label(
            status_bar,
            text="●",
            bg=STATUS_BG,
            fg=SUCCESS,
            font=(base["family"], 9, "bold"),
            padx=5,
        )
        self._status_dot.pack(side=tk.LEFT)
        ttk.Label(
            status_bar,
            textvariable=self._status_var,
            style="Status.TLabel",
            padding=(0, _PX_MICRO + 2),
        ).pack(side=tk.LEFT)
        tk.Label(
            status_bar,
            text="LOCAL-FIRST  ·  CACHE  ·  GPU READY",
            bg=STATUS_BG,
            fg=MUTED_SOFT,
            font=(base["family"], 8, "bold"),
            padx=24,
        ).pack(side=tk.RIGHT)

        self._sync_run_surface(0)
        self._sync_cut_action_controls()
        self._apply_text_widget_theme()
        if self._urls_ph_visible:
            self._txt_urls.configure(foreground=self._hint_fg)
        self._toggle_tts()
        # Entrada do app: primeiro workspace revelado com a mesma transição das trocas.
        self.after(90, lambda: self._transition_to_page(0))
        self._tick_clock()

    def _paint_brand_mark(self, canvas: tk.Canvas, family: str, size: int = 44) -> None:
        r = 13
        pts = [
            r, 1, size - r, 1,
            size - 1, 1, size - 1, r,
            size - 1, size - r, size - 1, size - 1,
            size - r, size - 1, r, size - 1,
            1, size - 1, 1, size - r,
            1, r, 1, 1,
        ]
        canvas.create_polygon(pts, smooth=True, fill=MOSS, outline=MOSS_HI)
        canvas.create_text(
            size / 2, size / 2 + 1, text="✦", fill="#FFFFFF", font=(family, 16, "bold")
        )

    def _tick_clock(self) -> None:
        if hasattr(self, "_clock_var"):
            self._clock_var.set(datetime.now().strftime("%H:%M"))
        self.after(15000, self._tick_clock)

    def _animate_page_underline(self) -> None:
        self._motion.tween("page.underline", 460, self._paint_page_underline, ease=ease_out_cubic)

    def _paint_page_underline(self, reveal: float) -> None:
        canvas = getattr(self, "_page_underline", None)
        if canvas is None:
            return
        w = canvas.winfo_width()
        if w <= 1:
            canvas.update_idletasks()
            w = canvas.winfo_width()
        if w <= 1:
            return
        canvas.delete("all")
        canvas.create_rectangle(0, 2, w, 3, fill="#1B2233", outline="")
        reveal_w = int(w * max(0.0, min(1.0, reveal)))
        n = max(1, reveal_w // 6)
        seg = reveal_w / n
        for i in range(n):
            color = sample_gradient((MOSS_DK, MOSS, AQUA), i / max(1, n - 1))
            canvas.create_rectangle(i * seg, 0, (i + 1) * seg + 1, 2, fill=color, outline="")

    def _paint_top_gradient(self, event: tk.Event | None = None) -> None:
        canvas = self._topbar
        if canvas is None:
            return
        w = event.width if event is not None else canvas.winfo_width()
        canvas.delete("all")
        if w <= 1:
            return
        n = max(2, w // 6)
        seg = w / n
        for i in range(n):
            color = sample_gradient((MOSS_DK, MOSS, AQUA), i / max(1, n - 1))
            canvas.create_rectangle(i * seg, 0, (i + 1) * seg + 1, 2, fill=color, outline="")

    def _on_root_configure(self, event: tk.Event | None = None) -> None:
        """Hook mantido para compatibilidade com integrações antigas da GUI."""
        return

    def _build_tab_cortes(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Workspace principal: fonte, estilo e recursos essenciais no mesmo enquadramento."""
        cb_st = self._theme.checkbutton_style

        body = ttk.Frame(parent, style="Content.TFrame", padding=(0, 0, 0, 0))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=3, uniform="cutcards")
        body.columnconfigure(1, weight=2, uniform="cutcards")
        # Preserve the natural height of the core cards. Em telas menores, os
        # recursos opcionais ficam abaixo deles; fonte e estilo nunca são
        # esmagados para abrir espaço para os extras.
        body.rowconfigure(0, weight=0)

        def make_card(parent_: tk.Misc) -> ttk.Frame:
            wrap = tk.Frame(parent_, bg=EDGE, highlightthickness=0, bd=0)
            inner = ttk.Frame(
                wrap,
                style=self._theme.card_style,
                padding=(18, 14, 18, 12),
            )
            inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
            return inner

        def card_heading(parent_: ttk.Frame, title: str, icon: str) -> None:
            row_ = ttk.Frame(parent_, style=self._theme.card_style)
            row_.pack(fill=tk.X, pady=(0, 10))
            tk.Label(
                row_,
                text=icon,
                bg=PANEL2,
                fg=MOSS_HI,
                font=(self._ui_family, 9, "bold"),
                padx=7,
                pady=4,
            ).pack(side=tk.LEFT, padx=(0, 9))
            ttk.Label(row_, text=title, style="Heading.TLabel").pack(side=tk.LEFT, anchor=tk.W)

        source = make_card(body)
        source.master.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, _PX_NEAR))
        card_heading(source, "1  ·  Fonte do vídeo", _IC_VIDEO)

        file_row = ttk.Frame(source, style=self._theme.card_style)
        file_row.pack(fill=tk.X, pady=(0, 12))
        self._btn_pick = ttk.Button(
            file_row,
            text="Escolher vídeo(s)",
            command=self._pick_video,
            style=sec,
        )
        self._btn_pick.pack(side=tk.LEFT)
        self._remember_idle(self._btn_pick, "normal")
        self._lbl_video = ttk.Label(
            file_row,
            textvariable=self._video_path,
            wraplength=360,
            style="Field.TLabel",
        )
        self._lbl_video.pack(side=tk.LEFT, padx=(_PX_NEAR, 0), fill=tk.X, expand=True)

        ttk.Label(source, text="Ou busque pelo tema no YouTube", style="Field.TLabel").pack(
            anchor=tk.W
        )
        self._ent_search_theme = ttk.Entry(source, textvariable=self._search_theme)
        self._ent_search_theme.pack(fill=tk.X, pady=(_PX_MICRO, 2))
        self._remember_idle(self._ent_search_theme, "normal")
        ttk.Label(
            source,
            text="Ex.: Filme odisseia  ·  encontra um vídeo longo e popular automaticamente.",
            style="Muted.TLabel",
            wraplength=380,
        ).pack(anchor=tk.W, pady=(0, 10))

        urls_header = ttk.Frame(source, style=self._theme.card_style)
        urls_header.pack(fill=tk.X, pady=(0, _PX_MICRO))
        ttk.Label(urls_header, text="URLs diretas (opcional)", style="Field.TLabel").pack(
            side=tk.LEFT
        )
        ttk.Button(
            urls_header,
            text="?",
            width=2,
            command=self._urls_help_dialog,
            style=sec,
        ).pack(side=tk.LEFT, padx=(_PX_MICRO, 0))
        fixed_f = tkfont.nametofont("TkFixedFont")
        self._txt_urls = ScrolledText(
            source,
            height=3,
            wrap=tk.WORD,
            font=fixed_f,
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=8,
            highlightthickness=1,
            highlightbackground=EDGE,
            highlightcolor=MOSS,
        )
        self._txt_urls.pack(fill=tk.X)
        self._remember_idle(self._txt_urls, tk.NORMAL)
        self._txt_urls.insert("1.0", _URLS_PLACEHOLDER)
        self._urls_ph_visible = True
        self._txt_urls.bind("<FocusIn>", self._urls_focus_in)
        self._txt_urls.bind("<FocusOut>", self._urls_focus_out)

        style_card = make_card(body)
        style_card.master.grid(row=0, column=1, sticky=tk.NSEW)
        card_heading(style_card, "2  ·  Legenda e estilo", _IC_SETTINGS)

        style_grid = ttk.Frame(style_card, style=self._theme.card_style)
        style_grid.pack(fill=tk.X)
        style_grid.columnconfigure(0, weight=1)
        style_grid.columnconfigure(1, weight=1)

        def field_label(parent_: ttk.Frame, text: str, row: int, column: int, colspan: int = 1) -> None:
            ttk.Label(parent_, text=text, style="Field.TLabel").grid(
                row=row,
                column=column,
                columnspan=colspan,
                sticky=tk.W,
                pady=(0, _PX_MICRO),
            )

        field_label(style_grid, "Idioma", 0, 0)
        field_label(style_grid, "Posição", 0, 1)
        self._cb_lang = ttk.Combobox(
            style_grid,
            textvariable=self._lang,
            values=("pt", "en"),
            state="readonly",
            width=8,
        )
        self._cb_lang.grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 10))
        self._remember_idle(self._cb_lang, "readonly")
        self._cb_position = ttk.Combobox(
            style_grid,
            textvariable=self._position,
            values=("bottom", "top"),
            state="readonly",
            width=10,
        )
        self._cb_position.grid(row=1, column=1, sticky=tk.W, pady=(0, 10))
        self._remember_idle(self._cb_position, "readonly")

        field_label(style_grid, "Fonte", 2, 0, 2)
        self._ent_font = ttk.Entry(style_grid, textvariable=self._font)
        self._ent_font.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))
        self._remember_idle(self._ent_font, "normal")

        field_label(style_grid, "Cor do texto", 4, 0)
        field_label(style_grid, "Fundo", 4, 1)
        colors_row = ttk.Frame(style_grid, style=self._theme.card_style)
        colors_row.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))
        colors_row.columnconfigure(0, weight=1)
        colors_row.columnconfigure(1, weight=1)
        text_color_row = ttk.Frame(colors_row, style=self._theme.card_style)
        text_color_row.grid(row=0, column=0, sticky=tk.EW, padx=(0, 8))
        self._ent_color = ttk.Entry(text_color_row, textvariable=self._color, width=8)
        self._ent_color.pack(side=tk.LEFT)
        self._btn_color_text = ttk.Button(
            text_color_row,
            text="…",
            width=3,
            command=lambda: self._pick_color(self._color),
            style=sec,
        )
        self._btn_color_text.pack(side=tk.LEFT, padx=(_PX_MICRO, 0))
        self._remember_idle(self._ent_color, "normal")
        self._remember_idle(self._btn_color_text, "normal")
        bg_color_row = ttk.Frame(colors_row, style=self._theme.card_style)
        bg_color_row.grid(row=0, column=1, sticky=tk.EW)
        self._ent_bg = ttk.Entry(bg_color_row, textvariable=self._bg_color, width=8)
        self._ent_bg.pack(side=tk.LEFT)
        self._btn_color_bg = ttk.Button(
            bg_color_row,
            text="…",
            width=3,
            command=lambda: self._pick_color(self._bg_color),
            style=sec,
        )
        self._btn_color_bg.pack(side=tk.LEFT, padx=(_PX_MICRO, 0))
        self._remember_idle(self._ent_bg, "normal")
        self._remember_idle(self._btn_color_bg, "normal")

        field_label(style_grid, "Opacidade do fundo", 6, 0, 2)
        opacity_row = ttk.Frame(style_grid, style=self._theme.card_style)
        opacity_row.grid(row=7, column=0, columnspan=2, sticky=tk.EW)
        self._sc_opacity = ttk.Scale(
            opacity_row,
            from_=0,
            to=100,
            variable=self._opacity,
            orient=tk.HORIZONTAL,
            command=self._on_opacity_scale,
            style="Horizontal.TScale",
        )
        self._sc_opacity.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._remember_idle(self._sc_opacity, "normal")
        self._lbl_opacity_pct = ttk.Label(
            opacity_row,
            text=f"{int(self._opacity.get())}%",
            width=5,
            style="Field.TLabel",
        )
        self._lbl_opacity_pct.pack(side=tk.LEFT, padx=(_PX_NEAR, 0))

        options_wrap = tk.Frame(body, bg=EDGE, highlightthickness=0, bd=0)
        options_wrap.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(10, 0))
        options = ttk.Frame(
            options_wrap,
            style=self._theme.card_style,
            padding=(18, 10, 18, 8),
        )
        options.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        card_heading(options, "3  ·  Recursos opcionais", _IC_MIC)

        options_top = ttk.Frame(options, style=self._theme.card_style)
        options_top.pack(fill=tk.X, pady=(0, 7))
        ttk.Label(options_top, text="Dublagem", style="Field.TLabel").pack(side=tk.LEFT)
        self._cb_dub = ttk.Combobox(
            options_top,
            textvariable=self._dub_to,
            values=("off", "en", "pt"),
            state="readonly",
            width=7,
        )
        self._cb_dub.pack(side=tk.LEFT, padx=(8, _PX_NEAR))
        self._remember_idle(self._cb_dub, "readonly")
        self._cb_dub.bind("<<ComboboxSelected>>", lambda _e: self._toggle_tts())
        ttk.Label(options_top, text="Voz", style="Field.TLabel").pack(side=tk.LEFT)
        self._ent_voice = ttk.Entry(options_top, textvariable=self._tts_voice, width=22)
        self._ent_voice.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, _PX_NEAR))
        self._remember_idle(self._ent_voice, "normal")
        self._lbl_voice_hint = ttk.Label(
            options_top,
            text="vazio = voz padrão",
            style="Muted.TLabel",
        )
        self._lbl_voice_hint.pack(side=tk.LEFT)

        improvements = ttk.Frame(options, style=self._theme.card_style)
        improvements.pack(fill=tk.X)
        ttk.Label(improvements, text="Transcrição", style="Field.TLabel").pack(side=tk.LEFT)
        self._cb_transcribe = ttk.Combobox(
            improvements,
            textvariable=self._transcribe_backend,
            values=("local", "groq"),
            state="readonly",
            width=8,
        )
        self._cb_transcribe.pack(side=tk.LEFT, padx=(8, _PX_NEAR))
        self._remember_idle(self._cb_transcribe, "readonly")
        ttk.Label(improvements, text="Watermark", style="Field.TLabel").pack(side=tk.LEFT)
        self._ent_watermark = ttk.Entry(
            improvements,
            textvariable=self._visual_watermark,
            width=14,
        )
        self._ent_watermark.pack(side=tk.LEFT, padx=(8, _PX_NEAR))
        self._remember_idle(self._ent_watermark, "normal")
        self._btn_limpar_temp = ttk.Button(
            improvements,
            text="Limpar temp",
            command=self._limpar_temp,
            style=sec,
        )
        self._btn_limpar_temp.pack(side=tk.RIGHT)
        self._remember_idle(self._btn_limpar_temp, "normal")

        checks = ttk.Frame(options, style=self._theme.card_style)
        checks.pack(fill=tk.X, pady=(5, 0))
        for text, var in (
            ("Karaokê", self._karaoke),
            ("Grade", self._visual_grade),
            ("Barra", self._visual_progress),
            ("Smart crop", self._smart_crop),
            ("GPU encode", self._use_gpu_encode),
            ("Kokoro na dub", self._prefer_local_tts),
        ):
            cb = ttk.Checkbutton(checks, text=text, variable=var, style=cb_st)
            cb.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
            self._remember_idle(cb, "normal")

    def _build_tab_quiz(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Aba 2 — Máquina de Quizzes (projeto.md §13.3.1)."""
        parent = self._make_surface(parent)
        body = self._make_section(parent, "Configuração do quiz", _IC_QUIZ, compact=True)

        row_theme = ttk.Frame(body)
        row_theme.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_theme, text="Tema / nicho").pack(anchor=tk.W)
        self._ent_quiz_theme = ttk.Entry(row_theme, textvariable=self._quiz_theme, width=48)
        self._ent_quiz_theme.pack(anchor=tk.W, fill=tk.X, pady=(_PX_MICRO, 0))
        self._remember_idle(self._ent_quiz_theme, "normal")
        ttk.Label(
            row_theme,
            text="Ex.: Geografia, Futebol, Curiosidades",
            font=(base["family"], 8),
            foreground=_MUTED_NOTE_FG,
        ).pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        row_count = ttk.Frame(body)
        row_count.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_count, text="Quantidade de perguntas (1–10)").pack(anchor=tk.W)
        count_line = ttk.Frame(row_count)
        count_line.pack(fill=tk.X, pady=(_PX_MICRO, 0))
        self._sp_quiz_count = ttk.Spinbox(
            count_line,
            from_=1,
            to=10,
            textvariable=self._quiz_count,
            width=6,
        )
        self._sp_quiz_count.pack(side=tk.LEFT)
        self._remember_idle(self._sp_quiz_count, "normal")
        self._lbl_quiz_count = ttk.Label(count_line, textvariable=self._quiz_count, width=4)
        self._lbl_quiz_count.pack(side=tk.LEFT, padx=(_PX_NEAR, 0))

        row_diff = ttk.Frame(body)
        row_diff.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_diff, text="Dificuldade das perguntas").pack(anchor=tk.W)
        self._cb_quiz_difficulty = ttk.Combobox(
            row_diff,
            textvariable=self._quiz_difficulty,
            values=("Fácil", "Médio", "Difícil", "Variado"),
            state="readonly",
            width=24,
        )
        self._cb_quiz_difficulty.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._remember_idle(self._cb_quiz_difficulty, "readonly")
        ttk.Label(
            row_diff,
            text="Fácil = trivia conhecida · Médio = equilíbrio · Difícil = pegadinhas · Variado = mistura",
            font=(base["family"], 8),
            foreground=_MUTED_NOTE_FG,
        ).pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        row_timer = ttk.Frame(body)
        row_timer.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_timer, text="Tempo de resposta — timer (3–10 s)").pack(anchor=tk.W)
        timer_line = ttk.Frame(row_timer)
        timer_line.pack(fill=tk.X, pady=(_PX_MICRO, 0))
        self._sc_quiz_timer = ttk.Scale(
            timer_line,
            from_=3,
            to=10,
            variable=self._quiz_timer_sec,
            orient=tk.HORIZONTAL,
            command=self._on_quiz_timer_scale,
            style="Horizontal.TScale",
            length=280,
        )
        self._sc_quiz_timer.pack(side=tk.LEFT)
        self._remember_idle(self._sc_quiz_timer, "normal")
        self._lbl_quiz_timer = ttk.Label(timer_line, width=6)
        self._lbl_quiz_timer.pack(side=tk.LEFT, padx=(_PX_NEAR, 0))
        self._sync_quiz_timer_label()

        row_voice = ttk.Frame(body)
        row_voice.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_voice, text="Voz da dublagem (Edge-TTS)").pack(anchor=tk.W)
        self._cb_quiz_voice = ttk.Combobox(
            row_voice,
            textvariable=self._quiz_tts_voice,
            values=_GUI_TTS_VOICE_LABELS,
            state="readonly",
            width=52,
        )
        self._cb_quiz_voice.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._remember_idle(self._cb_quiz_voice, "readonly")

        row_bg = ttk.Frame(body)
        row_bg.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_bg, text="Cor de fundo do vídeo").pack(anchor=tk.W)
        bg_line = ttk.Frame(row_bg)
        bg_line.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._ent_quiz_bg = ttk.Entry(bg_line, textvariable=self._quiz_bg_color, width=12)
        self._ent_quiz_bg.pack(side=tk.LEFT)
        self._btn_quiz_bg = ttk.Button(
            bg_line,
            text="Paleta…",
            command=lambda: self._pick_color(self._quiz_bg_color),
            style=sec,
        )
        self._btn_quiz_bg.pack(side=tk.LEFT, padx=(_PX_NEAR, 0))
        self._remember_idle(self._ent_quiz_bg, "normal")
        self._remember_idle(self._btn_quiz_bg, "normal")
        ttk.Label(
            row_bg,
            text="Hex #RRGGBB — fundo dos frames 9:16 (pergunta, gancho, recompensa, encerramento).",
            font=(base["family"], 8),
            foreground=_MUTED_NOTE_FG,
        ).pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        run_row = ttk.Frame(parent)
        run_row.pack(fill=tk.X, pady=(_PX_SECTION, 0))
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        run_center = ttk.Frame(run_row)
        run_center.pack(side=tk.LEFT)
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        self._btn_run_quiz = ttk.Button(
            run_center,
            text=f"{_IC_QUIZ}  Gerar Quiz",
            command=self._start_quiz_job,
            style="Accent.TButton",
        )
        self._btn_run_quiz.pack()
        self._remember_idle(self._btn_run_quiz, "normal")

    def _build_tab_batalha(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Aba Batalha 1v1 — duelo por física 2D (Pymunk + FFmpeg)."""
        parent = self._make_surface(parent)
        body = self._make_section(parent, "Configuração da batalha", _IC_BATALHA, compact=True)

        row_theme = ttk.Frame(body)
        row_theme.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_theme, text="Tema do duelo").pack(anchor=tk.W)
        self._ent_batalha_theme = ttk.Entry(row_theme, textvariable=self._batalha_theme, width=48)
        self._ent_batalha_theme.pack(anchor=tk.W, fill=tk.X, pady=(_PX_MICRO, 0))
        self._remember_idle(self._ent_batalha_theme, "normal")
        ttk.Label(
            row_theme,
            text='Ex.: "Interstellar vs Oppenheimer", marcas, times, personagens…',
            font=(base["family"], 8),
            foreground=_MUTED_NOTE_FG,
        ).pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        row_modo = ttk.Frame(body)
        row_modo.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_modo, text="Modo de jogo").pack(anchor=tk.W)
        self._cb_batalha_modo = ttk.Combobox(
            row_modo,
            textvariable=self._batalha_modo_label,
            values=(
                "Duelo de Tamanho (Agar.io)",
                "Domínio de Território",
                "Corrida (Plinko)",
            ),
            state="readonly",
            width=36,
        )
        self._cb_batalha_modo.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._remember_idle(self._cb_batalha_modo, "readonly")
        ttk.Label(
            row_modo,
            text="Tamanho: colisões alteram o raio · Território: pinta o fundo · Plinko: corrida até a linha de chegada",
            font=(base["family"], 8),
            foreground=_MUTED_NOTE_FG,
            wraplength=640,
        ).pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        row_voice = ttk.Frame(body)
        row_voice.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_voice, text="Voz da narração de abertura").pack(anchor=tk.W)
        self._cb_batalha_voice = ttk.Combobox(
            row_voice,
            textvariable=self._batalha_tts_voice,
            values=_GUI_TTS_VOICE_LABELS,
            state="readonly",
            width=52,
        )
        self._cb_batalha_voice.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._remember_idle(self._cb_batalha_voice, "readonly")

        run_row = ttk.Frame(parent)
        run_row.pack(fill=tk.X, pady=(_PX_SECTION, 0))
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        run_center = ttk.Frame(run_row)
        run_center.pack(side=tk.LEFT)
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        self._btn_run_batalha = ttk.Button(
            run_center,
            text=f"{_IC_BATALHA}  Gerar Batalha",
            command=self._start_batalha_job,
            style="Accent.TButton",
        )
        self._btn_run_batalha.pack()
        self._remember_idle(self._btn_run_batalha, "normal")

    def _build_tab_historia(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Aba História — texto → cenas (Groq) → TTS + ComfyUI → vídeo final."""
        parent = self._make_surface(parent)
        body = self._make_section(parent, "História narrada em vídeo", _IC_HISTORIA, compact=True)

        ttk.Label(
            body,
            text="Texto da história",
        ).pack(anchor=tk.W)
        fixed_f = tkfont.nametofont("TkFixedFont")
        self._txt_historia = ScrolledText(
            body,
            height=12,
            wrap=tk.WORD,
            font=fixed_f,
            relief=tk.FLAT,
            borderwidth=0,
            padx=12,
            pady=10,
            highlightthickness=0,
        )
        self._txt_historia.pack(fill=tk.BOTH, expand=True, pady=(_PX_MICRO, _PX_NEAR))
        self._remember_idle(self._txt_historia, tk.NORMAL)
        self._txt_historia.insert(
            "1.0",
            "Cole ou escreva aqui a história completa.\n"
            "A IA divide em cenas, gera narração (TTS), vídeo por cena (ComfyUI) "
            "e monta um MP4 final em resultados/historias/.",
        )

        row_voice = ttk.Frame(body)
        row_voice.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_voice, text="Voz da narração").pack(anchor=tk.W)
        self._cb_historia_voice = ttk.Combobox(
            row_voice,
            textvariable=self._historia_tts_voice,
            values=_GUI_TTS_VOICE_LABELS,
            state="readonly",
            width=52,
        )
        self._cb_historia_voice.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._remember_idle(self._cb_historia_voice, "readonly")
        ttk.Label(
            row_voice,
            text=(
                "Requer ComfyUI em http://127.0.0.1:8188 com workflow_historia.json. "
                "Cada cena pode levar vários minutos."
            ),
            font=(base["family"], 8),
            foreground=_MUTED_NOTE_FG,
            wraplength=640,
        ).pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        run_row = ttk.Frame(parent)
        run_row.pack(fill=tk.X, pady=(_PX_SECTION, 0))
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        run_center = ttk.Frame(run_row)
        run_center.pack(side=tk.LEFT)
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        self._btn_run_historia = ttk.Button(
            run_center,
            text=f"{_IC_HISTORIA}  Gerar vídeo da história",
            command=self._start_historia_job,
            style="Accent.TButton",
        )
        self._btn_run_historia.pack()
        self._remember_idle(self._btn_run_historia, "normal")

    def _historia_text_content(self) -> str:
        return self._txt_historia.get("1.0", tk.END).strip()

    def _batalha_modo_from_label(self, label: str) -> str:
        mapping = {
            "Duelo de Tamanho (Agar.io)": "tamanho",
            "Domínio de Território": "territorio",
            "Corrida (Plinko)": "plinko",
        }
        return normalize_batalha_modo(mapping.get(label.strip(), label))

    def _build_tab_tts(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Aba Text-to-Speech — texto → MP3 (local / Gemini / Edge)."""
        parent = self._make_surface(parent)
        body = self._make_section(parent, "Locução (TTS)", _IC_SPEAKER, compact=True)

        ttk.Label(
            body,
            text="Texto para sintetizar",
        ).pack(anchor=tk.W)
        fixed_f = tkfont.nametofont("TkFixedFont")
        self._txt_tts = ScrolledText(
            body,
            height=10,
            wrap=tk.WORD,
            font=fixed_f,
            relief=tk.FLAT,
            borderwidth=0,
            padx=12,
            pady=10,
            highlightthickness=0,
        )
        self._txt_tts.pack(fill=tk.BOTH, expand=True, pady=(_PX_MICRO, _PX_NEAR))
        self._remember_idle(self._txt_tts, tk.NORMAL)
        self._txt_tts.insert(
            "1.0",
            "Digite aqui o texto que será convertido em áudio MP3.\n"
            "Use «Ouvir amostra» para testar a voz antes de gerar o arquivo completo.",
        )

        row_voice = ttk.Frame(body)
        row_voice.pack(fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(row_voice, text="Voz").pack(anchor=tk.W)
        self._cb_tts_voice = ttk.Combobox(
            row_voice,
            textvariable=self._standalone_tts_voice,
            values=_GUI_TTS_VOICE_LABELS,
            state="readonly",
            width=52,
        )
        self._cb_tts_voice.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._remember_idle(self._cb_tts_voice, "readonly")
        if local_tts_available():
            tts_hint = "Voz local Kokoro (GPU) — padrão quando instalado via scripts/install_local_tts.sh."
        elif gemini_tts_available():
            tts_hint = "Gemini cloud — requer GEMINI_API_KEY no .env."
        else:
            tts_hint = (
                "Instale TTS local: bash scripts/install_local_tts.sh — ou defina GEMINI_API_KEY."
            )
        ttk.Label(
            row_voice,
            text=f"MP3 em resultados/tts/ · {tts_hint}",
            font=(base["family"], 8),
            foreground=_MUTED_NOTE_FG,
            wraplength=640,
        ).pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        btn_row = ttk.Frame(body)
        btn_row.pack(fill=tk.X, pady=(_PX_NEAR, 0))
        self._btn_tts_preview = ttk.Button(
            btn_row,
            text=f"{_IC_MIC}  Ouvir amostra",
            command=self._preview_tts_voice,
            style=sec,
        )
        self._btn_tts_preview.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        self._remember_idle(self._btn_tts_preview, "normal")

        run_row = ttk.Frame(parent)
        run_row.pack(fill=tk.X, pady=(_PX_SECTION, 0))
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        run_center = ttk.Frame(run_row)
        run_center.pack(side=tk.LEFT)
        ttk.Frame(run_row).pack(side=tk.LEFT, expand=True)
        self._btn_run_tts = ttk.Button(
            run_center,
            text=f"{_IC_SPEAKER}  Gerar MP3",
            command=self._start_tts_job,
            style="Accent.TButton",
        )
        self._btn_run_tts.pack()
        self._remember_idle(self._btn_run_tts, "normal")

    def _build_tab_youtube(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Agenda cinco pares MP4/TXT em cinco dias consecutivos pela Data API v3."""
        surface = self._make_surface(parent)
        files_body = self._make_section(
            surface, "Arquivos para publicar", _IC_YOUTUBE, compact=True
        )
        ttk.Label(
            files_body,
            text=(
                "Escolha exatamente 5 MP4 e 5 TXT. Cada TXT deve ter o mesmo nome-base do "
                "MP4 correspondente; os pares serão ordenados pelo nome do arquivo."
            ),
            style="Muted.TLabel",
            wraplength=760,
        ).pack(anchor=tk.W, pady=(0, _PX_NEAR))

        file_row = ttk.Frame(files_body, style=self._theme.card_style)
        file_row.pack(fill=tk.X, pady=(0, _PX_NEAR))
        self._btn_youtube_pick = ttk.Button(
            file_row,
            text="Escolher arquivos…",
            command=self._pick_youtube_files,
            style=sec,
        )
        self._btn_youtube_pick.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        self._remember_idle(self._btn_youtube_pick, "normal")
        ttk.Label(
            file_row,
            textvariable=self._youtube_selection_label,
            style="Field.TLabel",
            wraplength=620,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        options = ttk.Frame(files_body, style=self._theme.card_style)
        options.pack(fill=tk.X, pady=(0, _PX_NEAR))
        time_block = ttk.Frame(options, style=self._theme.card_style)
        time_block.pack(side=tk.LEFT, padx=(0, _PX_SECTION))
        ttk.Label(time_block, text="Horário diário (HH:MM)", style="Field.TLabel").pack(
            anchor=tk.W
        )
        self._ent_youtube_schedule_time = ttk.Entry(
            time_block,
            textvariable=self._youtube_schedule_time,
            width=10,
        )
        self._ent_youtube_schedule_time.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._ent_youtube_schedule_time.bind(
            "<KeyRelease>", self._refresh_youtube_schedule_preview
        )
        self._remember_idle(self._ent_youtube_schedule_time, "normal")
        self._chk_youtube_kids = ttk.Checkbutton(
            options,
            text="Conteúdo criado para crianças",
            variable=self._youtube_made_for_kids,
            style=self._theme.checkbutton_style,
        )
        self._chk_youtube_kids.pack(side=tk.LEFT, anchor=tk.S, pady=(0, 2))
        self._remember_idle(self._chk_youtube_kids, "normal")
        ttk.Label(
            files_body,
            textvariable=self._youtube_schedule_preview,
            style="Muted.TLabel",
            wraplength=820,
        ).pack(anchor=tk.W)
        self._refresh_youtube_schedule_preview()

        oauth_body = self._make_section(
            surface, "Autorização da conta", _IC_SETTINGS, compact=True
        )
        ttk.Label(
            oauth_body,
            text=(
                "No primeiro upload, use um JSON OAuth do Google Cloud do tipo "
                "«Aplicativo para computador». O navegador abrirá para você escolher e autorizar "
                "o canal; os próximos uploads reutilizam o token local."
            ),
            style="Muted.TLabel",
            wraplength=800,
        ).pack(anchor=tk.W, pady=(0, _PX_NEAR))
        oauth_row = ttk.Frame(oauth_body, style=self._theme.card_style)
        oauth_row.pack(fill=tk.X)
        self._btn_youtube_credentials = ttk.Button(
            oauth_row,
            text="Escolher credencial OAuth…",
            command=self._pick_youtube_credentials,
            style=sec,
        )
        self._btn_youtube_credentials.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        self._remember_idle(self._btn_youtube_credentials, "normal")
        ttk.Label(
            oauth_row,
            textvariable=self._youtube_client_secrets_path,
            style="Field.TLabel",
            wraplength=580,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        run_row = ttk.Frame(surface, style=self._theme.card_style)
        run_row.pack(fill=tk.X, pady=(_PX_SECTION, 0))
        ttk.Frame(run_row, style=self._theme.card_style).pack(side=tk.LEFT, expand=True)
        self._btn_youtube_post = ttk.Button(
            run_row,
            text=f"{_IC_YOUTUBE}  Agendar 5 vídeos no YouTube",
            command=self._post_to_youtube,
            style="Accent.TButton",
        )
        self._btn_youtube_post.pack(side=tk.LEFT)
        self._remember_idle(self._btn_youtube_post, "normal")
        ttk.Frame(run_row, style=self._theme.card_style).pack(side=tk.LEFT, expand=True)

    def _build_tab_performance(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Importa analytics recentes e apresenta três recomendações editoriais."""
        surface = self._make_surface(parent, expand=True)
        input_body = self._make_section(
            surface, "Desempenho dos últimos 7 dias", _IC_ANALYTICS, compact=True
        )
        ttk.Label(
            input_body,
            text=(
                "Importe o CSV exportado do TikTok, YouTube ou outra rede. A análise detecta "
                "títulos/temas, visualizações, engajamento, retenção e seguidores; depois combina "
                "o ranking local com a Groq para sugerir os próximos três temas."
            ),
            style="Muted.TLabel",
            wraplength=850,
        ).pack(anchor=tk.W, pady=(0, _PX_NEAR))

        file_row = ttk.Frame(input_body, style=self._theme.card_style)
        file_row.pack(fill=tk.X, pady=(0, _PX_NEAR))
        self._btn_performance_pick = ttk.Button(
            file_row,
            text="Escolher CSV…",
            command=self._pick_performance_csv,
            style=sec,
        )
        self._btn_performance_pick.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        ttk.Label(
            file_row,
            textvariable=self._performance_csv_label,
            style="Field.TLabel",
            wraplength=600,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn_performance_analyze = ttk.Button(
            file_row,
            text=f"{_IC_ANALYTICS}  Analisar e recomendar 3 temas",
            command=self._analyze_performance_csv_clicked,
            style="Accent.TButton",
            state=tk.DISABLED,
        )
        self._btn_performance_analyze.pack(side=tk.RIGHT, padx=(_PX_NEAR, 0))

        ttk.Label(
            input_body,
            text=(
                "Colunas reconhecidas automaticamente: Tema/Título/Caption, Views/Alcance, "
                "Curtidas, Comentários, Compartilhamentos, Salvamentos, Retenção/tempo médio "
                "e Novos seguidores. CSV com vírgula ou ponto e vírgula é aceito."
            ),
            style="Muted.TLabel",
            wraplength=850,
        ).pack(anchor=tk.W)

        result_body = self._make_section(
            surface, "Próximos temas recomendados", _IC_DONE, compact=True
        )
        ttk.Label(
            result_body,
            textvariable=self._performance_summary,
            style="Muted.TLabel",
            wraplength=850,
        ).pack(anchor=tk.W, pady=(0, _PX_NEAR))

        tree_row = ttk.Frame(result_body, style=self._theme.card_style)
        tree_row.pack(fill=tk.X)
        self._performance_tree = ttk.Treeview(
            tree_row,
            columns=("rank", "theme", "score", "evidence"),
            show="headings",
            height=3,
            selectmode=tk.BROWSE,
        )
        self._performance_tree.heading("rank", text="#", anchor=tk.CENTER)
        self._performance_tree.heading("theme", text="Tema", anchor=tk.W)
        self._performance_tree.heading("score", text="Score", anchor=tk.CENTER)
        self._performance_tree.heading("evidence", text="Principal evidência", anchor=tk.W)
        self._performance_tree.column("rank", width=42, stretch=False, anchor=tk.CENTER)
        self._performance_tree.column("theme", width=260, stretch=True, anchor=tk.W)
        self._performance_tree.column("score", width=72, stretch=False, anchor=tk.CENTER)
        self._performance_tree.column("evidence", width=430, stretch=True, anchor=tk.W)
        self._performance_tree.pack(fill=tk.X, expand=True)

        self._performance_details = ScrolledText(
            result_body,
            height=8,
            wrap=tk.WORD,
            relief=tk.FLAT,
            borderwidth=0,
            padx=14,
            pady=10,
            state=tk.DISABLED,
            font=(base["family"], 10),
        )
        self._performance_details.pack(fill=tk.BOTH, expand=True, pady=(_PX_NEAR, 0))
        self._btn_performance_copy = ttk.Button(
            result_body,
            text="Copiar recomendações",
            command=self._copy_performance_recommendations,
            style=sec,
            state=tk.DISABLED,
        )
        self._btn_performance_copy.pack(anchor=tk.E, pady=(_PX_NEAR, 0))

        loop_body = self._make_section(
            surface, "Loop de retenção — dados reais no pipeline", _IC_ANALYTICS, compact=True
        )
        ttk.Label(
            loop_body,
            text=(
                "Importe o relatório JSON do TikTok (tiktok_report_*.json). O app cruza views, "
                "engajamento, horários e seguidores ganhos por vídeo para revelar duração "
                "vencedora, melhores janelas de postagem, padrões de legenda e ímãs de "
                "seguidores — e grava data/growth_profile.json, que o pipeline de cortes lê "
                "para ajustar a duração-alvo dos próximos clipes."
            ),
            style="Muted.TLabel",
            wraplength=850,
        ).pack(anchor=tk.W, pady=(0, _PX_NEAR))
        loop_row = ttk.Frame(loop_body, style=self._theme.card_style)
        loop_row.pack(fill=tk.X)
        self._btn_retention_pick = ttk.Button(
            loop_row,
            text="Escolher relatório JSON…",
            command=self._pick_retention_report,
            style=sec,
        )
        self._btn_retention_pick.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        ttk.Label(
            loop_row,
            textvariable=self._retention_label,
            style="Field.TLabel",
            wraplength=520,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn_retention_apply = ttk.Button(
            loop_row,
            text=f"{_IC_ANALYTICS}  Analisar e aplicar ao pipeline",
            command=self._apply_retention_loop_clicked,
            style="Accent.TButton",
            state=tk.DISABLED,
        )
        self._btn_retention_apply.pack(side=tk.RIGHT, padx=(_PX_NEAR, 0))
        self._retention_details = ScrolledText(
            loop_body,
            height=8,
            wrap=tk.WORD,
            relief=tk.FLAT,
            borderwidth=0,
            padx=14,
            pady=10,
            state=tk.DISABLED,
            font=(base["family"], 10),
        )
        self._retention_details.pack(fill=tk.BOTH, expand=True, pady=(_PX_NEAR, 0))

    def _pick_retention_report(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecione o relatório JSON do TikTok",
            filetypes=[("Arquivo JSON", "*.json"), ("Todos", "*.*")],
        )
        if not path:
            return
        selected = Path(path).expanduser().resolve()
        self._retention_report_path.set(str(selected))
        self._retention_label.set(selected.name)
        self._sync_retention_controls()

    def _sync_retention_controls(self) -> None:
        blocked = self._retention_busy or self._pipeline_running
        if hasattr(self, "_btn_retention_pick"):
            self._btn_retention_pick.configure(state=tk.DISABLED if blocked else tk.NORMAL)
        if hasattr(self, "_btn_retention_apply"):
            ready = bool(self._retention_report_path.get()) and not blocked
            self._btn_retention_apply.configure(state=tk.NORMAL if ready else tk.DISABLED)

    def _apply_retention_loop_clicked(self) -> None:
        if self._retention_busy or not self._retention_report_path.get():
            return
        self._retention_busy = True
        self._sync_retention_controls()
        self._set_status("Analisando relatório de retenção…")
        report = self._retention_report_path.get()

        def worker() -> None:
            try:
                insight = analyze_retention_report_file(report)
                profile_path = save_growth_profile(insight)
            except Exception as exc:
                error_text = str(exc)
                self.after(0, lambda: self._retention_done(error_text, None))
                return
            self.after(0, lambda: self._retention_done(None, (insight, str(profile_path))))

        self._retention_thread = threading.Thread(target=worker, daemon=True)
        self._retention_thread.start()

    def _retention_done(
        self,
        error: str | None,
        payload: tuple[object, str] | None,
    ) -> None:
        self._retention_busy = False
        self._sync_retention_controls()
        details = self._retention_details
        details.configure(state=tk.NORMAL)
        details.delete("1.0", tk.END)
        if error is not None:
            details.insert(tk.END, f"Falha na análise de retenção: {error}")
            details.configure(state=tk.DISABLED)
            self._set_status("Falha na análise de retenção")
            return
        insight, profile_path = payload  # type: ignore[misc]
        details.insert(tk.END, f"{insight.summary}\n\nGrowth profile gravado em {profile_path}.")
        details.configure(state=tk.DISABLED)
        duration = insight.recommended_clip_duration_sec
        self._set_status(
            f"Loop de retenção aplicado — duração-alvo {duration}s"
            if duration
            else "Loop de retenção aplicado"
        )

    def _pick_performance_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecione o CSV dos últimos 7 dias",
            filetypes=[("Arquivo CSV", "*.csv"), ("Todos", "*.*")],
        )
        if not path:
            return
        selected = Path(path).expanduser().resolve()
        self._performance_csv_path.set(str(selected))
        self._performance_csv_label.set(selected.name)
        self._performance_last_analysis = None
        self._performance_summary.set(
            "CSV pronto para análise. Clique em «Analisar e recomendar 3 temas»."
        )
        self._clear_performance_results()
        self._sync_performance_controls()

    def _sync_performance_controls(self) -> None:
        unavailable = self._performance_busy or self._pipeline_running
        if hasattr(self, "_btn_performance_pick"):
            self._btn_performance_pick.configure(
                state=tk.DISABLED if unavailable else tk.NORMAL
            )
        if hasattr(self, "_btn_performance_analyze"):
            can_analyze = bool(self._performance_csv_path.get().strip()) and not unavailable
            self._btn_performance_analyze.configure(
                state=tk.NORMAL if can_analyze else tk.DISABLED
            )
        if hasattr(self, "_btn_performance_copy"):
            can_copy = self._performance_last_analysis is not None and not unavailable
            self._btn_performance_copy.configure(state=tk.NORMAL if can_copy else tk.DISABLED)

    def _clear_performance_results(self) -> None:
        if hasattr(self, "_performance_tree"):
            for item in self._performance_tree.get_children():
                self._performance_tree.delete(item)
        if hasattr(self, "_performance_details"):
            self._performance_details.configure(state=tk.NORMAL)
            self._performance_details.delete("1.0", tk.END)
            self._performance_details.configure(state=tk.DISABLED)

    def _analyze_performance_csv_clicked(self) -> None:
        if self._performance_busy:
            return
        if self._pipeline_running:
            messagebox.showinfo(
                "Aguarde",
                "Espere o processamento atual terminar antes de analisar o CSV.",
                parent=self,
            )
            return
        path = Path(self._performance_csv_path.get().strip())
        if not path.is_file():
            messagebox.showerror(
                "Análise de desempenho",
                "Selecione um arquivo CSV válido.",
                parent=self,
            )
            return

        self._performance_busy = True
        self._performance_last_analysis = None
        self._clear_performance_results()
        self._performance_summary.set("Lendo métricas e calculando o ranking…")
        self._sync_performance_controls()
        self._set_status("Analisando desempenho…")
        self._append_log(f"\n[Desempenho] Analisando CSV: {path.name}\n")

        def worker() -> None:
            report: PerformanceAnalysis | None = None
            error: str | None = None
            try:
                report = analyze_performance_csv(path)
            except PerformanceAnalysisError as exc:
                error = str(exc)
            except Exception as exc:
                error = _format_pipeline_error(exc)
            try:
                self.after(
                    0,
                    lambda result=report, message=error: self._finish_performance_analysis(
                        result, message
                    ),
                )
            except tk.TclError:
                pass

        self._performance_thread = threading.Thread(target=worker, daemon=True)
        self._performance_thread.start()

    @staticmethod
    def _performance_report_text(report: PerformanceAnalysis) -> str:
        blocks: list[str] = []
        for index, recommendation in enumerate(report.recommendations, start=1):
            blocks.append(
                f"{index}. {recommendation.theme}\n"
                f"Por que apostar: {recommendation.why}\n"
                f"Próximo vídeo: {recommendation.next_video}\n"
                f"Evidência: {recommendation.evidence}\n"
                f"Score: {recommendation.score:.0f}/100"
            )
        return "\n\n".join(blocks)

    def _finish_performance_analysis(
        self, report: PerformanceAnalysis | None, error: str | None
    ) -> None:
        self._performance_thread = None
        self._performance_busy = False
        if error or report is None:
            message = error or "A análise não retornou um resultado."
            self._performance_summary.set("Não foi possível analisar este CSV.")
            self._sync_performance_controls()
            self._set_status("Falha na análise de desempenho")
            self._append_log_error(f"[Desempenho] {message}")
            messagebox.showerror("Análise de desempenho", message, parent=self)
            return

        self._performance_last_analysis = report
        self._performance_summary.set(report.summary)
        for index, recommendation in enumerate(report.recommendations, start=1):
            self._performance_tree.insert(
                "",
                tk.END,
                iid=f"performance-{index}",
                values=(
                    index,
                    recommendation.theme,
                    f"{recommendation.score:.0f}",
                    recommendation.evidence,
                ),
            )
        report_text = self._performance_report_text(report)
        self._performance_details.configure(state=tk.NORMAL)
        self._performance_details.delete("1.0", tk.END)
        self._performance_details.insert("1.0", report_text)
        self._performance_details.configure(state=tk.DISABLED)
        self._sync_performance_controls()
        mode = "IA + ranking local" if report.used_ai else "ranking local (fallback)"
        self._set_status("Análise concluída — 3 temas recomendados")
        self._append_log(
            f"[Desempenho] Concluído: {report.valid_row_count} linha(s) válidas; "
            f"recomendação por {mode}.\n"
        )

    def _copy_performance_recommendations(self) -> None:
        report = self._performance_last_analysis
        if report is None:
            return
        self.clipboard_clear()
        self.clipboard_append(self._performance_report_text(report))
        self.update()
        self._set_status("Recomendações copiadas")

    def _tts_text_content(self) -> str:
        return self._txt_tts.get("1.0", tk.END).strip()

    def _stop_tts_preview_playback(self) -> None:
        proc = self._tts_preview_proc
        if proc is None:
            return
        self._tts_preview_proc = None
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _set_tts_preview_busy(self, busy: bool) -> None:
        self._tts_preview_busy = busy
        if self._pipeline_running:
            return
        state = tk.DISABLED if busy else tk.NORMAL
        if hasattr(self, "_btn_tts_preview"):
            self._btn_tts_preview.configure(state=state)

    def _preview_tts_voice(self) -> None:
        if self._pipeline_running:
            messagebox.showinfo(
                "Aguarde",
                "Espere o processamento atual terminar antes de ouvir a amostra.",
                parent=self,
            )
            return
        if self._tts_preview_busy:
            return

        text = self._tts_text_content()
        voice = voice_id_from_label(self._standalone_tts_voice.get() or default_voice_label())

        self._stop_tts_preview_playback()
        self._set_tts_preview_busy(True)
        self._append_log("\n[TTS] A gerar amostra de voz…\n")

        def worker() -> None:
            err: str | None = None
            mp3: str | None = None
            try:
                mp3 = synthesize_tts_preview(text, voice)
            except Exception as e:
                err = _format_pipeline_error(e)
            self.after(0, lambda: self._finish_tts_preview(mp3, err))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_tts_preview(self, mp3: str | None, err: str | None) -> None:
        self._set_tts_preview_busy(False)
        if err:
            self._append_log_error(f"[TTS] Amostra falhou: {err}\n")
            messagebox.showerror("Pré-ouvir voz", err, parent=self)
            return
        if not mp3:
            return
        self._append_log(f"[TTS] A reproduzir amostra: {mp3}\n")
        try:
            self._tts_preview_proc = play_audio_file(mp3)
        except Exception as e:
            self._append_log_error(f"[TTS] Não foi possível reproduzir: {_format_pipeline_error(e)}\n")
            messagebox.showerror(
                "Reprodução",
                f"MP3 gerado em:\n{mp3}\n\nErro ao reproduzir: {e}",
                parent=self,
            )

    def _on_quiz_timer_scale(self, value: str | float) -> None:
        try:
            v = int(float(value))
        except (TypeError, ValueError):
            return
        v = max(3, min(10, v))
        if self._quiz_timer_sec.get() != v:
            self._quiz_timer_sec.set(v)
        self._sync_quiz_timer_label()

    def _sync_quiz_timer_label(self) -> None:
        if hasattr(self, "_lbl_quiz_timer"):
            self._lbl_quiz_timer.configure(text=f"{int(self._quiz_timer_sec.get())} s")

    def _apply_text_widget_theme(self) -> None:
        bg, fg = self._text_bg, self._text_fg
        log_fg = getattr(self, "_log_fg", fg)
        sel_bg, sel_fg = MOSS_DK, "#FFFFFF"
        hl_bg, hl_focus = EDGE, MOSS_HI
        kw_urls = dict(
            background=bg,
            foreground=fg,
            insertbackground=fg,
            selectbackground=sel_bg,
            selectforeground=sel_fg,
        )
        self._txt_urls.configure(
            **kw_urls,
            highlightthickness=1,
            highlightbackground=EDGE,
            highlightcolor=MOSS,
        )
        self._log.configure(
            background=bg,
            foreground=log_fg,
            insertbackground=log_fg,
            selectbackground=sel_bg,
            selectforeground=sel_fg,
            highlightbackground=hl_bg,
            highlightcolor=hl_focus,
        )
        self._log.tag_configure("error", foreground="#E06A5C")
        if getattr(self, "_urls_ph_visible", False):
            self._txt_urls.configure(foreground=self._hint_fg)
        if hasattr(self, "_txt_tts"):
            self._txt_tts.configure(**kw_urls)
        if hasattr(self, "_txt_historia"):
            self._txt_historia.configure(**kw_urls)
        if hasattr(self, "_performance_details"):
            self._performance_details.configure(
                **kw_urls,
                highlightthickness=1,
                highlightbackground=EDGE,
                highlightcolor=MOSS,
            )
        if hasattr(self, "_retention_details"):
            self._retention_details.configure(
                **kw_urls,
                highlightthickness=1,
                highlightbackground=EDGE,
                highlightcolor=MOSS,
            )

    def _toggle_tts(self) -> None:
        if getattr(self, "_pipeline_running", False):
            return
        state = tk.NORMAL if (self._dub_to.get() != "off") else tk.DISABLED
        self._ent_voice.configure(state=state)
        self._idle_states[self._ent_voice] = state
        if hasattr(self, "_lbl_voice_hint"):
            self._lbl_voice_hint.configure(
                foreground=_MUTED_NOTE_FG if state == tk.DISABLED else self._hint_fg
            )

    def _pick_video(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecione um ou mais vídeos",
            filetypes=[
                ("Vídeo", "*.mp4 *.mkv *.mov *.webm *.avi"),
                ("Todos", "*.*"),
            ],
        )
        if not paths:
            return

        self._video_paths = [str(p) for p in paths]
        if len(self._video_paths) == 1:
            self._video_path.set(self._video_paths[0])
        else:
            self._video_path.set(
                f"{len(self._video_paths)} arquivos selecionados (processa em fila). Ex.: {self._video_paths[0]}"
            )

    def _pick_youtube_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecione 5 MP4 e os 5 TXT correspondentes",
            filetypes=[
                ("5 vídeos MP4 e 5 legendas TXT", "*.mp4 *.txt"),
                ("Vídeo MP4", "*.mp4"),
                ("Legenda TXT", "*.txt"),
                ("Todos", "*.*"),
            ],
        )
        if not paths:
            return
        try:
            selected = select_upload_batch(paths)
        except YouTubeUploadError as exc:
            messagebox.showerror("YouTube", str(exc), parent=self)
            return
        self._youtube_selected_batch = selected
        ordered_names = "  ·  ".join(
            f"{index}. {item.video_path.name}" for index, item in enumerate(selected, start=1)
        )
        self._youtube_selection_label.set(
            f"5 pares selecionados — ordem de publicação: {ordered_names}"
        )
        self._refresh_youtube_schedule_preview()

    def _refresh_youtube_schedule_preview(self, _event: tk.Event | None = None) -> None:
        try:
            dates = build_daily_publish_times(
                self._youtube_schedule_time.get(),
                count=5,
                timezone_name=self._youtube_schedule_timezone,
            )
        except Exception as exc:
            self._youtube_schedule_preview.set(f"Horário/fuso inválido: {exc}")
            return
        sequence = "  ·  ".join(
            f"{index}) {value.strftime('%d/%m/%Y %H:%M')}"
            for index, value in enumerate(dates, start=1)
        )
        self._youtube_schedule_preview.set(
            f"Começa amanhã · fuso {self._youtube_schedule_timezone}: {sequence}"
        )

    def _pick_youtube_credentials(self) -> Path | None:
        path = filedialog.askopenfilename(
            title="Selecione o JSON OAuth do YouTube",
            filetypes=[("Credencial OAuth JSON", "*.json"), ("Todos", "*.*")],
        )
        if not path:
            return None
        try:
            valid = validate_client_secrets_file(path)
        except YouTubeUploadError as exc:
            messagebox.showerror("Credencial OAuth", str(exc), parent=self)
            return None
        self._youtube_client_secrets_path.set(str(valid))
        self._remember_youtube_credentials_path(valid)
        return valid

    def _remember_youtube_credentials_path(self, path: Path) -> None:
        try:
            _YOUTUBE_CREDENTIALS_REFERENCE.parent.mkdir(parents=True, exist_ok=True)
            _YOUTUBE_CREDENTIALS_REFERENCE.write_text(str(path), encoding="utf-8")
        except OSError as exc:
            self._append_log(
                f"[YouTube] Não foi possível lembrar o caminho da credencial: {exc}\n"
            )

    def _resolve_youtube_credentials_path(self) -> Path | None:
        configured = (self._youtube_client_secrets_path.get() or "").strip()
        if configured:
            try:
                return validate_client_secrets_file(configured)
            except YouTubeUploadError as exc:
                messagebox.showerror("Credencial OAuth", str(exc), parent=self)
                return None
        for name in ("client_secret.json", "client_secrets.json"):
            candidate = _ROOT / name
            if candidate.is_file():
                try:
                    valid = validate_client_secrets_file(candidate)
                except YouTubeUploadError:
                    continue
                self._youtube_client_secrets_path.set(str(valid))
                self._remember_youtube_credentials_path(valid)
                return valid
        return self._pick_youtube_credentials()

    def _set_youtube_uploading(self, uploading: bool) -> None:
        self._youtube_uploading = uploading
        state = tk.DISABLED if uploading or self._pipeline_running else tk.NORMAL
        for name in (
            "_btn_youtube_pick",
            "_btn_youtube_credentials",
            "_btn_youtube_post",
            "_chk_youtube_kids",
            "_ent_youtube_schedule_time",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(state=state)
        if uploading:
            self._set_status("Enviando e agendando 5 vídeos no YouTube…")
        elif not self._pipeline_running:
            self._set_status("Pronto")

    def _post_to_youtube(self) -> None:
        if self._youtube_uploading:
            return
        if self._pipeline_running:
            messagebox.showinfo(
                "Aguarde",
                "Aguarde o processamento atual terminar antes de publicar no YouTube.",
                parent=self,
            )
            return
        selected_paths = [
            str(path)
            for item in self._youtube_selected_batch
            for path in (item.video_path, item.caption_path)
        ]
        try:
            selected = select_upload_batch(selected_paths)
        except YouTubeUploadError as exc:
            messagebox.showerror(
                "YouTube",
                f"{exc}\n\nClique em «Escolher arquivos…» e selecione os 5 MP4 e 5 TXT.",
                parent=self,
            )
            return
        try:
            publish_dates = build_daily_publish_times(
                self._youtube_schedule_time.get(),
                count=len(selected),
                timezone_name=self._youtube_schedule_timezone,
            )
        except Exception as exc:
            messagebox.showerror("Horário", str(exc), parent=self)
            return
        credentials_path = self._resolve_youtube_credentials_path()
        if credentials_path is None:
            return

        made_for_kids = bool(self._youtube_made_for_kids.get())
        token_path = _ROOT / "token.json"
        self._set_youtube_uploading(True)
        self._set_progress_bar(0.0)
        self._append_log(
            "\n[YouTube] Iniciando lote de 5 uploads agendados. "
            "Os arquivos ficam privados até a data de publicação.\n"
        )
        for index, (item, publish_date) in enumerate(
            zip(selected, publish_dates, strict=True), start=1
        ):
            self._append_log(
                f"  {index}. {item.video_path.name} → "
                f"{publish_date.strftime('%d/%m/%Y às %H:%M')}\n"
            )

        def worker() -> None:
            results: list[tuple[YouTubeUploadResult, datetime]] = []
            failures: list[tuple[str, str]] = []
            total = len(selected)
            for batch_index, (item, publish_date) in enumerate(
                zip(selected, publish_dates, strict=True)
            ):
                def report_progress(fraction: float, *, index: int = batch_index) -> None:
                    overall = (index + max(0.0, min(1.0, fraction))) / total
                    try:
                        self.after(
                            0,
                            lambda value=overall, current=index + 1: self._update_youtube_progress(
                                value, current, total
                            ),
                        )
                    except tk.TclError:
                        pass

                try:
                    result = upload_video_to_youtube(
                        item.video_path,
                        item.caption_path,
                        client_secrets_path=credentials_path,
                        token_path=token_path,
                        privacy_status="private",
                        made_for_kids=made_for_kids,
                        publish_at=youtube_publish_at(publish_date),
                        progress=report_progress,
                    )
                    results.append((result, publish_date))
                    self._log_q.put(
                        f"[YouTube] [{batch_index + 1}/{total}] Agendado: "
                        f"{item.video_path.name}\n"
                    )
                except Exception as exc:
                    failures.append((item.video_path.name, str(exc)))
                    self._log_q.put(
                        ("__LOG_ERROR__", f"[YouTube] {item.video_path.name}: {exc}")
                    )
            try:
                self.after(
                    0,
                    lambda completed=results, failed=failures: self._finish_youtube_batch(
                        completed, failed
                    ),
                )
            except tk.TclError:
                pass

        self._youtube_upload_thread = threading.Thread(target=worker, daemon=True)
        self._youtube_upload_thread.start()

    def _update_youtube_progress(self, fraction: float, current: int, total: int) -> None:
        self._set_progress_bar(fraction)
        percent = int(max(0.0, min(1.0, fraction)) * 100)
        self._set_status(f"YouTube — vídeo {current}/{total} — lote {percent}%")

    def _finish_youtube_batch(
        self,
        results: list[tuple[YouTubeUploadResult, datetime]],
        failures: list[tuple[str, str]],
    ) -> None:
        self._youtube_upload_thread = None
        self._set_youtube_uploading(False)
        if not results:
            message = failures[0][1] if failures else "Nenhum vídeo foi agendado."
            self._set_progress_bar(0.0)
            self._set_status("Falha ao agendar no YouTube")
            messagebox.showerror(
                "YouTube", f"Nenhum vídeo foi agendado:\n{message}", parent=self
            )
            return

        self._set_progress_bar(1.0)
        self.after(1000, lambda: self._set_progress_bar(0.0))
        self._set_status(
            f"YouTube — {len(results)} agendado(s), {len(failures)} falha(s)"
        )
        summary_lines = [
            f"{index}. {publish_date.strftime('%d/%m/%Y às %H:%M')} — {result.url}"
            for index, (result, publish_date) in enumerate(results, start=1)
        ]
        summary = "\n".join(summary_lines)
        self._append_log(f"[YouTube] Lote concluído:\n{summary}\n")
        self.clipboard_clear()
        self.clipboard_append("\n".join(result.url for result, _date in results))
        self.update()
        if self._notify_when_done.get():
            desktop_notify("YouTube", f"{len(results)} vídeo(s) agendado(s).")
        message = (
            f"{len(results)} vídeo(s) agendado(s). Eles ficam privados até o horário.\n\n"
            f"{summary}\n\nOs links foram copiados."
        )
        if failures:
            failed_names = ", ".join(name for name, _error in failures)
            messagebox.showwarning(
                "YouTube",
                f"{message}\n\nFalharam: {failed_names}. Veja o log.",
                parent=self,
            )
        else:
            messagebox.showinfo("YouTube", message, parent=self)

    def _pick_color(self, var: tk.StringVar) -> None:
        cur = var.get().strip() or "#FFFFFF"
        if not cur.startswith("#"):
            cur = "#" + cur
        rgb, hx = colorchooser.askcolor(color=cur, title="Escolher cor")
        if hx:
            var.set(hx.upper())

    def _append_log(self, text: str, *, tag: str | None = None) -> None:
        self._log.configure(state=tk.NORMAL)
        if tag:
            self._log.insert(tk.END, text, tag)
        else:
            self._log.insert(tk.END, text)
        self._trim_log_widget()
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _append_log_error(self, text: str) -> None:
        """Destaque vermelho no painel de log."""
        block = text if text.endswith("\n") else f"{text}\n"
        if "[ERRO]" not in block and "Traceback" not in block:
            block = f"\n[ERRO] {block.lstrip()}"
        self._append_log(block, tag="error")

    def _trim_log_widget(self) -> None:
        try:
            end_idx = self._log.index("end-1c")
            total_lines = int(end_idx.split(".")[0])
            max_lines = 500
            if total_lines > max_lines:
                self._log.delete("1.0", f"{total_lines - max_lines + 1}.0")
        except tk.TclError:
            pass

    def _push_error_to_log_queue(self, exc: BaseException, *, context: str) -> None:
        """Envia resumo + traceback para a fila (thread do worker)."""
        summary = _format_pipeline_error(exc)
        self._log_q.put(("__LOG_ERROR__", f"{context}: {summary}"))
        buf = io.StringIO()
        traceback.print_exc(file=buf)
        detail = buf.getvalue()
        if detail.strip():
            self._log_q.put(detail)

    def _clear_log(self) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                chunk = self._log_q.get_nowait()
                if chunk is None:
                    break
                if isinstance(chunk, tuple) and len(chunk) == 3 and chunk[0] == "__DONE__":
                    self._handle_pipeline_done(chunk[1], chunk[2])
                    continue
                if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "__PROGRESS__":
                    self._set_progress_bar(float(chunk[1]))
                    continue
                if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "__LOG_ERROR__":
                    self._append_log_error(str(chunk[1]))
                    self._last_pipeline_error = str(chunk[1])
                    continue
                if isinstance(chunk, str) and "Traceback (most recent call last)" in chunk:
                    self._append_log(chunk, tag="error")
                    continue
                self._append_log(chunk)
        except queue.Empty:
            pass
        self.after(80, self._drain_log_queue)

    def _set_progress_bar(self, fraction: float) -> None:
        """Barra 0–1 com largura animada até o alvo. Só roda na thread principal."""
        if not hasattr(self, "_prog_fill") or not hasattr(self, "_prog_track"):
            return
        target = max(0.0, min(1.0, float(fraction)))
        if target <= 0.0:
            self._motion.cancel("progress")
            self._prog_current = 0.0
            self._prog_fill.place_forget()
            return
        if not self._prog_fill.winfo_ismapped():
            self._prog_fill.place(
                in_=self._prog_track,
                x=0,
                y=0,
                anchor="nw",
                relheight=1.0,
                relwidth=0.002,
            )
        start = self._prog_current

        def step(t: float) -> None:
            frac = lerp(start, target, t)
            self._prog_current = frac
            self._prog_fill.place_configure(relwidth=max(0.002, frac))

        self._motion.tween("progress", 260, step, ease=ease_out_cubic)

    def _start_status_pulse(self) -> None:
        """Dot da barra de status respira enquanto o pipeline roda."""
        self._status_pulse_gen += 1
        self._pulse_step(self._status_pulse_gen, forward=True)

    def _pulse_step(self, gen: int, *, forward: bool) -> None:
        if gen != self._status_pulse_gen or self._status_dot is None:
            return
        a, b = (SUCCESS, "#17402F") if forward else ("#17402F", SUCCESS)

        def step(t: float) -> None:
            if gen == self._status_pulse_gen and self._status_dot is not None:
                self._status_dot.configure(fg=lerp_color(a, b, t))

        self._motion.tween(
            "status.pulse",
            760,
            step,
            ease=ease_out_cubic,
            done=lambda: self._pulse_step(gen, forward=not forward),
        )

    def _stop_status_pulse(self) -> None:
        self._status_pulse_gen += 1
        self._motion.cancel("status.pulse")
        if self._status_dot is not None:
            self._status_dot.configure(fg=SUCCESS)

    def _set_running(self, running: bool) -> None:
        self._pipeline_running = running
        for btn in (
            getattr(self, "_btn_run_cortes", None),
            getattr(self, "_btn_run_quiz", None),
            getattr(self, "_btn_run_batalha", None),
            getattr(self, "_btn_run_historia", None),
            getattr(self, "_btn_run_tts", None),
            getattr(self, "_btn_tts_preview", None),
        ):
            if btn is not None:
                btn.configure(state=tk.DISABLED if running else tk.NORMAL)
        self._btn_cancel.configure(state=tk.NORMAL if running else tk.DISABLED)
        self._sync_cut_action_controls()
        if hasattr(self, "_btn_send_telegram"):
            self._btn_send_telegram.configure(
                state=tk.DISABLED if running or self._telegram_sending else tk.NORMAL
            )
        if running:
            for w, _idle in self._idle_states.items():
                w.configure(state=tk.DISABLED)
            self._start_status_pulse()
            self._set_status("Processando…")
        else:
            for w, idle in self._idle_states.items():
                w.configure(state=idle)
            self._toggle_tts()
            if not running:
                self._set_tts_preview_busy(False)
            self._stop_status_pulse()
            self._set_status("Pronto")
        self._sync_performance_controls()
        self._sync_retention_controls()

    def _cancel_pipeline(self) -> None:
        try:
            from app.core.cancel import request_cancel

            request_cancel()
            self._append_log("\n[!] Cancelamento solicitado…\n")
            self._btn_cancel.configure(state=tk.DISABLED)
            self._set_status("Cancelando…")
        except Exception:
            # se algo der errado, não derruba a UI
            self._append_log("\n[!] Não foi possível solicitar cancelamento.\n")

    def _handle_pipeline_done(self, outputs: list[str], had_error: bool) -> None:
        self._set_running(False)
        self._set_progress_bar(1.0)
        self.after(750, lambda: self._set_progress_bar(0.0))
        self._append_log("\n--- Fim da execução ---\n")
        self._last_outputs = list(outputs)
        self._populate_results_tree(self._last_outputs)

        if had_error:
            self._set_status("Erro — veja o log")
            err_line = (self._last_pipeline_error or "").strip()
            box_msg = (
                f"{err_line}\n\nVeja o traceback completo no painel «Log da execução»."
                if err_line
                else "Ocorreu um erro. Veja os detalhes no painel «Log da execução» abaixo."
            )
            messagebox.showerror("Erro no processamento", box_msg, parent=self)
            self._append_log_error("--- Processamento interrompido por erro ---")
            if self._notify_when_done.get():
                desktop_notify(
                    "Processamento — atenção",
                    err_line or "Ocorreu um erro; confira o log na janela.",
                )
            return

        if not outputs:
            self._set_status("Concluído — nenhum arquivo gerado")
            if self._notify_when_done.get():
                desktop_notify("Processamento", "Nenhum arquivo gerado nesta execução.")
            return

        self._set_status(f"Concluído — {len(outputs)} arquivo(s) pronto(s)")

        if self._open_results_when_done.get():
            _open_folder(OUTPUT_DIR)

        if self._notify_when_done.get():
            n = len(outputs)
            desktop_notify(
                "Arquivos prontos",
                f"{n} vídeo(s) em resultados/ — legendas .txt ao lado de cada .mp4.",
            )

        if self._zip_when_done.get():
            zp = export_cortes_zip(outputs, OUTPUT_DIR)
            if zp:
                self._append_log(f"\n[Pacote .zip] {zp}\n")
            else:
                self._append_log("\n[Pacote .zip] Nada para compactar (sem arquivos válidos).\n")

    def _populate_results_tree(self, paths: list[str]) -> None:
        self._results_gen += 1
        gen = self._results_gen
        self._tree_row_to_path.clear()
        for item in self._tree.get_children():
            self._tree.delete(item)
        if not paths:
            return
        ordered = _sort_clip_outputs(paths)
        for i, path in enumerate(ordered):
            iid = f"r{i}"
            self._tree_row_to_path[iid] = path
            self._tree.insert("", tk.END, iid=iid, text=Path(path).name, values=("…",))

        def worker() -> None:
            durs: dict[str, float | None] = {}
            for p in ordered:
                durs[p] = ffprobe_duration_seconds(p)
            self.after(0, lambda: self._apply_durations_to_tree(gen, ordered, durs))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_durations_to_tree(
        self,
        gen: int,
        ordered: list[str],
        durs: dict[str, float | None],
    ) -> None:
        if gen != self._results_gen:
            return
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        for i, path in enumerate(ordered):
            iid = f"r{i}"
            if self._tree_row_to_path.get(iid) != path:
                continue
            try:
                self._tree.set(iid, "dur", format_duration_hms(durs.get(path)))
            except tk.TclError:
                pass

    def _selected_mp4_path(self) -> str | None:
        sel = self._tree.selection()
        if not sel:
            return None
        return self._tree_row_to_path.get(sel[0])

    def _copy_caption_selected(self) -> None:
        mp4 = self._selected_mp4_path()
        if not mp4:
            messagebox.showinfo("Seleção", "Selecione uma linha na tabela de clipes.", parent=self)
            return
        cap = Path(mp4).with_suffix(".txt")
        if not cap.is_file():
            messagebox.showwarning("Legenda", f"Arquivo não encontrado:\n{cap}", parent=self)
            return
        text = cap.read_text(encoding="utf-8").strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def _post_to_tiktok_selected(self) -> None:
        """Prepara post no TikTok: copia legenda, abre pasta do clipe e a página de upload.

        Não faz login nem publica — só deixa pronto para arrastar o vídeo e Ctrl+V.
        """
        mp4 = self._selected_mp4_path()
        if not mp4:
            messagebox.showinfo("Seleção", "Selecione uma linha na tabela de clipes.", parent=self)
            return
        cap = Path(mp4).with_suffix(".txt")
        caption_copied = False
        if cap.is_file():
            text = cap.read_text(encoding="utf-8").strip()
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            caption_copied = True
        _open_folder(Path(mp4).parent)
        webbrowser.open(TIKTOK_UPLOAD_URL)
        if caption_copied:
            messagebox.showinfo(
                "TikTok",
                "Legenda copiada. Arraste o vídeo e cole a legenda com Ctrl+V.",
                parent=self,
            )
        else:
            messagebox.showinfo(
                "TikTok",
                "Pasta e upload abertos. Nenhuma legenda .txt encontrada ao lado do clipe.",
                parent=self,
            )

    def _copy_path_selected(self) -> None:
        mp4 = self._selected_mp4_path()
        if not mp4:
            messagebox.showinfo("Seleção", "Selecione uma linha na tabela de clipes.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(mp4)
        self.update()

    def _copy_all_captions(self) -> None:
        if not self._last_outputs:
            messagebox.showinfo(
                "Legendas",
                "Ainda não há resultados; gere clipes, quiz, batalha ou história primeiro.",
                parent=self,
            )
            return
        parts: list[str] = []
        for path in _sort_clip_outputs(self._last_outputs):
            cap = Path(path).with_suffix(".txt")
            if cap.is_file():
                body = cap.read_text(encoding="utf-8").strip()
                parts.append(f"=== {Path(path).name} ===\n{body}")
        if not parts:
            messagebox.showinfo("Legendas", "Nenhum .txt encontrado ao lado dos .mp4.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append("\n\n".join(parts))
        self.update()

    def _export_zip_clicked(self) -> None:
        if not self._last_outputs:
            messagebox.showinfo(
                "Exportar",
                "Ainda não há resultados; gere clipes, quiz, batalha ou história primeiro.",
                parent=self,
            )
            return
        zp = export_cortes_zip(self._last_outputs, OUTPUT_DIR)
        if zp:
            messagebox.showinfo("Exportar", f"Pacote criado:\n{zp}", parent=self)
        else:
            messagebox.showwarning("Exportar", "Não foi possível criar o .zip (arquivos ausentes?).", parent=self)

    def _send_all_to_telegram_clicked(self) -> None:
        if self._telegram_sending:
            return
        all_paths = _telegram_result_paths(OUTPUT_DIR)
        video_paths = [path for path in all_paths if path.suffix.lower() in _TELEGRAM_VIDEO_SUFFIXES]
        video_stems = {path.with_suffix("") for path in video_paths}
        standalone_captions = [
            path
            for path in all_paths
            if path.suffix.lower() in _TELEGRAM_CAPTION_SUFFIXES
            and path.with_suffix("") not in video_stems
        ]
        if not video_paths and not standalone_captions:
            messagebox.showinfo(
                "Telegram",
                "Nenhum vídeo ou legenda encontrado em resultados/.",
                parent=self,
            )
            return
        if not _cfg.TELEGRAM_BOT_TOKEN or _cfg.TELEGRAM_ALLOWED_USER_ID <= 0:
            messagebox.showwarning(
                "Telegram",
                "Configure TELEGRAM_BOT_TOKEN e TELEGRAM_ALLOWED_USER_ID no arquivo .env.",
                parent=self,
            )
            return

        self._telegram_sending = True
        self._btn_send_telegram.configure(state=tk.DISABLED)
        self._set_status("Enviando resultados para o Telegram…")
        self._append_log(
            f"\n[Telegram] Enviando {len(video_paths)} vídeo(s) com legenda(s) "
            f"e {len(standalone_captions)} legenda(s) avulsa(s)…\n"
        )

        def worker() -> None:
            async def send() -> tuple[int, int]:
                from telegram import Bot

                sent = 0
                failed = 0
                paths = [*video_paths, *standalone_captions]
                temp_root = Path(TEMP_DIR)
                temp_root.mkdir(parents=True, exist_ok=True)
                async with Bot(token=_cfg.TELEGRAM_BOT_TOKEN) as bot:
                    with tempfile.TemporaryDirectory(prefix="telegram_", dir=str(temp_root)) as temp_dir:
                        for index, path in enumerate(paths, start=1):
                            if not path.is_file():
                                failed += 1
                                self._log_q.put(f"[Telegram] Arquivo não encontrado: {path}\n")
                                continue
                            try:
                                suffix = path.suffix.lower()
                                if suffix in _TELEGRAM_VIDEO_SUFFIXES:
                                    upload_path = path
                                    if path.stat().st_size > _TELEGRAM_MAX_UPLOAD_BYTES:
                                        self._log_q.put(
                                            f"[Telegram] {path.name} acima de 49 MB; compactando para envio…\n"
                                        )
                                        upload_path = _compress_for_telegram(
                                            path, Path(temp_dir), index
                                        )
                                    caption_path = path.with_suffix(".txt")
                                    caption = (
                                        caption_path.read_text(encoding="utf-8").strip()
                                        if caption_path.is_file()
                                        else ""
                                    )
                                    kwargs = {"caption": caption[:1024]} if caption else {}
                                    with upload_path.open("rb") as file:
                                        await bot.send_video(
                                            chat_id=_cfg.TELEGRAM_ALLOWED_USER_ID,
                                            video=file,
                                            filename=path.name,
                                            supports_streaming=True,
                                            **kwargs,
                                        )
                                else:
                                    body = path.read_text(encoding="utf-8").strip()
                                    if not body:
                                        continue
                                    for start in range(0, len(body), 3900):
                                        await bot.send_message(
                                            chat_id=_cfg.TELEGRAM_ALLOWED_USER_ID,
                                            text=f"📝 {path.name}\n\n{body[start:start + 3900]}",
                                        )
                                sent += 1
                                self._log_q.put(f"[Telegram] [{index}/{len(paths)}] {path.name}\n")
                            except Exception as exc:
                                failed += 1
                                self._log_q.put(("__LOG_ERROR__", f"[Telegram] {path.name}: {exc}"))
                            if index < len(paths):
                                await asyncio.sleep(0.4)
                return sent, failed

            try:
                sent, failed = asyncio.run(send())
            except Exception as exc:
                self.after(0, lambda error=str(exc): self._finish_telegram_send(0, 0, error))
                return
            self.after(0, lambda: self._finish_telegram_send(sent, failed, None))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_telegram_send(self, sent: int, failed: int, error: str | None) -> None:
        self._telegram_sending = False
        self._btn_send_telegram.configure(
            state=tk.DISABLED if self._pipeline_running else tk.NORMAL
        )
        if error:
            self._set_status("Falha no envio para o Telegram")
            self._append_log_error(f"[Telegram] Falha geral: {error}")
            messagebox.showerror("Telegram", f"Falha ao enviar os resultados:\n{error}", parent=self)
            return
        self._set_status(f"Telegram — {sent} enviado(s), {failed} falha(s)")
        self._append_log(f"[Telegram] Concluído: {sent} enviado(s), {failed} falha(s).\n")
        if failed:
            messagebox.showwarning(
                "Telegram",
                f"{sent} resultado(s) enviado(s); {failed} falha(s). Veja o log.",
                parent=self,
            )
        else:
            messagebox.showinfo("Telegram", f"{sent} resultado(s) enviado(s).", parent=self)

    def _worker_busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _enqueue_job(self, payload: dict[str, object], *, log_intro: str) -> None:
        if self._youtube_uploading:
            messagebox.showinfo(
                "Aguarde", "O upload para o YouTube ainda está em andamento.", parent=self
            )
            return
        if self._worker_busy():
            messagebox.showinfo("Aguarde", "O processamento ainda está em andamento.", parent=self)
            return

        from app.core.cancel import reset_cancel

        reset_cancel()
        self._toggle_activity_panel(True)
        self._clear_log()
        self._last_pipeline_error = None
        self._set_running(True)
        self._set_progress_bar(0.0)
        self._append_log(log_intro)

        def worker() -> None:
            self._pipeline_worker(payload)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _start_cortes_job(self) -> None:
        locals_ = list(self._video_paths)
        url_lines = self._urls_text_for_pipeline()
        urls = collect_urls_from_lines(url_lines)
        search_theme = (self._search_theme.get() or "").strip()

        raw_box = self._txt_urls.get("1.0", "end-1c").strip()
        ph = _URLS_PLACEHOLDER.strip()
        if raw_box and raw_box != ph and getattr(self, "_urls_ph_visible", False):
            self._urls_ph_visible = False
            self._txt_urls.configure(foreground=self._text_fg)

        if search_theme and (locals_ or urls):
            messagebox.showerror(
                "Entrada exclusiva",
                "Com «Tema / busca YouTube» preenchido, limpe os arquivos locais e as URLs "
                "(ou limpe o tema e use arquivos/URLs).",
                parent=self,
            )
            return

        if not search_theme and not locals_ and not urls:
            messagebox.showerror(
                "Entrada",
                "Informe um tema de busca, selecione arquivo(s) no disco e/ou cole "
                "pelo menos uma URL (uma por linha).",
                parent=self,
            )
            return
        if (search_theme or urls) and resolve_ytdlp_executable() is None:
            messagebox.showerror(
                "yt-dlp",
                "Não encontrei o yt-dlp.\nInstale na venv: pip install yt-dlp\n"
                "Ou defina YTDLP_PATH apontando para o executável.",
                parent=self,
            )
            return
        missing = [v for v in locals_ if not Path(v).is_file()]
        if missing:
            messagebox.showerror(
                "Arquivo",
                "Um ou mais caminhos selecionados não existem mais no disco.",
                parent=self,
            )
            return

        err = _validate_hex("Cor do texto", self._color.get())
        if err:
            messagebox.showerror("Cor", err, parent=self)
            return
        err = _validate_hex("Cor de fundo", self._bg_color.get())
        if err:
            messagebox.showerror("Cor", err, parent=self)
            return

        op = int(self._opacity.get())
        if op < 0 or op > 100:
            messagebox.showerror("Opacidade", "Use um valor entre 0 e 100.", parent=self)
            return

        voice = (self._tts_voice.get() or "").strip() or None
        dub_to = self._dub_to.get()
        if dub_to == "off":
            dub_to = None
            voice = None

        melhorias = self._collect_melhorias()
        self._apply_melhorias_to_config(melhorias)

        payload: dict[str, object] = {
            "job_type": "cortes",
            "local_paths": locals_,
            "urls": urls,
            "search_theme": search_theme,
            "melhorias": melhorias,
            "pipeline": {
                "target_language": self._lang.get(),
                "posicao": self._position.get(),
                "fonte": (self._font.get() or "Arial").strip() or "Arial",
                "cor_letra": _normalize_hex(self._color.get()),
                "cor_fundo": _normalize_hex(self._bg_color.get()),
                "opacidade": op,
                "dub_to": dub_to,
                "tts_voice": voice,
            },
        }
        if search_theme:
            log_intro = (
                f"[Cortes virais] Tema «{search_theme}» — buscando no YouTube o vídeo "
                "longo com mais visualizações, depois download e cortes.\n\n"
            )
        else:
            log_intro = (
                "[Cortes virais] Iniciando… O log abaixo mostra download, transcrição, "
                "clipes e legendas.\n\n"
            )
        self._enqueue_job(payload, log_intro=log_intro)

    def _start_quiz_job(self) -> None:
        theme = (self._quiz_theme.get() or "").strip()
        if not theme:
            messagebox.showerror(
                "Quiz",
                "Informe um tema ou nicho para o quiz.",
                parent=self,
            )
            return

        try:
            count = int(self._quiz_count.get())
        except (tk.TclError, ValueError):
            count = 5
        count = max(1, min(10, count))
        self._quiz_count.set(count)

        try:
            timer_sec = int(self._quiz_timer_sec.get())
        except (tk.TclError, ValueError):
            timer_sec = 5
        timer_sec = max(3, min(10, timer_sec))
        self._quiz_timer_sec.set(timer_sec)
        self._sync_quiz_timer_label()

        err = _validate_hex("Cor de fundo do quiz", self._quiz_bg_color.get())
        if err:
            messagebox.showerror("Cor", err, parent=self)
            return

        voice = voice_id_from_label(self._quiz_tts_voice.get() or default_voice_label())
        difficulty_label = (self._quiz_difficulty.get() or "Variado").strip()
        difficulty = normalize_quiz_difficulty(difficulty_label)
        cor_fundo = _normalize_hex(self._quiz_bg_color.get())

        payload: dict[str, object] = {
            "job_type": "quiz",
            "theme": theme,
            "count": count,
            "timer_sec": float(timer_sec),
            "tts_voice": voice,
            "difficulty": difficulty,
            "cor_fundo": cor_fundo,
        }
        self._enqueue_job(
            payload,
            log_intro=(
                f"[Máquina de Quizzes] Tema={theme!r}, {count} pergunta(s), "
                f"dificuldade={difficulty_label} ({difficulty}), timer={timer_sec}s, "
                f"fundo={cor_fundo}. Acompanhe as etapas abaixo.\n\n"
            ),
        )

    def _start_tts_job(self) -> None:
        text = self._tts_text_content()
        if not text:
            messagebox.showerror(
                "Text-to-Speech",
                "Informe o texto que deseja converter em MP3.",
                parent=self,
            )
            return

        voice = voice_id_from_label(self._standalone_tts_voice.get() or default_voice_label())
        preview = text[:80] + ("…" if len(text) > 80 else "")

        payload: dict[str, object] = {
            "job_type": "tts",
            "text": text,
            "tts_voice": voice,
        }
        self._enqueue_job(
            payload,
            log_intro=(
                f"[Text-to-Speech] Voz={voice!r}. Texto: {preview!r}\n"
                "A síntese pode levar alguns segundos (local GPU ou cloud).\n\n"
            ),
        )

    def _start_batalha_job(self) -> None:
        theme = (self._batalha_theme.get() or "").strip()
        if not theme:
            messagebox.showerror(
                "Batalha 1v1",
                "Informe um tema para o duelo.",
                parent=self,
            )
            return

        modo = self._batalha_modo_from_label(self._batalha_modo_label.get() or "")
        voice = voice_id_from_label(self._batalha_tts_voice.get() or default_voice_label())

        payload: dict[str, object] = {
            "job_type": "batalha",
            "theme": theme,
            "modo": modo,
            "tts_voice": voice,
        }
        self._enqueue_job(
            payload,
            log_intro=(
                f"[Batalha 1v1] Tema={theme!r}, modo={modo}, voz={voice!r}. "
                "Groq → imagens → TTS → simulação → FFmpeg.\n\n"
            ),
        )

    def _start_historia_job(self) -> None:
        story = self._historia_text_content()
        if not story:
            messagebox.showerror(
                "História",
                "Informe o texto da história para gerar o vídeo.",
                parent=self,
            )
            return

        voice = voice_id_from_label(self._historia_tts_voice.get() or default_voice_label())
        preview = story[:80] + ("…" if len(story) > 80 else "")

        payload: dict[str, object] = {
            "job_type": "historia",
            "story_text": story,
            "tts_voice": voice,
        }
        self._enqueue_job(
            payload,
            log_intro=(
                f"[História] Voz={voice!r}. Texto: {preview!r}\n"
                "Groq → cenas → TTS + ComfyUI → FFmpeg. ComfyUI deve estar em 127.0.0.1:8188.\n\n"
            ),
        )

    def _pipeline_worker(self, payload: dict[str, object]) -> None:
        """Thread em background: roteia por `job_type` e envia `__DONE__` à fila da UI."""
        old_out, old_err = sys.stdout, sys.stderr
        w = _QueueWriter(self._log_q)
        results: list[str] = []
        had_error = False
        job_type = str(payload.get("job_type") or "cortes")

        def _on_progress(frac: float) -> None:
            self._log_q.put(("__PROGRESS__", frac))

        try:
            sys.stdout = w
            sys.stderr = w
            with gui_pipeline_log_redirect(w):
                if job_type == "quiz":
                    results, had_error = self._run_quiz_job_payload(payload, _on_progress)
                elif job_type == "batalha":
                    results, had_error = self._run_batalha_job_payload(payload, _on_progress)
                elif job_type == "historia":
                    results, had_error = self._run_historia_job_payload(payload, _on_progress)
                elif job_type == "tts":
                    results, had_error = self._run_tts_job_payload(payload, _on_progress)
                else:
                    results, had_error = self._run_cortes_job_payload(payload, _on_progress)
        except Exception as e:
            had_error = True
            ctx = {
                "quiz": "Quiz",
                "batalha": "Batalha 1v1",
                "historia": "História",
                "tts": "Text-to-Speech",
            }.get(job_type, "Cortes virais")
            self._push_error_to_log_queue(e, context=ctx)
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            self._log_q.put(("__DONE__", results, had_error))
            self._log_q.put(None)

    def _collect_melhorias(self) -> dict[str, object]:
        backend = (self._transcribe_backend.get() or "local").strip().lower()
        if backend not in ("local", "groq"):
            backend = "local"
        return {
            "TRANSCRIBE_BACKEND": backend,
            "SUBTITLE_KARAOKE": bool(self._karaoke.get()),
            "VISUAL_GRADE": bool(self._visual_grade.get()),
            "VISUAL_PROGRESS_BAR": bool(self._visual_progress.get()),
            "VISUAL_WATERMARK_TEXT": (self._visual_watermark.get() or "").strip(),
            "LOCAL_TTS_PREFERRED": bool(self._prefer_local_tts.get()),
            "SMART_CROP_ENABLED": bool(self._smart_crop.get()),
            "USE_GPU_CLIP_ENCODE": bool(self._use_gpu_encode.get()),
            "SMART_CROP_SPLIT_ENABLED": True,
        }

    @staticmethod
    def _apply_melhorias_to_config(melhorias: dict[str, object]) -> None:
        """Aplica toggles da GUI nos flags de `app.core.config` (import-time)."""
        _cfg.TRANSCRIBE_BACKEND = str(melhorias.get("TRANSCRIBE_BACKEND") or "local")
        _cfg.SUBTITLE_KARAOKE = bool(melhorias.get("SUBTITLE_KARAOKE"))
        _cfg.VISUAL_GRADE = bool(melhorias.get("VISUAL_GRADE"))
        _cfg.VISUAL_PROGRESS_BAR = bool(melhorias.get("VISUAL_PROGRESS_BAR"))
        _cfg.VISUAL_WATERMARK_TEXT = str(melhorias.get("VISUAL_WATERMARK_TEXT") or "")
        _cfg.LOCAL_TTS_PREFERRED = bool(melhorias.get("LOCAL_TTS_PREFERRED"))
        _cfg.SMART_CROP_ENABLED = bool(melhorias.get("SMART_CROP_ENABLED"))
        _cfg.USE_GPU_CLIP_ENCODE = bool(melhorias.get("USE_GPU_CLIP_ENCODE"))
        _cfg.SMART_CROP_SPLIT_ENABLED = bool(melhorias.get("SMART_CROP_SPLIT_ENABLED", True))

    def _limpar_temp(self) -> None:
        """Apaga arquivos de temp/ com mais de 2 dias (nunca toca em resultados/)."""
        if self._pipeline_running:
            messagebox.showinfo(
                "Limpar temp",
                "Aguarde o processamento terminar antes de limpar temp/.",
                parent=self,
            )
            return
        temp = Path(TEMP_DIR)
        if not temp.is_dir():
            messagebox.showinfo("Limpar temp", f"Pasta não encontrada: {temp}", parent=self)
            return
        script = _ROOT / "limpar_temp.sh"
        try:
            if script.is_file():
                proc = subprocess.run(
                    ["bash", str(script)],
                    cwd=str(_ROOT),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                out = (proc.stdout or proc.stderr or "").strip() or "temp/ limpo."
            else:
                import time

                cutoff = time.time() - 2 * 86400
                n_files = 0
                for p in temp.rglob("*"):
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
                        n_files += 1
                for p in sorted((x for x in temp.rglob("*") if x.is_dir()), reverse=True):
                    try:
                        p.rmdir()
                    except OSError:
                        pass
                out = f"temp/ limpo ({n_files} arquivo(s) antigos)."
        except Exception as e:
            messagebox.showerror("Limpar temp", f"Falha ao limpar temp/:\n{e}", parent=self)
            return
        self._append_log(f"[Melhorias] {out}\n")
        messagebox.showinfo("Limpar temp", out, parent=self)

    def _run_cortes_job_payload(
        self,
        payload: dict[str, object],
        on_progress: object,
    ) -> tuple[list[str], bool]:
        locals_ = list(payload.get("local_paths") or [])
        urls = list(payload.get("urls") or [])
        search_theme = str(payload.get("search_theme") or "").strip()
        pipeline_raw = payload.get("pipeline")
        kwargs = dict(pipeline_raw) if isinstance(pipeline_raw, dict) else {}
        melhorias_raw = payload.get("melhorias")
        if isinstance(melhorias_raw, dict):
            self._apply_melhorias_to_config(melhorias_raw)
            _pipeline_log_line(
                "Melhorias ativas: "
                f"transcribe={_cfg.TRANSCRIBE_BACKEND}, karaoke={_cfg.SUBTITLE_KARAOKE}, "
                f"grade={_cfg.VISUAL_GRADE}, barra={_cfg.VISUAL_PROGRESS_BAR}, "
                f"crop={_cfg.SMART_CROP_ENABLED}, gpu={_cfg.USE_GPU_CLIP_ENCODE}, "
                f"kokoro_dub={_cfg.LOCAL_TTS_PREFERRED}, "
                f"watermark={_cfg.VISUAL_WATERMARK_TEXT!r}."
            )

        if search_theme:
            try:
                _pipeline_log_line(f"Buscando «{search_theme}» no YouTube…")
                hit = search_youtube_top_by_views(search_theme)
                mins = hit.duration_sec / 60.0
                views_fmt = f"{hit.view_count:,}".replace(",", ".")
                ch = f" · {hit.channel}" if hit.channel else ""
                _pipeline_log_line(
                    f"Escolhido: {hit.title}{ch} · {views_fmt} views · {mins:.0f} min\n"
                    f"    {hit.url}"
                )
                urls = [hit.url]
                locals_ = []
            except Exception as e:
                self._push_error_to_log_queue(e, context="Busca por tema")
                return [], True

        videos: list[str] = list(locals_)
        source_by_path: dict[str, VideoSourceAttribution] = {}

        if locals_:
            _pipeline_log_line(f"A usar {len(locals_)} arquivo(s) local(is) do disco.")
        if urls:
            n_u = len(urls)
            max_workers = max(1, min(DOWNLOAD_MAX_WORKERS, n_u))
            _pipeline_log_line(
                f"A baixar {n_u} URL(s) da internet (até {max_workers} em paralelo) com yt-dlp…"
            )

            def _dl(idx_url: tuple[int, str]) -> tuple[int, str, object]:
                idx, u = idx_url
                site = _media_site_hint(u)
                _pipeline_log_line(f"[{idx + 1}/{n_u}] A descarregar de {site}…\n    {u}")
                result = download_video(u, TEMP_DIR)
                path = result.path
                if result.attribution:
                    _pipeline_log_line(f"[{idx + 1}/{n_u}] Canal: {result.attribution.channel}")
                _pipeline_log_line(f"[{idx + 1}/{n_u}] Download concluído → {path}")
                return idx, u, result

            with ThreadPoolExecutor(max_workers=max_workers) as dl_pool:
                ordered = sorted(dl_pool.map(_dl, enumerate(urls)), key=lambda t: t[0])
            for _i, _u, result in ordered:
                path = result.path
                videos.append(path)
                if result.attribution:
                    source_by_path[str(Path(path).resolve())] = result.attribution
            _pipeline_log_line("Todos os downloads terminaram. A iniciar o pipeline de cortes.")

        backend = (_cfg.TRANSCRIBE_BACKEND or "local").strip().lower()
        tr_label = "local (faster-whisper GPU)" if backend == "local" else "Groq Whisper"
        _pipeline_log_line(
            f"Transcrição ({tr_label}), momentos virais, legendas no vídeo e textos para TikTok — "
            "pode demorar vários minutos."
        )
        pipeline_kw = dict(kwargs)
        if urls:
            pipeline_kw["source_by_path"] = source_by_path
        try:
            out = run_pipeline(
                video_path=videos,
                progress=on_progress,
                **pipeline_kw,
            )
        except Exception as e:
            self._push_error_to_log_queue(e, context="Cortes virais")
            return [], True
        _pipeline_log_line("Geração de clipes concluída. Arquivos:")
        for p in out:
            print(f"  • {p}\n", flush=True)
        return list(out), False

    def _run_quiz_job_payload(
        self,
        payload: dict[str, object],
        on_progress: object,
    ) -> tuple[list[str], bool]:
        quiz_payload = {
            "job_type": "quiz",
            "theme": payload.get("theme"),
            "count": payload.get("count"),
            "timer_sec": payload.get("timer_sec"),
            "tts_voice": payload.get("tts_voice"),
            "difficulty": payload.get("difficulty"),
            "cor_fundo": payload.get("cor_fundo"),
        }
        on_progress(0.05)
        try:
            result = run_quiz_pipeline(quiz_payload, log_queue=self._log_q, cancel_event=None)
        except Exception as e:
            self._push_error_to_log_queue(e, context="Quiz")
            return [], True
        on_progress(1.0)
        mp4 = str(result.video_path)
        _pipeline_log_line(f"Quiz concluído: {mp4}")
        if result.caption_path:
            print(f"  Legenda: {result.caption_path}\n", flush=True)
        return [mp4], False

    def _run_batalha_job_payload(
        self,
        payload: dict[str, object],
        on_progress: object,
    ) -> tuple[list[str], bool]:
        try:
            result = run_batalha_pipeline_from_payload(
                dict(payload),
                log_queue=self._log_q,
                cancel_event=None,
                progress=on_progress,
            )
        except Exception as e:
            self._push_error_to_log_queue(e, context="Batalha 1v1")
            return [], True
        mp4 = str(result.video_path)
        _pipeline_log_line(f"Batalha concluída: {mp4}")
        if result.caption_path:
            print(f"  Legenda: {result.caption_path}\n", flush=True)
        return [mp4], False

    def _run_historia_job_payload(
        self,
        payload: dict[str, object],
        on_progress: object,
    ) -> tuple[list[str], bool]:
        story_text = str(payload.get("story_text") or "").strip()
        voice = str(payload.get("tts_voice") or voice_id_from_label(default_voice_label()))
        on_progress(0.05)
        _pipeline_log_line(
            "A dividir história em cenas (Groq), gerar TTS/ComfyUI e montar com FFmpeg…"
        )
        try:
            result = run_historia_pipeline(story_text, voice=voice)
        except Exception as e:
            self._push_error_to_log_queue(e, context="História")
            return [], True
        on_progress(1.0)
        mp4 = str(result.video_path)
        _pipeline_log_line(f"História concluída: {mp4} ({len(result.scenes)} cena(s))")
        print(f"  • {mp4}\n", flush=True)
        return [mp4], False

    def _run_tts_job_payload(
        self,
        payload: dict[str, object],
        on_progress: object,
    ) -> tuple[list[str], bool]:
        from app.tts.tts_standalone import synthesize_tts_mp3

        text = str(payload.get("text") or "")
        voice = str(payload.get("tts_voice") or voice_id_from_label(default_voice_label()))
        on_progress(0.1)
        _pipeline_log_line("A sintetizar locução…")
        try:
            mp3 = synthesize_tts_mp3(text, voice)
        except Exception as e:
            self._push_error_to_log_queue(e, context="Text-to-Speech")
            return [], True
        on_progress(1.0)
        _pipeline_log_line(f"MP3 pronto: {mp3}")
        print(f"  • {mp3}\n", flush=True)
        return [mp3], False


def main() -> None:
    setup_logging(gui_quiet=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    CortesApp().mainloop()


if __name__ == "__main__":
    main()
