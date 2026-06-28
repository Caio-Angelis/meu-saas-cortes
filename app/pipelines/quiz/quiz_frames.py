"""
Geração de frames estáticos 9:16 para a Máquina de Quizzes (projeto.md §13.3.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from app.core.config import OUTPUT_VIDEO_HEIGHT, OUTPUT_VIDEO_WIDTH

if TYPE_CHECKING:
    from app.pipelines.quiz.quiz_pipeline import QuizQuestion

_log = logging.getLogger("quiz_frames")

CANVAS_W = OUTPUT_VIDEO_WIDTH
CANVAS_H = OUTPUT_VIDEO_HEIGHT
MARGIN_LR = 150
MARGIN_TOP = 300
MARGIN_BOTTOM = 300

DEFAULT_QUIZ_BG_COLOR = "#1A1A1A"
COLOR_BG = DEFAULT_QUIZ_BG_COLOR  # legado / testes


def normalize_quiz_bg_color(value: str) -> str:
    """Normaliza hex #RRGGBB; inválido → padrão escuro do quiz."""
    v = (value or "").strip() or DEFAULT_QUIZ_BG_COLOR
    if not v.startswith("#"):
        v = "#" + v
    if len(v) == 7:
        try:
            int(v[1:], 16)
            return v.upper()
        except ValueError:
            pass
    return DEFAULT_QUIZ_BG_COLOR
COLOR_TEXT = "#F5F5F5"
COLOR_OPTION_BG = "#2E2E3A"
COLOR_OPTION_BORDER = "#5A5A6E"
COLOR_OPTION_WRONG = "#3D2830"
COLOR_OPTION_WRONG_BORDER = "#8B4545"
COLOR_CORRECT = "#4CAF50"
COLOR_CORRECT_BORDER = "#81C784"
COLOR_CURIOSIDADE = "#B8C5D6"
COLOR_GANCHO = "#FFD54F"
COLOR_HOOK_SUB = "#9E9E9E"
COLOR_HOOK_BADGE = "#00E5FF"
COLOR_NUMERO = "#B0BEC5"

OPTION_LABELS = ("A", "B", "C", "D")
OPTION_GAP = 18
OPTION_RADIUS = 22
OPTION_PAD_X = 28
OPTION_PAD_Y = 20

# Coloque uma fonte customizada aqui, ex.: Path("assets/fonts/Inter-Bold.ttf")
CUSTOM_FONT_PATH: Path | None = None

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)


@dataclass
class _QuizFonts:
    pergunta: ImageFont.FreeTypeFont | ImageFont.ImageFont
    opcao: ImageFont.FreeTypeFont | ImageFont.ImageFont
    curiosidade: ImageFont.FreeTypeFont | ImageFont.ImageFont
    numero: ImageFont.FreeTypeFont | ImageFont.ImageFont
    gancho: ImageFont.FreeTypeFont | ImageFont.ImageFont
    hook_sub: ImageFont.FreeTypeFont | ImageFont.ImageFont
    hook_badge: ImageFont.FreeTypeFont | ImageFont.ImageFont
    reward: ImageFont.FreeTypeFont | ImageFont.ImageFont


def _resolve_font_path() -> Path | None:
    if CUSTOM_FONT_PATH is not None and CUSTOM_FONT_PATH.is_file():
        return CUSTOM_FONT_PATH
    for candidate in _FONT_CANDIDATES:
        p = Path(candidate)
        if p.is_file():
            return p
    return None


def _load_quiz_fonts() -> _QuizFonts:
    path = _resolve_font_path()
    if path is not None:
        try:
            return _QuizFonts(
                pergunta=ImageFont.truetype(str(path), 52),
                opcao=ImageFont.truetype(str(path), 36),
                curiosidade=ImageFont.truetype(str(path), 32),
                numero=ImageFont.truetype(str(path), 96),
                gancho=ImageFont.truetype(str(path), 64),
                hook_sub=ImageFont.truetype(str(path), 34),
                hook_badge=ImageFont.truetype(str(path), 40),
                reward=ImageFont.truetype(str(path), 64),
            )
        except OSError as e:
            _log.warning("Falha ao carregar fonte TTF %s: %s — fallback default", path, e)
    default = ImageFont.load_default()
    return _QuizFonts(
        pergunta=default,
        opcao=default,
        curiosidade=default,
        numero=default,
        gancho=default,
        hook_sub=default,
        hook_badge=default,
        reward=default,
    )


