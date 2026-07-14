"""
Interface gráfica com abas para múltiplos geradores (projeto.md §13.1).

- Aba «Cortes Virais»: pipeline de clipes (mesmas opções do main.py).
- Aba «Máquina de Quizzes»: geração de vídeo quiz via `app.pipelines.quiz.quiz_pipeline`.
- Aba «Batalha 1v1»: duelo por física 2D (`app.pipelines.batalha.batalha_pipeline`).
- Aba «História»: vídeo narrado com cenas IA (`app.pipelines.historia.historia_pipeline` + ComfyUI).
- Aba «Text-to-Speech»: texto → MP3 (local Kokoro / Gemini / Edge) com pré-ouvir a voz.
- Log e tabela de resultados permanecem globais na parte inferior.

Execute na raiz do projeto:
    python gui.py
"""

from __future__ import annotations

import _venv_reexec

_venv_reexec.ensure_venv(__file__)

from app.core.linux_desktop_bootstrap import apply_linux_desktop_defaults

apply_linux_desktop_defaults()

import io
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
# Garante caminhos relativos (resultados/, temp/) a partir da pasta do projeto
_ROOT = Path(__file__).resolve().parent
os.chdir(_ROOT)

import app.core.config as _cfg
from app.core.config import (
    DOWNLOAD_MAX_WORKERS,
    EDGE_TTS_VOICE_PT,
    OUTPUT_DIR,
    TEMP_DIR,
    TIKTOK_UPLOAD_URL,
)
from app.tts.gemini_tts import gemini_tts_available
from app.tts.local_tts import local_tts_available
from app.tts.tts_voices import (
    default_voice_label,
    gui_voice_labels,
    voice_id_from_label,
)
from app.gui.gui_export import desktop_notify, export_cortes_zip, ffprobe_duration_seconds, format_duration_hms
from app.gui.studio_theme import (
    EDGE,
    INK,
    INK2,
    MOSS,
    MOSS_DK,
    MOSS_HI,
    MUTED,
    MUTED_SOFT,
    PANEL,
    STATUS_BG,
    TEXT,
    StudioTheme,
    configure_studio_theme,
    configure_ui_fonts,
    load_photo,
    load_photo_resized,
)
from app.core.logging_setup import gui_pipeline_log_redirect, setup_logging
from app.pipelines.cortes.pipeline import run_pipeline
from app.pipelines.batalha.batalha_pipeline import normalize_batalha_modo, run_batalha_pipeline_from_payload
from app.pipelines.historia.historia_pipeline import run_historia_pipeline
from app.pipelines.quiz.quiz_pipeline import normalize_quiz_difficulty, run_quiz_pipeline
from app.tts.tts_standalone import play_audio_file, synthesize_tts_preview
from app.download.ytdlp_download import (
    VideoSourceAttribution,
    collect_urls_from_lines,
    download_video,
    resolve_ytdlp_executable,
)

# Espaçamento visual (px): micro / próximo / seção / hero
_PX_MICRO = 6
_PX_NEAR = 12
_PX_SECTION = 22
_PX_HERO = 28

# Texto de placeholder da área de URLs (uma linha; cor aplicada via hint)
_URLS_PLACEHOLDER = (
    "Cole as URLs aqui — uma por linha. Ex.: https://www.youtube.com/watch?v=… "
    "(yt-dlp baixa para temp/ antes do pipeline.)"
)

# Texto longo (yt-dlp / cookies) exibido só via botão [?]
_URLS_HELP_TEXT = (
    "Cada URL é baixada com yt-dlp para a pasta temp/ antes do pipeline.\n\n"
    "Se o YouTube responder 403: pip install -U \"yt-dlp[default]\" "
    "e configure cookies (veja .env.example)."
)

# Alias de tokens (compat com código legado da GUI)
_MUTED_NOTE_FG = MUTED_SOFT
_SIDEBAR_BG = INK2
_SIDEBAR_FG = MUTED
_STATUS_BG = STATUS_BG
_STATUS_FG = MUTED_SOFT

