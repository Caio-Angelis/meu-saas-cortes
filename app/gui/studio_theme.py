"""Tema visual «Editing Bay» — musgo/ink + assets em assets/gui/."""
from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import NamedTuple

# --- Tokens (hex) ---
INK = "#0B1210"
INK2 = "#111C18"
PANEL = "#16241F"
PANEL2 = "#1C2E27"
EDGE = "#2D483A"
MOSS = "#3D8F66"
MOSS_HI = "#5CB888"
MOSS_DK = "#245C40"
TEXT = "#E8F0EC"
MUTED = "#94A89E"
MUTED_SOFT = "#6F8578"
STATUS_BG = "#080E0C"
DANGER = "#E06A5C"
FOCUS = "#7BC9A3"

_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "gui"


class StudioTheme(NamedTuple):
    text_bg: str
    text_fg: str
    hint_fg: str
    is_dark: bool
    card_style: str
    checkbutton_style: str
    secondary_button_style: str
    log_fg: str
    accent_strip: str
    urls_inset_bg: str
    panel: str
    moss: str
    moss_hi: str
    edge: str
    ink: str
    assets_dir: Path


def assets_dir() -> Path:
    return _ASSETS


def load_photo(name: str, *, master: tk.Misc | None = None) -> tk.PhotoImage | None:
    path = _ASSETS / name
    if not path.is_file():
        return None
    try:
        # Prefer PIL → PhotoImage for large/resized assets when available
        from PIL import Image, ImageTk

        im = Image.open(path).convert("RGBA")
        return ImageTk.PhotoImage(im, master=master)
    except Exception:
        try:
            return tk.PhotoImage(file=str(path), master=master)
        except tk.TclError:
            return None