def _content_box() -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) da área útil dentro das margens TikTok."""
    return (MARGIN_LR, MARGIN_TOP, CANVAS_W - MARGIN_LR, CANVAS_H - MARGIN_BOTTOM)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = (text or "").strip().split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word]) if current else word
        if _text_width(draw, trial, font) <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_centered_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    font: ImageFont.ImageFont,
    color: str,
    center_x: int,
    top_y: int,
    line_spacing: int = 10,
) -> int:
    """Desenha linhas centralizadas; retorna y após o último linha."""
    y = top_y
    for line in lines:
        w = _text_width(draw, line, font)
        x = center_x - w // 2
        draw.text((x, y), line, font=font, fill=color)
        y += _text_height(draw, line, font) + line_spacing
    return y


def _option_layout(y_start: int, y_end: int) -> list[tuple[int, int, int, int]]:
    """Quatro retângulos (x0, y0, x1, y1) empilhados no centro."""
    x0, _, x1, _ = _content_box()
    total_h = y_end - y_start
    n = len(OPTION_LABELS)
    gap_total = OPTION_GAP * (n - 1)
    box_h = max(72, (total_h - gap_total) // n)
    boxes: list[tuple[int, int, int, int]] = []
    y = y_start
    for _ in OPTION_LABELS:
        boxes.append((x0, y, x1, y + box_h))
        y += box_h + OPTION_GAP
    return boxes


def _draw_option_box(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    label: str,
    option_text: str,
    *,
    font: ImageFont.ImageFont,
    fill: str,
    outline: str,
    text_color: str = COLOR_TEXT,
) -> None:
    draw.rounded_rectangle(rect, radius=OPTION_RADIUS, fill=fill, outline=outline, width=3)
    x0, y0, x1, y1 = rect
    inner_w = x1 - x0 - 2 * OPTION_PAD_X
    display = f"{label}. {option_text}"
    lines = _wrap_lines(draw, display, font, inner_w)
    block_h = sum(_text_height(draw, ln, font) + 6 for ln in lines) - 6
    y = y0 + (y1 - y0 - block_h) // 2
    cx = (x0 + x1) // 2
    for line in lines:
        w = _text_width(draw, line, font)
        draw.text((cx - w // 2, y), line, font=font, fill=text_color)
        y += _text_height(draw, line, font) + 6


def _draw_question_number(
    draw: ImageDraw.ImageDraw,
    question_index: int,
    *,
    font: ImageFont.ImageFont,
) -> None:
    """Número grande (#3) no canto superior direito da safe zone."""
    label = f"#{question_index}"
    x0, y0, x1, _ = _content_box()
    w = _text_width(draw, label, font)
    h = _text_height(draw, label, font)
    x = x1 - w - 8
    y = y0 - h - 24
    if y < MARGIN_TOP - 80:
        y = MARGIN_TOP - 80
    draw.text((x, y), label, font=font, fill=COLOR_NUMERO)


def _render_frame(
    question: QuizQuestion,
    *,
    show_answer: bool,
    fonts: _QuizFonts,
    question_index: int = 1,
    bg_color: str = DEFAULT_QUIZ_BG_COLOR,
) -> Image.Image:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), normalize_quiz_bg_color(bg_color))
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = _content_box()
    cx = CANVAS_W // 2
    content_w = x1 - x0

    _draw_question_number(draw, question_index, font=fonts.numero)

    question_lines = _wrap_lines(draw, question["pergunta"], fonts.pergunta, content_w)
    q_bottom = _draw_centered_block(
        draw,
        question_lines,
        font=fonts.pergunta,
        color=COLOR_TEXT,
        center_x=cx,
        top_y=y0,
        line_spacing=12,
    )

    options_top = max(q_bottom + 48, y0 + int((y1 - y0) * 0.32))
    footer_reserved = 260 if show_answer else 120
    options_bottom = y1 - footer_reserved
    boxes = _option_layout(options_top, options_bottom)
    correct_idx = question["resposta_correta"]

    for i, (rect, label) in enumerate(zip(boxes, OPTION_LABELS, strict=True)):
        opt_text = question["opcoes"][i]
        if not show_answer:
            fill, outline = COLOR_OPTION_BG, COLOR_OPTION_BORDER
        elif i == correct_idx:
            fill, outline = COLOR_CORRECT, COLOR_CORRECT_BORDER
        else:
            # Sem borda contrastante (evita halo azulado/ciano no encode)
            fill, outline = COLOR_OPTION_WRONG, COLOR_OPTION_WRONG
        _draw_option_box(
            draw,
            rect,
            label,
            opt_text,
            font=fonts.opcao,
            fill=fill,
            outline=outline,
        )

    if show_answer:
        footer_top = y1 - 220
        cur_lines = _wrap_lines(
            draw, question["curiosidade_extra"], fonts.curiosidade, content_w
        )
        _draw_centered_block(
            draw,
            cur_lines,
            font=fonts.curiosidade,
            color=COLOR_CURIOSIDADE,
            center_x=cx,
            top_y=footer_top,
            line_spacing=8,
        )

    return img