# Rótulos limpos (sem emoji “XP”) — tipografia + accent fazem o trabalho
_IC_VIDEO = "▸"
_IC_SETTINGS = "◈"
_IC_MIC = "◎"
_IC_RUN = "⚡"
_IC_CLIPBOARD = "☰"
_IC_LOG = "⌁"
_IC_DONE = "✓"
_IC_FOLDER = "◫"
_IC_QUIZ = "▣"
_IC_SPEAKER = "♫"
_IC_BATALHA = "⚔"
_IC_HISTORIA = "§"

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


def _configure_modern_theme(root: tk.Tk) -> StudioTheme:
    """Tema Editing Bay (musgo/ink) via app.gui.studio_theme."""
    return configure_studio_theme(root)


def _configure_ui_fonts(root: tk.Tk) -> None:
    configure_ui_fonts(root)


class CortesApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Studio Cortes — Editing Bay")
        _configure_ui_fonts(self)
        self._theme = _configure_modern_theme(self)
        self._text_bg = self._theme.text_bg
        self._text_fg = self._theme.text_fg
        self._hint_fg = self._theme.hint_fg
        self._is_dark_theme = self._theme.is_dark
        self._log_fg = self._theme.log_fg
        self._urls_ph_visible = False
        self._sidebar_buttons: list[ttk.Button] = []
        self._photos: list[tk.PhotoImage] = []  # evita GC dos PhotoImage
        self._status_var = tk.StringVar(value="Pronto")
        self.configure(background=INK)
        self.minsize(1080, 720)
        self.geometry("1280x900")

        self._video_path = tk.StringVar()
        self._video_paths: list[str] = []
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

        self._build_ui()
        self.after(80, self._drain_log_queue)
        self.after(120, self._position_log_pane_sash)

    def _position_log_pane_sash(self) -> None:
        """Garante altura útil do painel de log após o primeiro layout (ttk sem minsize no Linux)."""
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

    def _make_surface(self, parent: tk.Misc, *, expand: bool = False) -> ttk.Frame:
        """Uma superfície única (borda suave) — agrupa conteúdo sem cards empilhados."""
        wrap = tk.Frame(parent, bg=INK2, highlightthickness=1, highlightbackground=EDGE)
        wrap.pack(fill=tk.BOTH if expand else tk.X, expand=expand, pady=(0, _PX_NEAR))
        inner = ttk.Frame(
            wrap,
            style=self._theme.card_style,
            padding=(_PX_SECTION, _PX_NEAR + 2, _PX_SECTION, _PX_SECTION - 2),
        )
        inner.pack(fill=tk.BOTH, expand=True)
        return inner

    def _make_section(
        self,
        parent: tk.Misc,
        title: str,
        icon: str,
        *,
        compact: bool = False,
    ) -> ttk.Frame:
        """Bloco interno: só título + corpo (sem card/borda própria)."""
        block = ttk.Frame(parent, style=self._theme.card_style)
        block.pack(fill=tk.X, pady=(0, _PX_NEAR if compact else _PX_SECTION - 6))
        head = ttk.Frame(block, style=self._theme.card_style)
        head.pack(fill=tk.X, pady=(0, _PX_MICRO))
        ttk.Label(
            head,
            text=f"{icon}  {title}".upper() if compact else f"{icon}  {title}",
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

    def _select_sidebar_tab(self, index: int) -> None:
        self._notebook.select(index)
        for i, btn in enumerate(self._sidebar_buttons):
            btn.configure(style="SidebarActive.TButton" if i == index else "Sidebar.TButton")

    def _on_notebook_tab_changed(self, _event: tk.Event | None = None) -> None:
        try:
            idx = self._notebook.index(self._notebook.select())
        except (tk.TclError, ValueError):
            return
        for i, btn in enumerate(self._sidebar_buttons):
            btn.configure(style="SidebarActive.TButton" if i == idx else "Sidebar.TButton")

    def _build_ui(self) -> None:
        sec = self._theme.secondary_button_style
        cb_st = self._theme.checkbutton_style
        base = tkfont.nametofont("TkDefaultFont").actual()

        # --- Fundo atmosférico (canvas) ---
        self._bg_canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=INK)
        self._bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._bg_photo = self._keep_photo(load_photo_resized("bg_app.png", (1600, 1000), master=self))
        if self._bg_photo is not None:
            self._bg_canvas_img = self._bg_canvas.create_image(0, 0, anchor=tk.NW, image=self._bg_photo)
        else:
            self._bg_canvas_img = None
        self.bind("<Configure>", self._on_root_configure, add="+")

        shell = tk.Frame(self, bg=INK, highlightthickness=0)
        shell.place(x=0, y=0, relwidth=1, relheight=1)

        # --- Header ---
        header = tk.Frame(shell, height=64, bg=INK2, highlightthickness=0)
        header.pack(side=tk.TOP, fill=tk.X)
        header.pack_propagate(False)
        hdr_img = self._keep_photo(load_photo_resized("header_bar.png", (1600, 64), master=self))
        if hdr_img is not None:
            tk.Label(header, image=hdr_img, bg=INK2, bd=0).place(x=0, y=0, relwidth=1, relheight=1)
        logo = self._keep_photo(load_photo("logo_48.png", master=self))
        brand_row = tk.Frame(header, bg=INK2)
        brand_row.pack(side=tk.LEFT, padx=(_PX_SECTION, 0), pady=8)
        if logo is not None:
            tk.Label(brand_row, image=logo, bg=INK2, bd=0).pack(side=tk.LEFT, padx=(0, 12))
        brand_txt = tk.Frame(brand_row, bg=INK2)
        brand_txt.pack(side=tk.LEFT)
        tk.Label(
            brand_txt,
            text="STUDIO CORTES",
            bg=INK2,
            fg=TEXT,
            font=(base["family"], 14, "bold"),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        tk.Label(
            brand_txt,
            text="Editing Bay  ·  cortes virais com IA",
            bg=INK2,
            fg=MUTED,
            font=(base["family"], 8),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        tk.Frame(header, width=3, bg=MOSS, bd=0).pack(side=tk.LEFT, fill=tk.Y, padx=(18, 0), pady=14)

        # --- Body ---
        body = tk.Frame(shell, bg=INK, highlightthickness=0)
        body.pack(fill=tk.BOTH, expand=True)

        # Sidebar com textura
        sidebar = tk.Frame(body, width=232, bg=INK2, highlightthickness=0)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        side_bg = self._keep_photo(load_photo_resized("sidebar_bg.png", (232, 1000), master=self))
        if side_bg is not None:
            tk.Label(sidebar, image=side_bg, bg=INK2, bd=0).place(x=0, y=0, relwidth=1, relheight=1)

        nav_wrap = tk.Frame(sidebar, bg=INK2)
        nav_wrap.pack(fill=tk.X, pady=(_PX_SECTION, 0))
        tk.Label(
            nav_wrap,
            text="ESPAÇOS",
            bg=INK2,
            fg=MUTED_SOFT,
            font=(base["family"], 8, "bold"),
            anchor=tk.W,
            padx=_PX_SECTION,
        ).pack(fill=tk.X, pady=(0, _PX_NEAR))

        nav_data = [
            ("Cortes Virais", 0),
            ("Máquina de Quizzes", 1),
            ("Batalha 1v1", 2),
            ("História", 3),
            ("Text-to-Speech", 4),
        ]
        self._nav_idle = self._keep_photo(load_photo_resized("nav_idle.png", (220, 44), master=self))
        self._nav_active = self._keep_photo(load_photo_resized("nav_active.png", (220, 44), master=self))
        for label, idx in nav_data:
            btn = ttk.Button(
                nav_wrap,
                text=f"  {label}",
                command=lambda i=idx: self._select_sidebar_tab(i),
                style="SidebarActive.TButton" if idx == 0 else "Sidebar.TButton",
            )
            btn.pack(fill=tk.X, padx=6, pady=2)
            self._sidebar_buttons.append(btn)

        tk.Frame(sidebar, bg=INK2).pack(fill=tk.BOTH, expand=True)
        bottom_wrap = tk.Frame(sidebar, bg=INK2, padx=_PX_NEAR, pady=_PX_SECTION)
        bottom_wrap.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(
            bottom_wrap,
            text="Abrir resultados",
            command=lambda: _open_folder(OUTPUT_DIR),
            style=sec,
        ).pack(fill=tk.X, pady=(0, _PX_MICRO))
        ttk.Button(
            bottom_wrap,
            text="Limpar log",
            command=self._clear_log,
            style=sec,
        ).pack(fill=tk.X)

        # Content
        content_outer = tk.Frame(body, bg=INK, highlightthickness=0)
        content_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = ttk.Frame(content_outer, style="Content.TFrame", padding=(_PX_SECTION, _PX_NEAR, _PX_SECTION, _PX_SECTION))
        content.pack(fill=tk.BOTH, expand=True)

        self._notebook = ttk.Notebook(content)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        tab_cortes = ttk.Frame(self._notebook, padding=(_PX_SECTION, _PX_NEAR, _PX_SECTION, _PX_SECTION))
        tab_quiz = ttk.Frame(self._notebook, padding=(_PX_SECTION, _PX_NEAR, _PX_SECTION, _PX_SECTION))
        tab_batalha = ttk.Frame(self._notebook, padding=(_PX_SECTION, _PX_NEAR, _PX_SECTION, _PX_SECTION))
        tab_historia = ttk.Frame(self._notebook, padding=(_PX_SECTION, _PX_NEAR, _PX_SECTION, _PX_SECTION))
        tab_tts = ttk.Frame(self._notebook, padding=(_PX_SECTION, _PX_NEAR, _PX_SECTION, _PX_SECTION))
        self._notebook.add(tab_cortes, text="  Cortes Virais  ")
        self._notebook.add(tab_quiz, text="  Quiz  ")
        self._notebook.add(tab_batalha, text="  Batalha  ")
        self._notebook.add(tab_historia, text="  História  ")
        self._notebook.add(tab_tts, text="  TTS  ")

        self._build_tab_cortes(tab_cortes, sec=sec, base=base)
        self._build_tab_quiz(tab_quiz, sec=sec, base=base)
        self._build_tab_batalha(tab_batalha, sec=sec, base=base)
        self._build_tab_historia(tab_historia, sec=sec, base=base)
        self._build_tab_tts(tab_tts, sec=sec, base=base)

        self._notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        run_surface = self._make_surface(content)
        run_head = self._make_section(run_surface, "Execução e pós-processo", _IC_RUN, compact=True)
        f_primary = ttk.Frame(run_head, style=self._theme.card_style)
        f_primary.pack(fill=tk.X, pady=(0, _PX_MICRO))
        self._btn_cancel = ttk.Button(
            f_primary,
            text="Cancelar processamento",
            command=self._cancel_pipeline,
            state=tk.DISABLED,
            style=sec,
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

        prog_wrap = ttk.Frame(run_head, style=self._theme.card_style)
        prog_wrap.pack(fill=tk.X, pady=(_PX_MICRO, 0))
        self._prog_track = tk.Frame(prog_wrap, height=8, bg=INK, highlightthickness=0)
        self._prog_track.pack(fill=tk.X)
        self._prog_fill = tk.Frame(self._prog_track, height=8, bg=MOSS, highlightthickness=0)

        results_surface = self._make_surface(content, expand=True)
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
            "Bem-vindo ao Editing Bay.\n"
            "Progresso de qualquer aba aparece aqui (downloads, transcrição, FFmpeg).\n"
            "Use «Gerar» na aba correspondente.\n\n",
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

        self._img_tiktok = self._keep_photo(load_photo_resized("btn_tiktok.png", (220, 44), master=self))
        if self._img_tiktok is not None:
            btn_tt = tk.Button(
                r_a,
                image=self._img_tiktok,
                command=self._post_to_tiktok_selected,
                bd=0,
                highlightthickness=0,
                bg=PANEL,
                activebackground=PANEL,
                cursor="hand2",
            )
            btn_tt.pack(side=tk.LEFT, padx=(0, _PX_NEAR), pady=_PX_MICRO)
            self._remember_idle(btn_tt, "normal")
        else:
            ttk.Button(
                r_a,
                text="Postar no TikTok (selecionado)",
                command=self._post_to_tiktok_selected,
                style=sec,
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
        ttk.Label(
            status_bar,
            textvariable=self._status_var,
            style="Status.TLabel",
            padding=(_PX_SECTION, _PX_MICRO + 2),
        ).pack(side=tk.LEFT)
        tk.Label(
            status_bar,
            text="Editing Bay",
            bg=STATUS_BG,
            fg=MOSS,
            font=(base["family"], 8, "bold"),
            padx=_PX_SECTION,
        ).pack(side=tk.RIGHT)

        self._apply_text_widget_theme()
        if self._urls_ph_visible:
            self._txt_urls.configure(foreground=self._hint_fg)
        self._toggle_tts()

    def _on_root_configure(self, event: tk.Event | None = None) -> None:
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_bg_photo", None) is None or getattr(self, "_bg_canvas_img", None) is None:
            return
        try:
            w = max(800, int(self.winfo_width()))
            h = max(600, int(self.winfo_height()))
        except tk.TclError:
            return
        # Reescala ocasionalmente (throttle)
        key = (w // 40, h // 40)
        if getattr(self, "_bg_scale_key", None) == key:
            return
        self._bg_scale_key = key
        img = self._keep_photo(load_photo_resized("bg_app.png", (w, h), master=self))
        if img is None:
            return
        self._bg_photo = img
        self._bg_canvas.itemconfigure(self._bg_canvas_img, image=img)
        self._bg_canvas.coords(self._bg_canvas_img, 0, 0)

    def _build_tab_cortes(self, parent: ttk.Frame, *, sec: str, base: dict) -> None:
        """Aba 1 — inputs do pipeline de cortes virais (uma superfície, blocos internos)."""
        surface = self._make_surface(parent)
        cb_st = self._theme.checkbutton_style

        cols = ttk.Frame(surface, style=self._theme.card_style)
        cols.pack(fill=tk.X, expand=False)
        cols.columnconfigure(0, weight=1, uniform="topcols")
        cols.columnconfigure(1, weight=1, uniform="topcols")
        left = ttk.Frame(cols, style=self._theme.card_style)
        left.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, _PX_SECTION))
        right = ttk.Frame(cols, style=self._theme.card_style)
        right.grid(row=0, column=1, sticky=tk.NSEW)

        v_body = self._make_section(left, "Vídeo de entrada", _IC_VIDEO, compact=True)
        row = ttk.Frame(v_body, style=self._theme.card_style)
        row.pack(fill=tk.X, pady=(0, _PX_NEAR))
        self._btn_pick = ttk.Button(
            row,
            text="Escolher arquivo(s)…",
            command=self._pick_video,
            style=sec,
        )
        self._btn_pick.pack(side=tk.LEFT)
        self._remember_idle(self._btn_pick, "normal")
        self._lbl_video = ttk.Label(row, textvariable=self._video_path, wraplength=380, style="Field.TLabel")
        self._lbl_video.pack(side=tk.LEFT, padx=(_PX_NEAR, 0), fill=tk.X, expand=True)

        tip_row = ttk.Frame(v_body, style=self._theme.card_style)
        tip_row.pack(fill=tk.X, pady=(0, _PX_MICRO))
        ttk.Label(
            tip_row,
            text="URLs opcionais — uma por linha",
            style="Field.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(
            tip_row,
            text="?",
            width=2,
            command=self._urls_help_dialog,
            style=sec,
        ).pack(side=tk.LEFT, padx=(_PX_MICRO, 0))

        fixed_f = tkfont.nametofont("TkFixedFont")
        self._txt_urls = ScrolledText(
            v_body,
            height=4,
            wrap=tk.WORD,
            font=fixed_f,
            relief=tk.FLAT,
            borderwidth=0,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=EDGE,
            highlightcolor=MOSS,
        )
        self._txt_urls.pack(fill=tk.BOTH, expand=False)
        self._remember_idle(self._txt_urls, tk.NORMAL)
        self._txt_urls.insert("1.0", _URLS_PLACEHOLDER)
        self._urls_ph_visible = True
        self._txt_urls.bind("<FocusIn>", self._urls_focus_in)
        self._txt_urls.bind("<FocusOut>", self._urls_focus_out)

        # Direita: legendas + dublagem no mesmo fluxo
        f_sub = self._make_section(right, "Legendas", _IC_SETTINGS, compact=True)

        grid = ttk.Frame(f_sub, style=self._theme.card_style)
        grid.pack(fill=tk.X, pady=(0, _PX_NEAR))
        for col, (label, var, values, width, attr) in enumerate((
            ("Idioma", self._lang, ("pt", "en"), 8, "_cb_lang"),
            ("Posição", self._position, ("bottom", "top"), 10, "_cb_position"),
        )):
            blk = ttk.Frame(grid, style=self._theme.card_style)
            blk.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
            ttk.Label(blk, text=label, style="Field.TLabel").pack(anchor=tk.W)
            cb = ttk.Combobox(
                blk,
                textvariable=var,
                values=values,
                state="readonly",
                width=width,
            )
            cb.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
            self._remember_idle(cb, "readonly")
            setattr(self, attr, cb)

        blk_font = ttk.Frame(f_sub, style=self._theme.card_style)
        blk_font.pack(anchor=tk.W, fill=tk.X, pady=(0, _PX_NEAR))
        ttk.Label(blk_font, text="Fonte", style="Field.TLabel").pack(anchor=tk.W)
        self._ent_font = ttk.Entry(blk_font, textvariable=self._font, width=28)
        self._ent_font.pack(anchor=tk.W, fill=tk.X, pady=(_PX_MICRO, 0))
        self._remember_idle(self._ent_font, "normal")

        blk_colors = ttk.Frame(f_sub, style=self._theme.card_style)
        blk_colors.pack(anchor=tk.W, fill=tk.X, pady=(0, _PX_NEAR))
        c_txt = ttk.Frame(blk_colors, style=self._theme.card_style)
        c_txt.pack(side=tk.LEFT, padx=(0, _PX_SECTION))
        ttk.Label(c_txt, text="Cor do texto", style="Field.TLabel").pack(anchor=tk.W)
        row_ct = ttk.Frame(c_txt, style=self._theme.card_style)
        row_ct.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._ent_color = ttk.Entry(row_ct, textvariable=self._color, width=10)
        self._ent_color.pack(side=tk.LEFT)
        self._btn_color_text = ttk.Button(
            row_ct, text="Paleta…", command=lambda: self._pick_color(self._color), style=sec,
        )
        self._btn_color_text.pack(side=tk.LEFT, padx=(_PX_MICRO, 0))
        self._remember_idle(self._ent_color, "normal")
        self._remember_idle(self._btn_color_text, "normal")

        c_bg = ttk.Frame(blk_colors, style=self._theme.card_style)
        c_bg.pack(side=tk.LEFT)
        ttk.Label(c_bg, text="Fundo", style="Field.TLabel").pack(anchor=tk.W)
        row_cb = ttk.Frame(c_bg, style=self._theme.card_style)
        row_cb.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._ent_bg = ttk.Entry(row_cb, textvariable=self._bg_color, width=10)
        self._ent_bg.pack(side=tk.LEFT)
        self._btn_color_bg = ttk.Button(
            row_cb, text="Paleta…", command=lambda: self._pick_color(self._bg_color), style=sec,
        )
        self._btn_color_bg.pack(side=tk.LEFT, padx=(_PX_MICRO, 0))
        self._remember_idle(self._ent_bg, "normal")
        self._remember_idle(self._btn_color_bg, "normal")

        op_block = ttk.Frame(f_sub, style=self._theme.card_style)
        op_block.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(op_block, text="Opacidade do fundo", style="Field.TLabel").pack(anchor=tk.W)
        op_line = ttk.Frame(op_block, style=self._theme.card_style)
        op_line.pack(fill=tk.X, pady=(_PX_MICRO, 0))
        self._sc_opacity = ttk.Scale(
            op_line,
            from_=0,
            to=100,
            variable=self._opacity,
            orient=tk.HORIZONTAL,
            command=self._on_opacity_scale,
            style="Horizontal.TScale",
            length=200,
        )
        self._sc_opacity.pack(side=tk.LEFT)
        self._remember_idle(self._sc_opacity, "normal")
        self._lbl_opacity_pct = ttk.Label(op_line, text=f"{int(self._opacity.get())}%", width=5, style="Field.TLabel")
        self._lbl_opacity_pct.pack(side=tk.LEFT, padx=(_PX_NEAR, 0))

        dub_body = self._make_section(right, "Dublagem", _IC_MIC, compact=True)
        dub_row = ttk.Frame(dub_body, style=self._theme.card_style)
        dub_row.pack(fill=tk.X)
        blk_mode = ttk.Frame(dub_row, style=self._theme.card_style)
        blk_mode.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
        ttk.Label(blk_mode, text="Modo", style="Field.TLabel").pack(anchor=tk.W)
        self._cb_dub = ttk.Combobox(
            blk_mode,
            textvariable=self._dub_to,
            values=("off", "en", "pt"),
            state="readonly",
            width=8,
        )
        self._cb_dub.pack(anchor=tk.W, pady=(_PX_MICRO, 0))
        self._remember_idle(self._cb_dub, "readonly")
        self._cb_dub.bind("<<ComboboxSelected>>", lambda _e: self._toggle_tts())

        b_voice = ttk.Frame(dub_row, style=self._theme.card_style)
        b_voice.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(b_voice, text="Voz TTS (opcional)", style="Field.TLabel").pack(anchor=tk.W)
        self._ent_voice = ttk.Entry(b_voice, textvariable=self._tts_voice)
        self._ent_voice.pack(anchor=tk.W, fill=tk.X, pady=(_PX_MICRO, 0))
        self._remember_idle(self._ent_voice, "normal")
        self._lbl_voice_hint = ttk.Label(
            dub_body,
            text="Vazio → Kokoro (se ligado) ou Edge do .env",
            style="Field.TLabel",
        )
        self._lbl_voice_hint.pack(anchor=tk.W, pady=(_PX_MICRO, 0))

        # Melhorias — faixa compacta na mesma superfície
        mel_body = self._make_section(surface, "Melhorias", _IC_SETTINGS, compact=True)
        mel_top = ttk.Frame(mel_body, style=self._theme.card_style)
        mel_top.pack(fill=tk.X, pady=(0, _PX_MICRO))
        ttk.Label(mel_top, text="Transcrição", style="Field.TLabel").pack(side=tk.LEFT)
        self._cb_transcribe = ttk.Combobox(
            mel_top,
            textvariable=self._transcribe_backend,
            values=("local", "groq"),
            state="readonly",
            width=10,
        )
        self._cb_transcribe.pack(side=tk.LEFT, padx=(_PX_NEAR, _PX_SECTION))
        self._remember_idle(self._cb_transcribe, "readonly")
        ttk.Label(mel_top, text="Watermark", style="Field.TLabel").pack(side=tk.LEFT)
        self._ent_watermark = ttk.Entry(mel_top, textvariable=self._visual_watermark, width=16)
        self._ent_watermark.pack(side=tk.LEFT, padx=(_PX_NEAR, _PX_NEAR))
        self._remember_idle(self._ent_watermark, "normal")
        self._btn_limpar_temp = ttk.Button(
            mel_top,
            text="Limpar temp/",
            command=self._limpar_temp,
            style=sec,
        )
        self._btn_limpar_temp.pack(side=tk.LEFT)
        self._remember_idle(self._btn_limpar_temp, "normal")

        mel_checks = ttk.Frame(mel_body, style=self._theme.card_style)
        mel_checks.pack(fill=tk.X)
        for text, var in (
            ("Karaokê", self._karaoke),
            ("Grade", self._visual_grade),
            ("Barra", self._visual_progress),
            ("Smart crop", self._smart_crop),
            ("GPU encode", self._use_gpu_encode),
            ("Kokoro na dub", self._prefer_local_tts),
        ):
            cb = ttk.Checkbutton(mel_checks, text=text, variable=var, style=cb_st)
            cb.pack(side=tk.LEFT, padx=(0, _PX_NEAR))
            self._remember_idle(cb, "normal")

        run_row = ttk.Frame(surface, style=self._theme.card_style)
        run_row.pack(fill=tk.X, pady=(_PX_NEAR, 0))
        ttk.Frame(run_row, style=self._theme.card_style).pack(side=tk.LEFT, expand=True)
        run_center = ttk.Frame(run_row, style=self._theme.card_style)
        run_center.pack(side=tk.LEFT)
        ttk.Frame(run_row, style=self._theme.card_style).pack(side=tk.LEFT, expand=True)
        self._img_run = self._keep_photo(load_photo_resized("btn_accent.png", (280, 56), master=self))
        self._img_run_hot = self._keep_photo(load_photo_resized("btn_accent_hot.png", (280, 56), master=self))
        if self._img_run is not None:
            self._btn_run_cortes = tk.Button(
                run_center,
                image=self._img_run,
                command=self._start_cortes_job,
                bd=0,
                highlightthickness=0,
                bg=PANEL,
                activebackground=PANEL,
                cursor="hand2",
            )
            if self._img_run_hot is not None:
                self._btn_run_cortes.bind(
                    "<Enter>",
                    lambda _e: self._btn_run_cortes.configure(image=self._img_run_hot),
                )
                self._btn_run_cortes.bind(
                    "<Leave>",
                    lambda _e: self._btn_run_cortes.configure(image=self._img_run),
                )
        else:
            self._btn_run_cortes = ttk.Button(
                run_center,
                text=f"{_IC_RUN}  Gerar clipes",
                command=self._start_cortes_job,
                style="Accent.TButton",
            )
        self._btn_run_cortes.pack()
        self._remember_idle(self._btn_run_cortes, "normal")

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
        """Barra verde 0–1 (largura relativa ao trilho). Só atualiza na thread principal."""
        if not hasattr(self, "_prog_fill") or not hasattr(self, "_prog_track"):
            return
        frac = max(0.0, min(1.0, float(fraction)))
        if frac <= 0.0:
            self._prog_fill.place_forget()
            return
        self._prog_fill.place(
            in_=self._prog_track,
            x=0,
            y=0,
            anchor="nw",
            relheight=1.0,
            relwidth=frac,
        )

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
        if running:
            for w, _idle in self._idle_states.items():
                w.configure(state=tk.DISABLED)
            self._set_status("Processando…")
        else:
            for w, idle in self._idle_states.items():
                w.configure(state=idle)
            self._toggle_tts()
            if not running:
                self._set_tts_preview_busy(False)
                self._set_status("Pronto")

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

    def _worker_busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _enqueue_job(self, payload: dict[str, object], *, log_intro: str) -> None:
        if self._worker_busy():
            messagebox.showinfo("Aguarde", "O processamento ainda está em andamento.", parent=self)
            return

        from app.core.cancel import reset_cancel

        reset_cancel()
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

        raw_box = self._txt_urls.get("1.0", "end-1c").strip()
        ph = _URLS_PLACEHOLDER.strip()
        if raw_box and raw_box != ph and getattr(self, "_urls_ph_visible", False):
            self._urls_ph_visible = False
            self._txt_urls.configure(foreground=self._text_fg)

        if not locals_ and not urls:
            messagebox.showerror(
                "Entrada",
                "Selecione arquivo(s) no disco e/ou cole pelo menos uma URL (uma por linha).",
                parent=self,
            )
            return
        if urls and resolve_ytdlp_executable() is None:
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
        self._enqueue_job(
            payload,
            log_intro=(
                "[Cortes virais] Iniciando… O log abaixo mostra download, transcrição, "
                "clipes e legendas.\n\n"
            ),
        )

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
            # Split continua desligado na GUI (renderer 6B.4 adiado).
            "SMART_CROP_SPLIT_ENABLED": False,
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
        _cfg.SMART_CROP_SPLIT_ENABLED = bool(melhorias.get("SMART_CROP_SPLIT_ENABLED", False))

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
    CortesApp().mainloop()


if __name__ == "__main__":
    main()