def load_photo_resized(
    name: str,
    size: tuple[int, int],
    *,
    master: tk.Misc | None = None,
) -> tk.PhotoImage | None:
    path = _ASSETS / name
    if not path.is_file():
        return None
    try:
        from PIL import Image, ImageTk

        im = Image.open(path).convert("RGBA")
        im = im.resize(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(im, master=master)
    except Exception:
        return load_photo(name, master=master)


def configure_studio_theme(root: tk.Tk) -> StudioTheme:
    """Tema clam custom — não depende de sv-ttk (evita look Windows genérico)."""
    style = ttk_style = __import__("tkinter.ttk", fromlist=["Style"]).Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=INK)
    style.configure(".", background=INK, foreground=TEXT, borderwidth=0, focuscolor=FOCUS)
    style.configure("TFrame", background=INK)
    style.configure("Content.TFrame", background=INK2)
    style.configure("Panel.TFrame", background=PANEL, relief="flat")
    style.configure("Cardlike.TFrame", background=PANEL, relief="flat")
    style.configure("TLabelframe", background=PANEL, foreground=TEXT)
    style.configure(
        "TLabelframe.Label",
        background=PANEL,
        foreground=MOSS_HI,
        font=_ui_font(10, bold=True),
    )
    # Labels vivem sobretudo dentro de painéis (PANEL); chrome usa tk.Label com bg explícito.
    style.configure("TLabel", background=PANEL, foreground=TEXT)
    style.configure(
        "Heading.TLabel",
        background=PANEL,
        foreground=TEXT,
        font=_ui_font(11, bold=True),
    )
    style.configure(
        "Section.TLabel",
        background=PANEL,
        foreground=MOSS_HI,
        font=_ui_font(9, bold=True),
    )
    style.configure(
        "Field.TLabel",
        background=PANEL,
        foreground=MUTED,
        font=_ui_font(8),
    )
    style.configure(
        "Brand.TLabel",
        background=INK2,
        foreground=TEXT,
        font=_ui_font(16, bold=True),
    )
    style.configure(
        "Subheading.TLabel",
        background=PANEL,
        foreground=MUTED,
        font=_ui_font(9),
    )
    style.configure(
        "Muted.TLabel",
        background=INK,
        foreground=MUTED_SOFT,
        font=_ui_font(8),
    )
    style.configure(
        "TButton",
        background=PANEL2,
        foreground=TEXT,
        padding=(14, 9),
        focusthickness=1,
        focuscolor=FOCUS,
        borderwidth=0,
        font=_ui_font(10),
    )
    style.map(
        "TButton",
        background=[("active", EDGE), ("pressed", MOSS_DK), ("disabled", INK2)],
        foreground=[("disabled", MUTED_SOFT)],
    )
    style.configure(
        "Accent.TButton",
        background=MOSS_DK,
        foreground="#FFFFFF",
        padding=(22, 14),
        font=_ui_font(11, bold=True),
        focusthickness=1,
        focuscolor=MOSS_HI,
    )
    style.map(
        "Accent.TButton",
        background=[("active", MOSS), ("pressed", MOSS_DK), ("disabled", EDGE)],
        foreground=[("disabled", MUTED)],
    )
    style.configure(
        "Ghost.TButton",
        background=INK2,
        foreground=MUTED,
        padding=(12, 8),
        font=_ui_font(9),
    )
    style.map(
        "Ghost.TButton",
        background=[("active", PANEL), ("pressed", PANEL2)],
        foreground=[("active", TEXT)],
    )
    style.configure("TCheckbutton", background=PANEL, foreground=TEXT, padding=(10, 6))
    style.map(
        "TCheckbutton",
        background=[("active", PANEL)],
        foreground=[("disabled", MUTED_SOFT)],
    )
    style.configure(
        "TEntry",
        fieldbackground=INK,
        foreground=TEXT,
        insertcolor=TEXT,
        bordercolor=EDGE,
        lightcolor=EDGE,
        darkcolor=INK2,
        padding=(10, 8),
        relief="flat",
    )
    style.map(
        "TEntry",
        fieldbackground=[("focus", INK)],
        bordercolor=[("focus", MOSS)],
        lightcolor=[("focus", MOSS)],
    )
    # Select / Combobox — seta + campo + lista alinhados ao Editing Bay
    style.configure(
        "TCombobox",
        fieldbackground=INK,
        background=PANEL2,
        foreground=TEXT,
        padding=(12, 9),
        insertcolor=TEXT,
        arrowcolor=MOSS_HI,
        bordercolor=EDGE,
        lightcolor=EDGE,
        darkcolor=INK2,
        relief="flat",
        arrowsize=14,
    )
    style.map(
        "TCombobox",
        fieldbackground=[
            ("readonly", INK),
            ("focus", INK),
            ("disabled", INK2),
        ],
        foreground=[
            ("readonly", TEXT),
            ("disabled", MUTED_SOFT),
        ],
        background=[
            ("readonly", PANEL2),
            ("active", EDGE),
            ("pressed", MOSS_DK),
            ("disabled", INK2),
        ],
        bordercolor=[("focus", MOSS), ("readonly", EDGE)],
        lightcolor=[("focus", MOSS)],
        arrowcolor=[("disabled", MUTED_SOFT), ("active", MOSS_HI)],
        selectbackground=[("readonly", MOSS_DK), ("focus", MOSS_DK)],
        selectforeground=[("readonly", "#FFFFFF"), ("focus", "#FFFFFF")],
    )
    style.configure(
        "TSpinbox",
        fieldbackground=INK,
        foreground=TEXT,
        padding=(10, 8),
        insertcolor=TEXT,
        arrowcolor=MOSS_HI,
        bordercolor=EDGE,
        lightcolor=EDGE,
        darkcolor=INK2,
        relief="flat",
    )
    # Dropdown list (popdown)
    root.option_add("*TCombobox*Listbox.background", INK)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", MOSS_DK)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", _ui_font(10))
    root.option_add("*TCombobox*Listbox.relief", "flat")
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    style.configure(
        "Treeview",
        background=INK2,
        foreground=TEXT,
        fieldbackground=INK2,
        rowheight=40,
        font=_ui_font(10),
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        background=PANEL2,
        foreground=MOSS_HI,
        font=_ui_font(10, bold=True),
        relief="flat",
        padding=(12, 8),
    )
    style.map(
        "Treeview",
        background=[("selected", MOSS_DK)],
        foreground=[("selected", "#FFFFFF")],
    )
    style.configure("TPanedwindow", background=INK)
    style.configure("TSeparator", background=EDGE)
    style.configure("Horizontal.TScale", background=PANEL, troughcolor=INK2)
    style.configure(
        "Sidebar.TButton",
        background=INK2,
        foreground=MUTED,
        font=_ui_font(10),
        padding=(18, 12),
        anchor="w",
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Sidebar.TButton",
        background=[("active", PANEL), ("pressed", PANEL)],
        foreground=[("active", TEXT)],
    )
    style.configure(
        "SidebarActive.TButton",
        background=MOSS_DK,
        foreground="#FFFFFF",
        font=_ui_font(10, bold=True),
        padding=(18, 12),
        anchor="w",
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "SidebarActive.TButton",
        background=[("active", MOSS), ("pressed", MOSS_DK)],
    )
    style.configure("Status.TLabel", background=STATUS_BG, foreground=MUTED_SOFT, font=_ui_font(9))
    style.configure("Status.TFrame", background=STATUS_BG)
    style.configure("TNotebook", borderwidth=0, padding=0, background=INK2)
    style.configure(
        "TNotebook.Tab",
        background=INK,
        foreground=MUTED,
        padding=(18, 10),
        font=_ui_font(10),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PANEL), ("active", PANEL2)],
        foreground=[("selected", MOSS_HI), ("active", TEXT)],
    )
    style.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=INK, arrowcolor=MUTED)
    style.configure("Horizontal.TScrollbar", background=PANEL2, troughcolor=INK, arrowcolor=MUTED)

    return StudioTheme(
        text_bg=INK2,
        text_fg=TEXT,
        hint_fg=MUTED,
        is_dark=True,
        card_style="Panel.TFrame",
        checkbutton_style="TCheckbutton",
        secondary_button_style="Ghost.TButton",
        log_fg="#C5D4CB",
        accent_strip=MOSS,
        urls_inset_bg=INK,
        panel=PANEL,
        moss=MOSS,
        moss_hi=MOSS_HI,
        edge=EDGE,
        ink=INK,
        assets_dir=_ASSETS,
    )