def render_quiz_frame_pair(
    question: QuizQuestion,
    question_index: int,
    work_dir: Path,
    *,
    total_questions: int | None = None,
    bg_color: str = DEFAULT_QUIZ_BG_COLOR,
) -> tuple[Path, Path]:
    """
    Gera `frame_1_pergunta_{idx}.png` e `frame_2_resposta_{idx}.png` em work_dir.

    Retorna (frame_pergunta, frame_resposta).
    """
    del total_questions  # reservado para overlays FFmpeg (progresso no topo)
    work_dir.mkdir(parents=True, exist_ok=True)
    fonts = _load_quiz_fonts()
    idx = question_index

    f1 = work_dir / f"frame_1_pergunta_{idx}.png"
    f2 = work_dir / f"frame_2_resposta_{idx}.png"

    _render_frame(
        question, show_answer=False, fonts=fonts, question_index=idx, bg_color=bg_color
    ).save(f1, format="PNG", optimize=True)
    _render_frame(
        question, show_answer=True, fonts=fonts, question_index=idx, bg_color=bg_color
    ).save(f2, format="PNG", optimize=True)

    return f1, f2


def render_quiz_hook_frame(
    work_dir: Path,
    *,
    gancho: str,
    subtitulo: str,
    bg_color: str = DEFAULT_QUIZ_BG_COLOR,
) -> Path:
    """
    Frame de abertura (2–3 s): cartão de gancho — NÃO parece pergunta do quiz.

    Só frase de impacto + linha de contexto (tema / quantidade). A P1 começa no segmento seguinte.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "frame_hook.png"
    fonts = _load_quiz_fonts()
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), normalize_quiz_bg_color(bg_color))
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = _content_box()
    cx = CANVAS_W // 2
    content_w = x1 - x0

    badge = "QUIZ"
    bw = _text_width(draw, badge, fonts.hook_badge)
    draw.text((cx - bw // 2, y0), badge, font=fonts.hook_badge, fill=COLOR_HOOK_BADGE)

    gancho_lines = _wrap_lines(draw, gancho.strip(), fonts.gancho, content_w)
    block_h = sum(_text_height(draw, ln, fonts.gancho) + 16 for ln in gancho_lines) - 16
    gancho_top = (CANVAS_H - block_h) // 2 - 40
    g_bottom = _draw_centered_block(
        draw,
        gancho_lines,
        font=fonts.gancho,
        color=COLOR_GANCHO,
        center_x=cx,
        top_y=max(y0 + 80, gancho_top),
        line_spacing=16,
    )

    sub_lines = _wrap_lines(draw, subtitulo.strip(), fonts.hook_sub, content_w)
    _draw_centered_block(
        draw,
        sub_lines,
        font=fonts.hook_sub,
        color=COLOR_HOOK_SUB,
        center_x=cx,
        top_y=min(g_bottom + 48, y1 - 120),
        line_spacing=10,
    )

    img.save(out, format="PNG", optimize=True)
    return out


def render_quiz_reward_frame(
    work_dir: Path,
    *,
    message: str,
    index: int,
    bg_color: str = DEFAULT_QUIZ_BG_COLOR,
) -> Path:
    """Frame 1 s entre perguntas (micro-recompensa: Acertou? / Errou?)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / f"frame_reward_{index}.png"
    fonts = _load_quiz_fonts()
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), normalize_quiz_bg_color(bg_color))
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = _content_box()
    cx = CANVAS_W // 2
    content_w = x1 - x0
    lines = _wrap_lines(draw, message.strip(), fonts.reward, content_w)
    block_h = sum(_text_height(draw, ln, fonts.reward) + 14 for ln in lines) - 14
    top_y = (CANVAS_H - block_h) // 2
    _draw_centered_block(
        draw,
        lines,
        font=fonts.reward,
        color=COLOR_GANCHO,
        center_x=cx,
        top_y=top_y,
        line_spacing=14,
    )
    img.save(out, format="PNG", optimize=True)
    return out


def render_quiz_outro_frame(
    work_dir: Path,
    *,
    message: str,
    bg_color: str = DEFAULT_QUIZ_BG_COLOR,
) -> Path:
    """
    Frame 9:16 de encerramento (CTA após todas as perguntas).

    Retorna caminho de `frame_outro.png`.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "frame_outro.png"
    fonts = _load_quiz_fonts()
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), normalize_quiz_bg_color(bg_color))
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = _content_box()
    cx = CANVAS_W // 2
    content_w = x1 - x0
    lines = _wrap_lines(draw, message, fonts.pergunta, content_w)
    block_h = sum(_text_height(draw, ln, fonts.pergunta) + 12 for ln in lines) - 12
    top_y = (CANVAS_H - block_h) // 2
    _draw_centered_block(
        draw,
        lines,
        font=fonts.pergunta,
        color=COLOR_TEXT,
        center_x=cx,
        top_y=top_y,
        line_spacing=14,
    )
    img.save(out, format="PNG", optimize=True)
    return out
