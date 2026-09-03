"""Sistema visual do Studio Cortes.

O aplicativo continua usando Tkinter/ttk para manter o setup local-first e leve,
mas os componentes compartilham uma linguagem visual única: superfícies escuras,
hierarquia tipográfica clara e acentos elétricos para ações e estados.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import NamedTuple

# Tokens legados continuam com os mesmos nomes porque a GUI importa esses nomes.
# A paleta nova evita o verde-musgo anterior e aproxima o produto de ferramentas
# criativas contemporâneas, sem adicionar uma dependência de tema externo.
INK = "#080A10"
INK2 = "#0E111A"
PANEL = "#151A26"
PANEL2 = "#1D2433"
EDGE = "#303B57"
MOSS = "#8B5CF6"
MOSS_HI = "#C4B5FD"
MOSS_DK = "#6D3FD5"
TEXT = "#F4F6FB"
MUTED = "#A3ADC2"
MUTED_SOFT = "#6B768D"
STATUS_BG = "#090B12"
DANGER = "#FB7185"
FOCUS = "#22D3EE"
AQUA = "#22D3EE"
SUCCESS = "#34D399"
WARNING = "#FBBF24"

_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "gui"
_UI_FAMILY = "DejaVu Sans"


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
    """Carrega um asset opcional mantendo compatibilidade com a GUI antiga."""
    path = _ASSETS / name
    if not path.is_file():
        return None
    try:
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
    """Configura ttk com uma aparência escura, limpa e sem dependências externas."""
    style = __import__("tkinter.ttk", fromlist=["Style"]).Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=INK)
    style.configure(
        ".",
        background=INK,
        foreground=TEXT,
        borderwidth=0,
        focuscolor=FOCUS,
        font=_ui_font(10),
    )
    # Frames internos de formulários vivem em cards; chrome e workspace usam
    # Content.TFrame/Chrome.TFrame explicitamente.
    style.configure("TFrame", background=PANEL)
    style.configure("Content.TFrame", background=INK)
    style.configure("Panel.TFrame", background=PANEL, relief="flat")
    style.configure("Cardlike.TFrame", background=PANEL, relief="flat")
    style.configure("Chrome.TFrame", background=INK2)
    style.configure("Hero.TFrame", background=PANEL, relief="flat")

    style.configure("TLabelframe", background=PANEL, foreground=TEXT)
    style.configure(
        "TLabelframe.Label",
        background=PANEL,
        foreground=MOSS_HI,
        font=_ui_font(10, bold=True),
    )
    style.configure("TLabel", background=PANEL, foreground=TEXT)
    style.configure(
        "Heading.TLabel",
        background=PANEL,
        foreground=TEXT,
        font=_ui_font(12, bold=True),
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
        font=_ui_font(9),
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
        background=PANEL,
        foreground=MUTED_SOFT,
        font=_ui_font(9),
    )
    style.configure(
        "Eyebrow.TLabel",
        background=INK,
        foreground=AQUA,
        font=_ui_font(8, bold=True),
    )
    style.configure(
        "PageTitle.TLabel",
        background=INK,
        foreground=TEXT,
        font=_ui_font(26, bold=True),
    )
    style.configure(
        "PageSubtitle.TLabel",
        background=INK,
        foreground=MUTED,
        font=_ui_font(10),
    )
    style.configure(
        "Chip.TLabel",
        background=PANEL2,
        foreground=MUTED,
        font=_ui_font(8, bold=True),
        padding=(10, 6),
    )
    style.configure(
        "ChipAccent.TLabel",
        background="#211B3B",
        foreground=MOSS_HI,
        font=_ui_font(8, bold=True),
        padding=(10, 6),
    )

    style.configure(
        "TButton",
        background=PANEL2,
        foreground=TEXT,
        padding=(16, 11),
        focusthickness=1,
        focuscolor=FOCUS,
        borderwidth=1,
        bordercolor=EDGE,
        lightcolor=EDGE,
        darkcolor=EDGE,
        relief="flat",
        font=_ui_font(10),
    )
    style.map(
        "TButton",
        background=[("active", EDGE), ("pressed", MOSS_DK), ("disabled", INK2)],
        foreground=[("disabled", MUTED_SOFT)],
        bordercolor=[("focus", FOCUS), ("active", MOSS)],
    )
    style.configure(
        "Accent.TButton",
        background=MOSS_DK,
        foreground="#FFFFFF",
        padding=(26, 14),
        font=_ui_font(12, bold=True),
        focusthickness=1,
        focuscolor=AQUA,
        borderwidth=1,
        bordercolor=MOSS,
        lightcolor=MOSS,
        darkcolor=MOSS_DK,
    )
    style.map(
        "Accent.TButton",
        background=[("active", MOSS), ("pressed", MOSS_DK), ("disabled", EDGE)],
        foreground=[("disabled", MUTED)],
        bordercolor=[("active", MOSS_HI), ("focus", AQUA)],
    )
    style.configure(
        "Header.TButton",
        background=INK2,
        foreground=MUTED,
        padding=(10, 7),
        font=_ui_font(9, bold=True),
        borderwidth=0,
    )
    style.map(
        "Header.TButton",
        background=[("active", PANEL2), ("pressed", PANEL)],
        foreground=[("active", TEXT)],
    )
    style.configure(
        "Ghost.TButton",
        background=INK2,
        foreground=MUTED,
        padding=(12, 9),
        font=_ui_font(9),
        borderwidth=1,
        bordercolor=EDGE,
        lightcolor=EDGE,
        darkcolor=EDGE,
    )
    style.map(
        "Ghost.TButton",
        background=[("active", PANEL), ("pressed", PANEL2)],
        foreground=[("active", TEXT)],
        bordercolor=[("active", MOSS)],
    )
    style.configure(
        "AltAccent.TButton",
        background="#12303A",
        foreground="#A5F3FC",
        padding=(14, 10),
        font=_ui_font(10, bold=True),
        borderwidth=1,
        bordercolor="#1D6576",
        lightcolor="#1D6576",
        darkcolor="#12303A",
    )
    style.map(
        "AltAccent.TButton",
        background=[("active", "#174B58"), ("pressed", "#0E242B")],
        foreground=[("active", "#CFFAFE")],
        bordercolor=[("active", AQUA)],
    )
    style.configure(
        "Danger.TButton",
        background="#321A27",
        foreground="#FDA4AF",
        padding=(12, 9),
        font=_ui_font(9, bold=True),
        borderwidth=1,
        bordercolor="#693044",
        lightcolor="#693044",
        darkcolor="#321A27",
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#4B2030"), ("pressed", "#28141E")],
        foreground=[("active", "#FFE4E6")],
    )

    style.configure("TCheckbutton", background=PANEL, foreground=TEXT, padding=(8, 7))
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
        padding=(12, 10),
        relief="flat",
    )
    style.map(
        "TEntry",
        fieldbackground=[("focus", INK)],
        bordercolor=[("focus", AQUA)],
        lightcolor=[("focus", AQUA)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=INK,
        background=PANEL2,
        foreground=TEXT,
        padding=(12, 10),
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
        fieldbackground=[("readonly", INK), ("focus", INK), ("disabled", INK2)],
        foreground=[("readonly", TEXT), ("disabled", MUTED_SOFT)],
        background=[
            ("readonly", PANEL2),
            ("active", EDGE),
            ("pressed", MOSS_DK),
            ("disabled", INK2),
        ],
        bordercolor=[("focus", AQUA), ("readonly", EDGE)],
        lightcolor=[("focus", AQUA)],
        arrowcolor=[("disabled", MUTED_SOFT), ("active", MOSS_HI)],
        selectbackground=[("readonly", MOSS_DK), ("focus", MOSS_DK)],
        selectforeground=[("readonly", "#FFFFFF"), ("focus", "#FFFFFF")],
    )
    style.configure(
        "TSpinbox",
        fieldbackground=INK,
        foreground=TEXT,
        padding=(10, 9),
        insertcolor=TEXT,
        arrowcolor=MOSS_HI,
        bordercolor=EDGE,
        lightcolor=EDGE,
        darkcolor=INK2,
        relief="flat",
    )

    # Scrollbars tk nativas (ScrolledText) no mesmo tom escuro.
    root.option_add("*Scrollbar.background", PANEL2)
    root.option_add("*Scrollbar.troughcolor", INK)
    root.option_add("*Scrollbar.activeBackground", MOSS)
    root.option_add("*Scrollbar.borderWidth", 0)
    root.option_add("*Scrollbar.relief", "flat")
    root.option_add("*Scrollbar.width", 10)
    root.option_add("*Scrollbar.arrowColor", PANEL2)

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
        background=PANEL,
        foreground=TEXT,
        fieldbackground=PANEL,
        rowheight=46,
        font=_ui_font(10),
        borderwidth=0,
        relief="flat",
    )
    style.configure(
        "Treeview.Heading",
        background=PANEL2,
        foreground=MUTED,
        font=_ui_font(9, bold=True),
        relief="flat",
        padding=(12, 9),
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
        padding=(16, 12),
        anchor="w",
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Sidebar.TButton",
        background=[("active", PANEL2), ("pressed", PANEL)],
        foreground=[("active", TEXT)],
    )
    style.configure(
        "SidebarActive.TButton",
        background="#2A2054",
        foreground="#F5F3FF",
        font=_ui_font(10, bold=True),
        padding=(16, 12),
        anchor="w",
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "SidebarActive.TButton",
        background=[("active", "#302254"), ("pressed", MOSS_DK)],
        foreground=[("active", "#FFFFFF")],
    )

    style.configure("Status.TLabel", background=STATUS_BG, foreground=MUTED_SOFT, font=_ui_font(9))
    style.configure("Status.TFrame", background=STATUS_BG)

    # A navegação lateral é a única forma de trocar de workspace. O Notebook
    # continua existindo como contrato interno, mas sua chrome não aparece.
    style.configure("Hidden.TNotebook", background=INK, borderwidth=0, padding=0)
    try:
        style.layout("Hidden.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
        style.layout("Hidden.TNotebook.Tab", [])
    except tk.TclError:
        # Em temas Tk muito antigos, manter o layout padrão ainda é funcional.
        pass

    # Scrollbar slim estilo web: só o thumb arredondado, sem setas.
    for name, trough, thumb in (("Vertical", "ns", "nswe"), ("Horizontal", "we", "nswe")):
        try:
            style.layout(
                f"{name}.TScrollbar",
                [
                    (
                        f"{name}.Scrollbar.trough",
                        {
                            "sticky": trough,
                            "children": [(f"{name}.Scrollbar.thumb", {"sticky": thumb})],
                        },
                    )
                ],
            )
        except tk.TclError:
            pass
        style.configure(
            f"{name}.TScrollbar",
            background=EDGE,
            troughcolor=INK,
            borderwidth=0,
            relief="flat",
            arrowsize=0,
        )
        style.map(
            f"{name}.TScrollbar",
            background=[("active", MOSS), ("pressed", MOSS_HI)],
        )

    return StudioTheme(
        text_bg=INK2,
        text_fg=TEXT,
        hint_fg=MUTED,
        is_dark=True,
        card_style="Panel.TFrame",
        checkbutton_style="TCheckbutton",
        secondary_button_style="Ghost.TButton",
        log_fg="#D3D8E7",
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
    family = _UI_FAMILY
    return (family, size, "bold") if bold else (family, size)


def configure_ui_fonts(root: tk.Tk) -> None:
    """Escolhe uma fonte moderna disponível sem exigir instalação adicional."""
    global _UI_FAMILY
    try:
        available = set(tkfont.families(root))
        candidates = (
            "Inter",
            "Manrope",
            "Plus Jakarta Sans",
            "Aptos",
            "Ubuntu Sans",
            "Noto Sans",
            "DejaVu Sans",
        )
        _UI_FAMILY = next((family for family in candidates if family in available), _UI_FAMILY)

        ui = tkfont.nametofont("TkDefaultFont")
        fixed = tkfont.nametofont("TkFixedFont")
        ui.configure(family=_UI_FAMILY, size=10)
        mono_candidates = ("JetBrains Mono", "Ubuntu Mono", "Noto Sans Mono", "DejaVu Sans Mono")
        mono = next((family for family in mono_candidates if family in available), "DejaVu Sans Mono")
        fixed.configure(family=mono, size=10)
        if " " in _UI_FAMILY:
            root.option_add("*Font", f"{{{_UI_FAMILY}}} 10")
        else:
            root.option_add("*Font", f"{_UI_FAMILY} 10")
    except tk.TclError:
        pass