def _ui_font(size: int, *, bold: bool = False) -> tuple:
    family = "Ubuntu Sans"
    if sys.platform == "win32":
        family = "Segoe UI"
    elif sys.platform == "darwin":
        family = ".SF NS Text"
    return (family, size, "bold") if bold else (family, size)


def configure_ui_fonts(root: tk.Tk) -> None:
    try:
        ui = tkfont.nametofont("TkDefaultFont")
        fixed = tkfont.nametofont("TkFixedFont")
        if sys.platform == "win32":
            ui.configure(family="Segoe UI", size=10)
            fixed.configure(family="Consolas", size=10)
        elif sys.platform == "darwin":
            ui.configure(family=".SF NS Text", size=11)
            fixed.configure(family="Menlo", size=10)
        else:
            for fam in ("Ubuntu Sans", "Ubuntu", "Noto Sans", "DejaVu Sans"):
                try:
                    ui.configure(family=fam, size=10)
                    break
                except tk.TclError:
                    continue
            for mono in ("Ubuntu Mono", "Noto Sans Mono", "DejaVu Sans Mono"):
                try:
                    fixed.configure(family=mono, size=10)
                    break
                except tk.TclError:
                    continue
        fam = str(ui.cget("family"))
        sz = int(ui.cget("size"))
        if " " in fam:
            root.option_add("*Font", f"{{{fam}}} {sz}")
        else:
            root.option_add("*Font", f"{fam} {sz}")
    except tk.TclError:
        pass
