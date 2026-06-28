"""
Motor de física 2D (Pymunk) e renderização PIL para Batalha 1v1 — 1080×1920.

Fase 2: simulação base, texturas nas bolinhas, modos tamanho / território / plinko.
"""

from __future__ import annotations

import json
import logging
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pymunk
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.pipelines.batalha.batalha_images import hex_to_rgb
from app.pipelines.batalha.batalha_pipeline import (
    BATALHA_MOD_PLINKO,
    BATALHA_MOD_TAMANHO,
    BATALHA_MOD_TERRITORIO,
    BATALHA_VIDEO_HEIGHT,
    BATALHA_VIDEO_WIDTH,
    BatalhaAssets,
    BatalhaSpec,
    normalize_batalha_modo,
)
from app.core.cancel import raise_if_cancelled

_log = logging.getLogger("batalha_frames")

# --- Física / tempo ---
FPS = 30
DT = 1.0 / FPS
MAX_SIM_DURATION_SEC = 42.0
COLLISION_DEBOUNCE_SEC = 0.06

# --- Arena ---
MARGIN = 48
WALL_THICKNESS = 8

# --- Bolinhas ---
INITIAL_RADIUS = 58.0
MIN_RADIUS = 14.0
BALL_COLLISION_TYPE = 1
WALL_COLLISION_TYPE = 2
PIN_COLLISION_TYPE = 3

# --- Agar (tamanho) ---
AGAR_LOSER_SHRINK = 0.12
AGAR_WINNER_GROW = 0.06
AGAR_MIN_MASS_RATIO = 0.02  # perdedor ~0% do raio inicial

# --- Território ---
TERRITORY_GRID_COLS = 54
TERRITORY_GRID_ROWS = 96
TERRITORY_PAINT_RADIUS_PX = 72.0
TERRITORY_WIN_RATIO = 0.88

# --- Plinko ---
# A bola precisa passar entre pinos: folga = espaçamento_centros - 2*raio_pino >= diâmetro_bola + margem
PLINKO_BALL_RADIUS = 28.0
PLINKO_PIN_RADIUS = 7.0
PLINKO_GAP_MARGIN = 16.0  # folga mínima entre bola e pinos (px)
PLINKO_PLAYFIELD_INSET = 36.0  # recuo extra dentro de MARGIN (pinos + cestos)
PLINKO_BASKET_SCORES = (10, 50, 100, 50, 10)
PLINKO_BASKET_ZONE_HEIGHT = 130.0
PLINKO_BASKET_ZONE_TOP = BATALHA_VIDEO_HEIGHT - MARGIN - PLINKO_BASKET_ZONE_HEIGHT
PLINKO_BASKET_FLOOR_Y = float(BATALHA_VIDEO_HEIGHT - MARGIN)
PLINKO_FINISH_Y = PLINKO_BASKET_ZONE_TOP  # última fileira de pinos fica acima disto
PLINKO_BALLS_PER_TEAM = 5
PLINKO_SPAWN_INTERVAL_SEC = 2.0
PLINKO_SPAWN_INTERVAL_FRAMES = int(FPS * PLINKO_SPAWN_INTERVAL_SEC)
PLINKO_GRAVITY = (0.0, 520.0)
PLINKO_DAMPING = 0.98
PLINKO_ELASTICITY = 0.85
PLINKO_ROW_SPACING_FACTOR = 0.72  # < sqrt(3)/2 — fileiras mais densas que o hexagonal padrão
PLINKO_PIN_BOTTOM_MARGIN = 24.0  # última fileira acima dos cestos
PLINKO_STUCK_SPEED = 28.0
PLINKO_STUCK_SEC = 1.25
PLINKO_COLLISION_MIN_IMPULSE = 8.0
PLINKO_COLLISION_DEBOUNCE_SEC = 0.028
PLINKO_BASKET_DIVIDER_RADIUS = 5.0  # espessura visual das divisórias (~10px)
PLINKO_BASKET_DIVIDER_PHYSICS_RADIUS = 2.0  # colisão fina — evita “teto” nas cápsulas no topo
PLINKO_BASKET_DIVIDER_FRICTION = 0.01
PLINKO_BASKET_DIVIDER_ELASTICITY = 0.35
PLINKO_BASKET_MIN_GAP_FACTOR = 1.75  # vão livre >= fator × diâmetro da bolinha (testes/layout)
PLINKO_BASKET_REST_SPEED = 14.0
PLINKO_POST_SETTLE_SEC = 0.45  # breve pausa com placar final antes da tela de vitória
PLINKO_VICTORY_SCREEN_MIN_SEC = 4.0
PLINKO_VICTORY_SCREEN_PAD_SEC = 0.45
PLINKO_END_ITER_HOLD_SEC = 0.35
PLINKO_HUD_NAME_FONT_SIZE = 28
PLINKO_HUD_SCORE_FONT_SIZE = 38
PLINKO_BASKET_LABEL_FONT_SIZE = 52
PLINKO_VICTORY_HEADLINE_FONT_SIZE = 44
PLINKO_VICTORY_NAME_FONT_SIZE = 68
PLINKO_VICTORY_LOGO_MAX_W = 640
PLINKO_VICTORY_LOGO_MAX_H = 400
PLINKO_VICTORY_TEXT_Y_RATIO = 0.30
PLINKO_VICTORY_LOGO_Y_RATIO = 0.50

_BATALHA_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


@dataclass
class OpponentBall:
    player_id: int
    name: str
    color_rgb: tuple[int, int, int]
    body: pymunk.Body
    shape: pymunk.Circle
    texture: Image.Image
    radius: float
    eliminated: bool = False


@dataclass
class BatalhaSimulationResult:
    collision_times_sec: list[float]
    winner_id: int | None
    duration_sec: float
    modo: str
    frame_count: int
    frames: list[bytes] = field(default_factory=list)
    frame_size: tuple[int, int] = (BATALHA_VIDEO_WIDTH, BATALHA_VIDEO_HEIGHT)


@dataclass
class _CollisionRecorder:
    times_sec: list[float] = field(default_factory=list)
    _last_time: float = -1.0

    def register(
        self,
        sim_time: float,
        *,
        min_impulse: float = 80.0,
        impulse: float = 0.0,
        debounce_sec: float | None = None,
    ) -> None:
        if impulse < min_impulse and min_impulse > 0:
            return
        gap = COLLISION_DEBOUNCE_SEC if debounce_sec is None else debounce_sec
        if sim_time - self._last_time < gap:
            return
        self._last_time = sim_time
        self.times_sec.append(round(sim_time, 4))


def plinko_min_pin_spacing(
    usable_width: float,
    *,
    ball_radius: float = PLINKO_BALL_RADIUS,
    pin_radius: float = PLINKO_PIN_RADIUS,
    gap_margin: float = PLINKO_GAP_MARGIN,
) -> float:
    """Espaçamento mínimo entre centros de pinos na mesma fileira."""
    return 2.0 * pin_radius + 2.0 * ball_radius + gap_margin


def plinko_playfield_margin(*, margin: float = MARGIN, inset: float = PLINKO_PLAYFIELD_INSET) -> float:
    return margin + inset


def plinko_basket_x_bounds(
    width_px: int = BATALHA_VIDEO_WIDTH,
    scores: tuple[int, ...] = PLINKO_BASKET_SCORES,
    *,
    margin: float = MARGIN,
    inset: float = PLINKO_PLAYFIELD_INSET,
) -> list[tuple[float, float]]:
    """Limites X (x_min, x_max) de cada cesto, da esquerda para a direita."""
    m = plinko_playfield_margin(margin=margin, inset=inset)
    usable_w = width_px - 2 * m
    n = len(scores)
    if n == 0:
        return []
    basket_w = usable_w / n
    return [(m + i * basket_w, m + (i + 1) * basket_w) for i in range(n)]


def plinko_basket_inner_gap_px(
    width_px: int = BATALHA_VIDEO_WIDTH,
    scores: tuple[int, ...] = PLINKO_BASKET_SCORES,
    *,
    divider_radius: float = PLINKO_BASKET_DIVIDER_RADIUS,
    margin: float = MARGIN,
    inset: float = PLINKO_PLAYFIELD_INSET,
) -> float:
    """Largura útil entre duas divisórias adjacentes (descontando os raios das cápsulas)."""
    bounds = plinko_basket_x_bounds(width_px, scores, margin=margin, inset=inset)
    if not bounds:
        return 0.0
    basket_w = bounds[0][1] - bounds[0][0]
    return max(0.0, basket_w - 2.0 * divider_radius)


def plinko_basket_divider_radius_for_gap(
    *,
    ball_radius: float = PLINKO_BALL_RADIUS,
    min_gap_factor: float = PLINKO_BASKET_MIN_GAP_FACTOR,
    preferred_radius: float = PLINKO_BASKET_DIVIDER_RADIUS,
    width_px: int = BATALHA_VIDEO_WIDTH,
    scores: tuple[int, ...] = PLINKO_BASKET_SCORES,
) -> float:
    """Raio da divisória que garante vão confortável (>= ~1.75× diâmetro da bola)."""
    bounds = plinko_basket_x_bounds(width_px, scores)
    if not bounds:
        return preferred_radius
    basket_w = bounds[0][1] - bounds[0][0]
    min_gap = 2.0 * ball_radius * min_gap_factor
    max_radius = max(2.0, (basket_w - min_gap) / 2.0)
    return min(preferred_radius, max_radius)


def plinko_basket_index_for_x(
    x: float,
    bounds: list[tuple[float, float]],
) -> int:
    """Índice do cesto (0 = esquerda) para a coordenada X do centro da bolinha."""
    if not bounds:
        return 0
    for i, (x0, x1) in enumerate(bounds):
        if i == len(bounds) - 1:
            if x0 <= x <= x1:
                return i
        elif x0 <= x < x1:
            return i
    if x < bounds[0][0]:
        return 0
    return len(bounds) - 1


def plinko_ball_entered_basket_zone(y: float, *, radius: float = 0.0) -> bool:
    """True quando a bolinha entrou na cesta (base cruzou o topo da zona)."""
    return y + radius >= PLINKO_BASKET_ZONE_TOP


@lru_cache(maxsize=24)
def _batalha_font(size: int) -> ImageFont.ImageFont:
    for candidate in _BATALHA_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), max(12, int(size)))
            except OSError:
                continue
    return ImageFont.load_default()


def plinko_victory_narration_text(winner_name: str) -> str:
    """Texto TTS e visual do encerramento do Plinko."""
    name = re.sub(r"\s+", " ", (winner_name or "vencedor").strip())
    return f"Vitória do {name}"


def _paste_image_contain(
    canvas: Image.Image,
    img: Image.Image,
    *,
    cx: float,
    top_y: float,
    max_w: float,
    max_h: float,
) -> tuple[int, int, int, int] | None:
    """Cola imagem centralizada em caixa (contain). Retorna bbox ou None."""
    src = img.convert("RGBA")
    w, h = src.size
    if w < 1 or h < 1:
        return None
    scale = min(max_w / w, max_h / h, 1.0)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    if nw != w or nh != h:
        src = src.resize((nw, nh), Image.Resampling.LANCZOS)
    lx = int(cx - nw / 2)
    ly = int(top_y)
    canvas.alpha_composite(src, (lx, ly))
    return (lx, ly, lx + nw, ly + nh)


def ensure_plinko_victory_screen(sim: BatalhaSimulationBase) -> None:
    """Garante estado da tela final do Plinko (ex.: timeout antes do encerramento normal)."""
    if sim.modo != BATALHA_MOD_PLINKO or sim._plinko_show_victory_screen:  # type: ignore[attr-defined]
        return
    wid = getattr(sim, "_plinko_pending_winner_id", None)
    if wid not in (0, 1):
        scores = getattr(sim, "_team_scores", None)
        wid = plinko_winner_from_scores(scores) if scores is not None else (sim.winner_id or 0)
    sim._plinko_pending_winner_id = wid  # type: ignore[attr-defined]
    sim._plinko_show_victory_screen = True  # type: ignore[attr-defined]
    if getattr(sim, "plinko_victory_screen_start_sec", None) is None:
        sim.plinko_victory_screen_start_sec = sim.sim_time  # type: ignore[attr-defined]


def plinko_victory_screen_duration_sec(narration_duration_sec: float) -> float:
    return max(
        PLINKO_VICTORY_SCREEN_MIN_SEC,
        float(narration_duration_sec) + PLINKO_VICTORY_SCREEN_PAD_SEC,
    )


def plinko_winner_from_scores(team_scores: list[int] | tuple[int, ...]) -> int:
    """Maior pontuação vence; empate favorece o time 0."""
    if len(team_scores) < 2:
        return 0
    return 1 if team_scores[1] > team_scores[0] else 0


def plinko_pin_columns_for_width(
    usable_width: float,
    *,
    ball_radius: float = PLINKO_BALL_RADIUS,
    pin_radius: float = PLINKO_PIN_RADIUS,
    gap_margin: float = PLINKO_GAP_MARGIN,
) -> tuple[int, float]:
    """
    Retorna (número de pinos por fileira, espaçamento horizontal entre centros).

    Garante que a bola caiba entre dois pinos adjacentes na mesma linha.
    """
    min_spacing = plinko_min_pin_spacing(
        usable_width,
        ball_radius=ball_radius,
        pin_radius=pin_radius,
        gap_margin=gap_margin,
    )
    pin_cols = max(5, int(usable_width / min_spacing) - 1)
    spacing_x = usable_width / (pin_cols + 1)
    while pin_cols > 5 and spacing_x < min_spacing:
        pin_cols -= 1
        spacing_x = usable_width / (pin_cols + 1)
    return pin_cols, spacing_x


def agar_radius_after_collision(
    radius_a: float,
    radius_b: float,
    *,
    loser_shrink: float = AGAR_LOSER_SHRINK,
    winner_grow: float = AGAR_WINNER_GROW,
) -> tuple[float, float]:
    """Retorna (novo_raio_vencedor, novo_raio_perdedor) — vencedor = maior."""
    if radius_a >= radius_b:
        winner_r, loser_r = radius_a, radius_b
    else:
        winner_r, loser_r = radius_b, radius_a
    loser_r = max(MIN_RADIUS, loser_r * (1.0 - loser_shrink))
    winner_r = winner_r * (1.0 + winner_grow * (loser_r / max(radius_a, radius_b, 1.0)))
    if radius_a >= radius_b:
        return winner_r, loser_r
    return loser_r, winner_r


def territory_owner_ratios(grid: np.ndarray) -> tuple[float, float]:
    """Frações de células (0 = jogador 0, 1 = jogador 1)."""
    total = grid.size
    if total == 0:
        return 0.5, 0.5
    c0 = float(np.sum(grid == 0))
    c1 = float(np.sum(grid == 1))
    return c0 / total, c1 / total


def paint_territory_disk(
    grid: np.ndarray,
    cx_px: float,
    cy_px: float,
    owner: int,
    *,
    radius_px: float = TERRITORY_PAINT_RADIUS_PX,
    width_px: int = BATALHA_VIDEO_WIDTH,
    height_px: int = BATALHA_VIDEO_HEIGHT,
) -> None:
    """Pinta disco no grid de território (owner 0 ou 1)."""
    cols, rows = grid.shape[1], grid.shape[0]
    gx = int((cx_px / width_px) * cols)
    gy = int((cy_px / height_px) * rows)
    gr = max(1, int((radius_px / width_px) * cols))
    y0 = max(0, gy - gr)
    y1 = min(rows, gy + gr + 1)
    x0 = max(0, gx - gr)
    x1 = min(cols, gx + gr + 1)
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (xx - gx) ** 2 + (yy - gy) ** 2 <= gr * gr
    grid[y0:y1, x0:x1][mask] = owner


def _load_texture(path: Path, diameter: int) -> Image.Image:
    """
    Carrega textura base (sem máscara/borda) para uso em modos que não precisam de tratamento.

    No Plinko, usamos `_ball_texture_with_border` (cover + máscara AA + stroke).
    """
    with Image.open(path) as im:
        tex = im.convert("RGBA")
    d = max(32, diameter)
    return ImageOps.fit(tex, (d, d), centering=(0.5, 0.5), method=Image.Resampling.LANCZOS)


@lru_cache(maxsize=256)
def _ball_texture_with_border(path_str: str, diameter: int, rgb: tuple[int, int, int]) -> Image.Image:
    """
    Textura circular (cover, sem distorção) + borda grossa (stroke) com AA.

    Cacheado para evitar custo por frame (principalmente em Plinko).
    """
    d = max(32, int(diameter))
    border = max(4, int(round(d * 0.09)))  # ~6px para bolas ~56px
    scale = 4  # AA: desenha maior e reduz
    D = d * scale
    B = border * scale

    with Image.open(Path(path_str)) as im:
        src = im.convert("RGBA")
    fitted = ImageOps.fit(src, (D, D), centering=(0.5, 0.5), method=Image.Resampling.LANCZOS)

    out_big = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    draw_big = ImageDraw.Draw(out_big)
    # stroke
    draw_big.ellipse((0, 0, D - 1, D - 1), fill=(*rgb, 255))

    inner = Image.new("L", (D, D), 0)
    ImageDraw.Draw(inner).ellipse((B, B, D - B - 1, D - B - 1), fill=255)
    out_big.paste(fitted, (0, 0), inner)

    out = out_big.resize((d, d), Image.Resampling.LANCZOS)
    return out


def _circle_moment(radius: float, mass: float) -> float:
    return mass * (0.5 * radius**2)


def _ball_mass(radius: float, density: float = 0.0028) -> float:
    return max(1.0, density * math.pi * radius**2)


def draw_textured_ball(
    canvas: Image.Image,
    texture: Image.Image,
    x: float,
    y: float,
    angle_rad: float,
    radius: float,
) -> None:
    """Desenha bolinha com textura rotacionada conforme body.angle."""
    diameter = max(8, int(radius * 2))
    tex = texture
    if tex.size != (diameter, diameter):
        tex = tex.resize((diameter, diameter), Image.Resampling.LANCZOS)
    deg = -math.degrees(angle_rad)
    rotated = tex.rotate(deg, resample=Image.Resampling.BICUBIC, center=(diameter // 2, diameter // 2))
    px = int(x - diameter / 2)
    py = int(y - diameter / 2)
    canvas.alpha_composite(rotated, (px, py))


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    shadow_fill: tuple[int, int, int, int] = (0, 0, 0, 210),
    shadow_offset: tuple[int, int] = (3, 3),
) -> None:
    x, y = xy
    sx, sy = shadow_offset
    draw.text((x + sx, y + sy), text, font=font, fill=shadow_fill)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_text_with_stroke(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 255),
    stroke_px: int = 2,
) -> None:
    x, y = xy
    s = int(stroke_px)
    for dx, dy in ((-s, 0), (s, 0), (0, -s), (0, s), (-s, -s), (s, s), (-s, s), (s, -s)):
        draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)


@lru_cache(maxsize=6)
def _plinko_vignette(size: tuple[int, int]) -> Image.Image:
    """Vignette/gradiente simples (mais claro no centro, escuro nas bordas)."""
    w, h = size
    base = Image.new("RGBA", (w, h), (10, 12, 20, 255))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    cx, cy = w / 2, h / 2
    max_r = int(max(w, h) * 0.78)
    steps = 18
    for i in range(steps):
        t = i / max(steps - 1, 1)
        r = int(max_r * (0.22 + 0.78 * t))
        alpha = int(18 + 130 * t * t)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(0, 0, 0, alpha), width=max(6, int(10 * t)))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=18))
    base.alpha_composite(overlay)
    return base


class BatalhaSimulationBase(ABC):
    """Simulação 9:16 com paredes, gravidade ajustável e registro de colisões."""

    modo: str = ""

    def __init__(
        self,
        spec: BatalhaSpec,
        avatar_1_path: Path,
        avatar_2_path: Path,
        *,
        gravity: tuple[float, float] = (0.0, 0.0),
        damping: float = 0.98,
    ) -> None:
        self.spec = spec
        self.width = BATALHA_VIDEO_WIDTH
        self.height = BATALHA_VIDEO_HEIGHT
        self.space = pymunk.Space()
        self.space.gravity = gravity
        self.space.damping = damping
        self.sim_time = 0.0
        self.winner_id: int | None = None
        self._collision_recorder = _CollisionRecorder()
        self.balls: list[OpponentBall] = []
        self._setup_walls()
        self._setup_balls(avatar_1_path, avatar_2_path)
        self._setup_collision_handlers()
        self._post_setup()

    @property
    def collision_times_sec(self) -> list[float]:
        return self._collision_recorder.times_sec

    def _register_collision(self, impulse: float = 500.0) -> None:
        self._collision_recorder.register(self.sim_time, impulse=impulse)

    def _setup_walls(self) -> None:
        static = self.space.static_body
        m = MARGIN
        w, h = self.width, self.height
        segments = [
            ((m, m), (w - m, m)),
            ((w - m, m), (w - m, h - m)),
            ((w - m, h - m), (m, h - m)),
            ((m, h - m), (m, m)),
        ]
        for a, b in segments:
            seg = pymunk.Segment(static, a, b, WALL_THICKNESS)
            seg.elasticity = 0.85
            seg.friction = 0.6
            seg.collision_type = WALL_COLLISION_TYPE
            self.space.add(seg)

    def _make_ball(
        self,
        player_id: int,
        position: tuple[float, float],
        velocity: tuple[float, float],
        radius: float,
        texture_path: Path,
    ) -> OpponentBall:
        name = spec_name = self.spec[f"oponente_{player_id + 1}"]
        color_hex = self.spec[f"cor_{player_id + 1}"]
        rgb = hex_to_rgb(color_hex)
        mass = _ball_mass(radius)
        moment = _circle_moment(radius, mass)
        body = pymunk.Body(mass, moment)
        body.position = position
        body.velocity = velocity
        shape = pymunk.Circle(body, radius)
        shape.elasticity = 0.92
        shape.friction = 0.35
        shape.collision_type = BALL_COLLISION_TYPE
        self.space.add(body, shape)
        # Plinko pede textura com cover + máscara AA + borda (stroke).
        # Para os demais modos, ainda fica com aparência boa e custo baixo graças ao cache.
        tex = _ball_texture_with_border(str(Path(texture_path)), int(radius * 2), rgb)
        return OpponentBall(
            player_id=player_id,
            name=spec_name,
            color_rgb=rgb,
            body=body,
            shape=shape,
            texture=tex,
            radius=radius,
        )

    @abstractmethod
    def _setup_balls(self, avatar_1_path: Path, avatar_2_path: Path) -> None:
        ...

    def _setup_collision_handlers(self) -> None:
        """Pymunk 7+: ``Space.on_collision`` em vez de ``add_collision_handler``."""

        def on_ball_ball_begin(arbiter: pymunk.Arbiter, space: pymunk.Space, data: dict) -> None:
            impulse = float(arbiter.total_impulse.length) or 100.0
            data["sim"]._register_collision(impulse)
            data["sim"]._on_ball_ball_collision(arbiter)

        def on_ball_wall_begin(arbiter: pymunk.Arbiter, space: pymunk.Space, data: dict) -> None:
            impulse = float(arbiter.total_impulse.length) or 80.0
            data["sim"]._register_collision(impulse)

        def on_ball_pin_begin(arbiter: pymunk.Arbiter, space: pymunk.Space, data: dict) -> None:
            impulse = float(arbiter.total_impulse.length) or 80.0
            data["sim"]._register_collision(impulse)

        payload = {"sim": self}
        self.space.on_collision(
            BALL_COLLISION_TYPE,
            BALL_COLLISION_TYPE,
            begin=on_ball_ball_begin,
            data=payload,
        )
        self.space.on_collision(
            BALL_COLLISION_TYPE,
            WALL_COLLISION_TYPE,
            begin=on_ball_wall_begin,
            data=payload,
        )
        self.space.on_collision(
            BALL_COLLISION_TYPE,
            PIN_COLLISION_TYPE,
            begin=on_ball_pin_begin,
            data=payload,
        )

    @abstractmethod
    def _post_setup(self) -> None:
        ...

    @abstractmethod
    def _on_ball_ball_collision(self, arbiter: pymunk.Arbiter) -> None:
        ...

    def _set_ball_radius(self, ball: OpponentBall, new_radius: float) -> None:
        new_radius = max(MIN_RADIUS, new_radius)
        ball.radius = new_radius
        ball.shape.unsafe_set_radius(new_radius)
        density = 0.0028
        ball.body.mass = _ball_mass(new_radius, density)
        ball.body.moment = _circle_moment(new_radius, ball.body.mass)
        d = max(32, int(new_radius * 2))
        ball.texture = ball.texture.resize((d, d), Image.Resampling.LANCZOS)

    def _ball_from_shape(self, shape: pymunk.Shape) -> OpponentBall | None:
        for b in self.balls:
            if b.shape is shape or b.body is shape.body:
                return b
        return None

    def _alive_balls(self) -> list[OpponentBall]:
        return [b for b in self.balls if not b.eliminated]

    def finished(self) -> bool:
        return self.winner_id is not None

    def step(self, dt: float = DT) -> None:
        if self.finished():
            return
        self._last_dt = dt
        self.space.step(dt)
        self.sim_time += dt
        self._after_step()

    def _after_step(self) -> None:
        pass

    @abstractmethod
    def _render_background(self, canvas: Image.Image) -> None:
        ...

    def _draw_hud(self, draw: ImageDraw.ImageDraw) -> None:
        font = ImageFont.load_default()
        y = 24
        for ball in self.balls:
            label = ball.name[:18]
            pct = ""
            if self.modo == BATALHA_MOD_TAMANHO and not ball.eliminated:
                r0 = INITIAL_RADIUS
                pct = f" {int(100 * (ball.radius / r0) ** 2)}%"
            draw.text((24, y), f"{label}{pct}", fill=(*ball.color_rgb, 255), font=font)
            y += 22

    def render_frame(self) -> Image.Image:
        canvas = Image.new("RGBA", (self.width, self.height), (18, 18, 24, 255))
        self._render_background(canvas)
        for ball in self._alive_balls():
            if ball.radius < 1:
                continue
            pos = ball.body.position
            draw_textured_ball(
                canvas,
                ball.texture,
                float(pos.x),
                float(pos.y),
                float(ball.body.angle),
                ball.radius,
            )
        draw = ImageDraw.Draw(canvas)
        self._draw_hud(draw)
        self._draw_mode_overlay(draw)
        return canvas

    def _draw_mode_overlay(self, draw: ImageDraw.ImageDraw) -> None:
        pass

    def render_rgb_bytes(self) -> bytes:
        return self.render_frame().convert("RGB").tobytes()


class TamanhoSimulation(BatalhaSimulationBase):
    """Duelo Agar.io: colisões transferem massa/raio; quem zera perde."""

    modo = BATALHA_MOD_TAMANHO

    def __init__(self, spec: BatalhaSpec, avatar_1_path: Path, avatar_2_path: Path) -> None:
        super().__init__(spec, avatar_1_path, avatar_2_path, gravity=(0.0, 0.0), damping=0.99)

    def _setup_balls(self, avatar_1_path: Path, avatar_2_path: Path) -> None:
        cx = self.width / 2
        cy = self.height / 2
        offset = min(220, self.width * 0.22)
        self.balls = [
            self._make_ball(0, (cx - offset, cy), (220.0, 40.0), INITIAL_RADIUS, avatar_1_path),
            self._make_ball(1, (cx + offset, cy), (-220.0, -40.0), INITIAL_RADIUS, avatar_2_path),
        ]

    def _post_setup(self) -> None:
        pass

    def _on_ball_ball_collision(self, arbiter: pymunk.Arbiter) -> None:
        if self.finished():
            return
        shapes = arbiter.shapes
        if len(shapes) < 2:
            return
        b0 = self._ball_from_shape(shapes[0])
        b1 = self._ball_from_shape(shapes[1])
        if not b0 or not b1 or b0.eliminated or b1.eliminated:
            return
        r0, r1 = b0.radius, b1.radius
        new_w, new_l = agar_radius_after_collision(r0, r1)
        if r0 >= r1:
            self._set_ball_radius(b0, new_w)
            self._set_ball_radius(b1, new_l)
            loser, winner = b1, b0
        else:
            self._set_ball_radius(b0, new_l)
            self._set_ball_radius(b1, new_w)
            loser, winner = b0, b1
        min_r = INITIAL_RADIUS * math.sqrt(AGAR_MIN_MASS_RATIO)
        if loser.radius <= min_r + 0.5:
            loser.eliminated = True
            self.space.remove(loser.body, loser.shape)
            self.winner_id = winner.player_id

    def _render_background(self, canvas: Image.Image) -> None:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, self.width, self.height), fill=(22, 22, 30, 255))
        m = MARGIN
        draw.rectangle((m, m, self.width - m, self.height - m), outline=(60, 60, 80, 255), width=3)


class TerritorioSimulation(BatalhaSimulationBase):
    """Domínio: colisões pintam território; vitória por % da área."""

    modo = BATALHA_MOD_TERRITORIO

    def __init__(self, spec: BatalhaSpec, avatar_1_path: Path, avatar_2_path: Path) -> None:
        self._grid = np.zeros((TERRITORY_GRID_ROWS, TERRITORY_GRID_COLS), dtype=np.int8)
        self._grid[:, : TERRITORY_GRID_COLS // 2] = 0
        self._grid[:, TERRITORY_GRID_COLS // 2 :] = 1
        self._color0 = hex_to_rgb(spec["cor_1"])
        self._color1 = hex_to_rgb(spec["cor_2"])
        super().__init__(spec, avatar_1_path, avatar_2_path, gravity=(0.0, 0.0), damping=0.985)
        self._territory_bg = self._build_territory_background()

    def _setup_balls(self, avatar_1_path: Path, avatar_2_path: Path) -> None:
        cx = self.width / 2
        cy = self.height / 2
        offset = min(200, self.width * 0.2)
        self.balls = [
            self._make_ball(
                0,
                (cx - offset, cy),
                (160.0, 90.0),
                INITIAL_RADIUS * 0.85,
                avatar_1_path,
            ),
            self._make_ball(
                1,
                (cx + offset, cy),
                (-160.0, -90.0),
                INITIAL_RADIUS * 0.85,
                avatar_2_path,
            ),
        ]

    def _post_setup(self) -> None:
        pass

    def _build_territory_background(self) -> Image.Image:
        rows, cols = self._grid.shape
        arr = np.zeros((rows, cols, 3), dtype=np.uint8)
        arr[self._grid == 0] = self._color0
        arr[self._grid == 1] = self._color1
        small = Image.fromarray(arr, mode="RGB")
        return small.resize((self.width, self.height), Image.Resampling.NEAREST)

    def _refresh_territory_bg(self) -> None:
        rows, cols = self._grid.shape
        arr = np.zeros((rows, cols, 3), dtype=np.uint8)
        arr[self._grid == 0] = self._color0
        arr[self._grid == 1] = self._color1
        self._territory_bg = Image.fromarray(arr, mode="RGB").resize(
            (self.width, self.height),
            Image.Resampling.NEAREST,
        )

    def _on_ball_ball_collision(self, arbiter: pymunk.Arbiter) -> None:
        if self.finished():
            return
        shapes = arbiter.shapes
        if len(shapes) < 2:
            return
        b0 = self._ball_from_shape(shapes[0])
        b1 = self._ball_from_shape(shapes[1])
        if not b0 or not b1:
            return
        # Pinta com a cor do oponente mais “agressivo” (maior impulso relativo)
        v0 = b0.body.velocity.length
        v1 = b1.body.velocity.length
        painter = b0 if v0 >= v1 else b1
        cx = (float(b0.body.position.x) + float(b1.body.position.x)) / 2
        cy = (float(b0.body.position.y) + float(b1.body.position.y)) / 2
        paint_territory_disk(self._grid, float(cx), float(cy), painter.player_id)
        if int(self.sim_time * FPS) % 3 == 0:
            self._refresh_territory_bg()
        r0, r1 = territory_owner_ratios(self._grid)
        if r0 >= TERRITORY_WIN_RATIO:
            self.winner_id = 0
        elif r1 >= TERRITORY_WIN_RATIO:
            self.winner_id = 1

    def _after_step(self) -> None:
        if self.finished():
            return
        if int(self.sim_time * FPS) % 5 == 0:
            r0, r1 = territory_owner_ratios(self._grid)
            if r0 >= TERRITORY_WIN_RATIO:
                self.winner_id = 0
            elif r1 >= TERRITORY_WIN_RATIO:
                self.winner_id = 1

    def _render_background(self, canvas: Image.Image) -> None:
        canvas.paste(self._territory_bg, (0, 0))
        draw = ImageDraw.Draw(canvas)
        m = MARGIN
        draw.rectangle((m, m, self.width - m, self.height - m), outline=(255, 255, 255, 80), width=2)

    def _draw_mode_overlay(self, draw: ImageDraw.ImageDraw) -> None:
        r0, r1 = territory_owner_ratios(self._grid)
        bar_w = self.width - 80
        x0 = 40
        y0 = self.height - 56
        draw.rectangle((x0, y0, x0 + bar_w, y0 + 18), fill=(40, 40, 40, 200))
        split = int(bar_w * r0)
        draw.rectangle((x0, y0, x0 + split, y0 + 18), fill=(*self._color0, 255))
        draw.rectangle((x0 + split, y0, x0 + bar_w, y0 + 18), fill=(*self._color1, 255))
        draw.text((x0, y0 - 18), f"{int(r0 * 100)}% — {int(r1 * 100)}%", fill=(255, 255, 255, 255))


class PlinkoSimulation(BatalhaSimulationBase):
    """Plinko: pinos + cestos com pontuação; várias bolinhas por time (spawn temporizado)."""

    modo = BATALHA_MOD_PLINKO

    def __init__(
        self,
        spec: BatalhaSpec,
        avatar_1_path: Path,
        avatar_2_path: Path,
        *,
        logo_1_path: Path | None = None,
        logo_2_path: Path | None = None,
    ) -> None:
        self._avatar_paths = (Path(avatar_1_path), Path(avatar_2_path))
        self._logo_paths = (
            Path(logo_1_path) if logo_1_path else Path(avatar_1_path),
            Path(logo_2_path) if logo_2_path else Path(avatar_2_path),
        )
        self._pin_positions: list[tuple[float, float, float]] = []
        self._stuck_time: dict[int, float] = {}
        self._basket_scores: tuple[int, ...] = PLINKO_BASKET_SCORES
        self._basket_x_bounds: list[tuple[float, float]] = []
        self._team_scores: list[int] = [0, 0]
        self._scored_ball_ids: set[int] = set()
        self._spawn_pairs_released = 0
        self._sim_frame_index = 0
        self._team_colors: tuple[tuple[int, int, int], tuple[int, int, int]] = (
            hex_to_rgb(spec["cor_1"]),
            hex_to_rgb(spec["cor_2"]),
        )
        self._team_names: tuple[str, str] = (spec["oponente_1"], spec["oponente_2"])
        self._plinko_pending_winner_id: int | None = None
        self._plinko_end_phase: str | None = None
        self._plinko_post_settle_remaining = 0.0
        self._plinko_victory_remaining = 0.0
        self._plinko_show_victory_screen = False
        self.plinko_victory_screen_duration_sec = PLINKO_VICTORY_SCREEN_MIN_SEC
        self.plinko_victory_screen_start_sec: float | None = None
        super().__init__(
            spec,
            avatar_1_path,
            avatar_2_path,
            gravity=PLINKO_GRAVITY,
            damping=PLINKO_DAMPING,
        )

    def _register_collision(self, impulse: float = 500.0) -> None:
        """Plinko: registra quase todo impacto (pino, parede, bola, cesto)."""
        self._collision_recorder.register(
            self.sim_time,
            min_impulse=PLINKO_COLLISION_MIN_IMPULSE,
            impulse=impulse,
            debounce_sec=PLINKO_COLLISION_DEBOUNCE_SEC,
        )

    def _setup_balls(self, avatar_1_path: Path, avatar_2_path: Path) -> None:
        del avatar_1_path, avatar_2_path
        self.balls = []

    def _setup_walls(self) -> None:
        """Arena sem chão duplicado — o chão dos cestos é adicionado em ``_spawn_baskets``."""
        static = self.space.static_body
        m = MARGIN
        w, h = self.width, self.height
        for a, b in (
            ((m, m), (w - m, m)),
            ((w - m, m), (w - m, h - m)),
            ((m, h - m), (m, m)),
        ):
            seg = pymunk.Segment(static, a, b, WALL_THICKNESS)
            seg.elasticity = 0.85
            seg.friction = 0.6
            seg.collision_type = WALL_COLLISION_TYPE
            self.space.add(seg)

    def _post_setup(self) -> None:
        self._spawn_pins()
        self._spawn_baskets()

    def _spawn_baskets(self) -> None:
        """Chão + paredes entre cestos; sem teto (entrada livre por cima)."""
        static = self.space.static_body
        m = plinko_playfield_margin()
        self._basket_x_bounds = plinko_basket_x_bounds(self.width, self._basket_scores)
        y_top = PLINKO_BASKET_ZONE_TOP
        y_bot = PLINKO_BASKET_FLOOR_Y
        x_right = self.width - m

        floor = pymunk.Segment(static, (m, y_bot), (x_right, y_bot), WALL_THICKNESS)
        floor.elasticity = PLINKO_ELASTICITY
        floor.friction = 0.55
        floor.collision_type = WALL_COLLISION_TYPE
        self.space.add(floor)

        divider_r = min(
            plinko_basket_divider_radius_for_gap(width_px=self.width, scores=self._basket_scores),
            PLINKO_BASKET_DIVIDER_PHYSICS_RADIUS,
        )
        for x_min, _x_max in self._basket_x_bounds[1:]:
            seg = pymunk.Segment(static, (x_min, y_top), (x_min, y_bot), divider_r)
            seg.elasticity = PLINKO_BASKET_DIVIDER_ELASTICITY
            seg.friction = PLINKO_BASKET_DIVIDER_FRICTION
            seg.collision_type = WALL_COLLISION_TYPE
            self.space.add(seg)

    def _spawn_ball_pair(self, pair_index: int) -> None:
        cx = self.width / 2
        lane = min(120, self.width * 0.11)
        spread = (pair_index - (PLINKO_BALLS_PER_TEAM - 1) / 2.0) * 14.0
        y0 = MARGIN + 88
        r = PLINKO_BALL_RADIUS
        av1, av2 = self._avatar_paths
        balls = [
            self._make_ball(0, (cx - lane + spread, y0), (30.0, 0.0), r, av1),
            self._make_ball(1, (cx + lane - spread * 0.35, y0), (-25.0, 0.0), r, av2),
        ]
        for ball in balls:
            ball.shape.elasticity = PLINKO_ELASTICITY
            ball.shape.friction = 0.15
        self.balls.extend(balls)

    def _try_spawn_ball_pair(self) -> None:
        if self._spawn_pairs_released >= PLINKO_BALLS_PER_TEAM:
            return
        if self._sim_frame_index % PLINKO_SPAWN_INTERVAL_FRAMES != 0:
            return
        self._spawn_ball_pair(self._spawn_pairs_released)
        self._spawn_pairs_released += 1

    def _spawn_pins(self) -> None:
        static = self.space.static_body
        m = plinko_playfield_margin()
        usable_w = self.width - 2 * m
        pin_cols, spacing_x = plinko_pin_columns_for_width(usable_w)
        row_spacing_y = spacing_x * PLINKO_ROW_SPACING_FACTOR
        y_top = m + 110
        y_bottom = PLINKO_FINISH_Y - PLINKO_PIN_RADIUS - PLINKO_PIN_BOTTOM_MARGIN

        row_ys: list[float] = []
        row = 0
        while True:
            y = y_top + row * row_spacing_y
            if y > y_bottom:
                break
            row_ys.append(y)
            row += 1
        if len(row_ys) > 1:
            row_ys = row_ys[:-1]

        self._pin_positions = []
        for row_idx, y in enumerate(row_ys):
            x_shift = spacing_x / 2.0 if row_idx % 2 else 0.0
            for col in range(pin_cols):
                x = m + (col + 1) * spacing_x + x_shift
                if x - PLINKO_PIN_RADIUS < m or x + PLINKO_PIN_RADIUS > self.width - m:
                    continue
                pin = pymunk.Circle(static, PLINKO_PIN_RADIUS, (float(x), float(y)))
                pin.elasticity = PLINKO_ELASTICITY
                pin.friction = 0.12
                pin.collision_type = PIN_COLLISION_TYPE
                self.space.add(pin)
                self._pin_positions.append((float(x), float(y), PLINKO_PIN_RADIUS))

    def _unstick_slow_balls(self, dt: float) -> None:
        """Empurra bolas presas entre pinos (velocidade baixa por muito tempo)."""
        for ball in self._alive_balls():
            if id(ball.body) in self._scored_ball_ids:
                continue
            key = id(ball.body)
            speed = float(ball.body.velocity.length)
            if speed < PLINKO_STUCK_SPEED:
                self._stuck_time[key] = self._stuck_time.get(key, 0.0) + dt
            else:
                self._stuck_time[key] = 0.0
            if self._stuck_time.get(key, 0.0) < PLINKO_STUCK_SEC:
                continue
            self._stuck_time[key] = 0.0
            impulse_x = 900.0 if ball.player_id == 0 else -900.0
            ball.body.apply_impulse_at_local_point((impulse_x, 2200.0))
            ball.body.velocity = ball.body.velocity * 0.2 + pymunk.Vec2d(impulse_x * 0.002, 180.0)

    def _on_ball_ball_collision(self, arbiter: pymunk.Arbiter) -> None:
        pass

    def _score_balls_in_baskets(self) -> None:
        """Soma pontos ao entrar na cesta (faixa X); física segue livre (sem congelar)."""
        if not self._basket_x_bounds:
            return
        for ball in self._alive_balls():
            body_id = id(ball.body)
            if body_id in self._scored_ball_ids:
                continue
            y = float(ball.body.position.y)
            if not plinko_ball_entered_basket_zone(y, radius=ball.radius):
                continue
            x = float(ball.body.position.x)
            idx = plinko_basket_index_for_x(x, self._basket_x_bounds)
            self._team_scores[ball.player_id] += self._basket_scores[idx]
            self._scored_ball_ids.add(body_id)

    def _plinko_expected_ball_count(self) -> int:
        return PLINKO_BALLS_PER_TEAM * 2

    def _plinko_all_balls_spawned(self) -> bool:
        return (
            self._spawn_pairs_released >= PLINKO_BALLS_PER_TEAM
            and len(self.balls) >= self._plinko_expected_ball_count()
        )

    def _plinko_ball_settled(self, ball: OpponentBall) -> bool:
        if id(ball.body) not in self._scored_ball_ids:
            return False
        if float(ball.body.velocity.length) > PLINKO_BASKET_REST_SPEED:
            return False
        y = float(ball.body.position.y)
        return plinko_ball_entered_basket_zone(y, radius=ball.radius)

    def _plinko_all_balls_scored(self) -> bool:
        """Fim da fase de jogo: todas as bolinhas já caíram e foram pontuadas."""
        if not self._plinko_all_balls_spawned():
            return False
        return len(self._scored_ball_ids) >= self._plinko_expected_ball_count()

    def _plinko_all_balls_settled(self) -> bool:
        """Compatível com testes; preferir ``_plinko_all_balls_scored`` no fluxo ao vivo."""
        return self._plinko_all_balls_scored()

    def _freeze_scored_balls(self) -> None:
        for ball in self._alive_balls():
            if id(ball.body) not in self._scored_ball_ids:
                continue
            ball.body.velocity = (0.0, 0.0)
            ball.body.angular_velocity = 0.0

    def _begin_plinko_post_settle(self) -> None:
        if self._plinko_end_phase is not None:
            return
        self._plinko_pending_winner_id = plinko_winner_from_scores(self._team_scores)
        self._plinko_end_phase = "post_settle"
        self._plinko_post_settle_remaining = PLINKO_POST_SETTLE_SEC
        self._freeze_scored_balls()

    def _begin_plinko_victory_screen(self) -> None:
        self._plinko_end_phase = "victory"
        self._plinko_show_victory_screen = True
        self.plinko_victory_screen_start_sec = self.sim_time
        self._plinko_victory_remaining = self.plinko_victory_screen_duration_sec
        self._freeze_scored_balls()

    def _tick_plinko_end_phases(self, dt: float) -> None:
        self._freeze_scored_balls()
        if self._plinko_end_phase == "post_settle":
            self._plinko_post_settle_remaining -= dt
            if self._plinko_post_settle_remaining <= 0:
                self._begin_plinko_victory_screen()
            return
        if self._plinko_end_phase == "victory":
            self._plinko_victory_remaining -= dt
            if self._plinko_victory_remaining <= 0:
                self.winner_id = self._plinko_pending_winner_id
                self._plinko_end_phase = None

    def _after_step(self) -> None:
        if self.finished():
            return
        dt = getattr(self, "_last_dt", DT)
        if self._plinko_end_phase is not None:
            self._tick_plinko_end_phases(dt)
            return
        self._try_spawn_ball_pair()
        self._sim_frame_index += 1
        self._unstick_slow_balls(dt)
        self._score_balls_in_baskets()
        if self._plinko_all_balls_scored():
            self._begin_plinko_post_settle()

    def _winner_logo_image(self) -> Image.Image | None:
        wid = self._plinko_pending_winner_id
        if wid is None or wid not in (0, 1):
            return None
        path = self._logo_paths[wid]
        if not path.is_file():
            return None
        try:
            with Image.open(path) as im:
                return im.convert("RGBA")
        except OSError:
            return None

    def render_frame(self) -> Image.Image:
        canvas = Image.new("RGBA", (self.width, self.height), (18, 18, 24, 255))
        if self._plinko_show_victory_screen:
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 0, self.width, self.height), fill=(10, 12, 20, 255))
            self._draw_victory_finale(draw, canvas)
            return canvas
        self._render_background(canvas)
        for ball in self._alive_balls():
            if ball.radius < 1:
                continue
            pos = ball.body.position
            draw_textured_ball(
                canvas,
                ball.texture,
                float(pos.x),
                float(pos.y),
                float(ball.body.angle),
                ball.radius,
            )
        draw = ImageDraw.Draw(canvas)
        self._draw_hud(draw)
        return canvas

    @staticmethod
    def _draw_centered_text(
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        text: str,
        *,
        fill: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((cx - tw / 2, cy - th / 2), text, fill=fill, font=font)

    def _draw_hud(self, draw: ImageDraw.ImageDraw) -> None:
        # HUD estilo mobile: duas pílulas (esq/dir), tipografia pesada e sombra.
        name_font = _batalha_font(PLINKO_HUD_NAME_FONT_SIZE)
        score_font = _batalha_font(PLINKO_HUD_SCORE_FONT_SIZE * 2)

        top = 18
        pad_x = 18
        pill_h = 96
        pill_w = int(self.width * 0.44)
        radius = 26
        fill = (14, 16, 26, 190)
        outline = (70, 80, 110, 150)

        left = (pad_x, top, pad_x + pill_w, top + pill_h)
        right = (self.width - pad_x - pill_w, top, self.width - pad_x, top + pill_h)

        for rect in (left, right):
            draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=2)

        for pid, rect in ((0, left), (1, right)):
            x0, y0, x1, y1 = rect
            name = self._team_names[pid][:16]
            pts = str(self._team_scores[pid])
            color = self._team_colors[pid]
            # centralização vertical (com respiro) para evitar "colar no teto"
            inner_pad_top = 10
            inner_pad_bot = 10
            inner_h = max(1, (y1 - y0) - inner_pad_top - inner_pad_bot)
            top_y = y0 + inner_pad_top

            name_bbox = draw.textbbox((0, 0), name, font=name_font)
            name_h = name_bbox[3] - name_bbox[1]
            pts_bbox = draw.textbbox((0, 0), pts, font=score_font)
            pts_w = pts_bbox[2] - pts_bbox[0]
            pts_h = pts_bbox[3] - pts_bbox[1]

            # layout em duas linhas: nome em cima, score em baixo, ambos "respirando"
            gap = 6
            total_h = name_h + gap + pts_h
            start_y = top_y + max(0, (inner_h - total_h) // 2)

            name_x = x0 + 18
            name_y = start_y
            pts_x = x1 - 18 - pts_w
            pts_y = start_y + name_h + gap

            # nome (menor)
            _draw_text_with_shadow(
                draw,
                (name_x, name_y),
                name,
                font=name_font,
                fill=(240, 240, 245, 255),
            )
            # score (gigante, cor do time)
            _draw_text_with_shadow(
                draw,
                (pts_x, pts_y),
                pts,
                font=score_font,
                fill=(*color, 255),
            )

    def _render_background(self, canvas: Image.Image) -> None:
        # fundo com vignette/gradiente (retenção: profundidade e contraste)
        base = _plinko_vignette((self.width, self.height))
        canvas.alpha_composite(base)
        draw = ImageDraw.Draw(canvas)
        m = MARGIN
        draw.rounded_rectangle(
            (m, m, self.width - m, self.height - m),
            radius=28,
            outline=(90, 110, 170, 180),
            width=3,
        )

        y_top = PLINKO_BASKET_ZONE_TOP
        y_bot = PLINKO_BASKET_FLOOR_Y
        score_font = _batalha_font(int(PLINKO_BASKET_LABEL_FONT_SIZE * 1.55))
        label_y = (y_top + y_bot) / 2 - 4.0
        for i, (x0, x1) in enumerate(self._basket_x_bounds):
            tint = (20, 24, 40, 230) if i % 2 == 0 else (16, 18, 32, 230)
            draw.rectangle((x0, y_top, x1, y_bot), fill=tint)

        # divisórias (espessura alinhada ao raio físico da cápsula)
        div_w = int(PLINKO_BASKET_DIVIDER_RADIUS * 2)
        div_color = (255, 200, 70, 255)
        for x0, _x1 in self._basket_x_bounds[1:]:
            x = int(round(x0 - div_w / 2))
            draw.rounded_rectangle(
                (x, y_top, x + div_w, y_bot),
                radius=max(4, div_w // 2),
                fill=div_color,
            )

        # fundo luminoso dos cestos (overlay com alpha_composite)
        basket_overlay = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        od = ImageDraw.Draw(basket_overlay)
        inset = int(PLINKO_BASKET_DIVIDER_RADIUS) + 6
        for i, (x0, x1) in enumerate(self._basket_x_bounds):
            score_val = self._basket_scores[i]
            fill_rgba = (255, 215, 0, 30) if score_val == 100 else (255, 255, 255, 15)
            od.rectangle(
                (int(x0 + inset), int(y_top), int(x1 - inset), int(y_bot)),
                fill=fill_rgba,
            )
        canvas.alpha_composite(basket_overlay)

        # números gigantes por cima (com stroke)
        for i, (x0, x1) in enumerate(self._basket_x_bounds):
            score = str(self._basket_scores[i])
            is_center = self._basket_scores[i] == 100
            color = (255, 215, 0, 255) if is_center else (245, 245, 248, 255)

            bbox = draw.textbbox((0, 0), score, font=score_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            _draw_text_with_stroke(
                draw,
                ((x0 + x1) / 2 - tw / 2, label_y - th / 2),
                score,
                font=score_font,
                fill=color,
                stroke_px=2,
            )

        # pinos com glow neon (camada desfocada + pino sólido)
        for ox, oy, r in self._pin_positions:
            glow_r = max(10, int(r * 3.2))
            glow = Image.new("RGBA", (glow_r * 2, glow_r * 2), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            gd.ellipse((0, 0, glow_r * 2 - 1, glow_r * 2 - 1), fill=(60, 255, 255, 90))
            glow = glow.filter(ImageFilter.GaussianBlur(radius=max(6, int(glow_r * 0.28))))
            canvas.alpha_composite(glow, (int(ox - glow_r), int(oy - glow_r)))

            pr = int(r)
            draw.ellipse((ox - pr, oy - pr, ox + pr, oy + pr), fill=(235, 245, 255, 255))

    def _draw_victory_finale(self, draw: ImageDraw.ImageDraw, canvas: Image.Image) -> None:
        wid = self._plinko_pending_winner_id
        if wid is None:
            return
        name = self._team_names[wid]
        color = self._team_colors[wid]
        victory_line = plinko_victory_narration_text(name)

        headline_font = _batalha_font(PLINKO_VICTORY_HEADLINE_FONT_SIZE)
        name_font = _batalha_font(PLINKO_VICTORY_NAME_FONT_SIZE)
        cx = self.width / 2
        text_y = self.height * PLINKO_VICTORY_TEXT_Y_RATIO
        self._draw_centered_text(
            draw,
            cx,
            text_y,
            victory_line.upper(),
            fill=(255, 230, 120, 255),
            font=headline_font,
        )

        logo = self._winner_logo_image()
        logo_top = self.height * PLINKO_VICTORY_LOGO_Y_RATIO
        if logo is not None:
            bbox = _paste_image_contain(
                canvas,
                logo,
                cx=cx,
                top_y=logo_top,
                max_w=PLINKO_VICTORY_LOGO_MAX_W,
                max_h=PLINKO_VICTORY_LOGO_MAX_H,
            )
            if bbox is not None:
                draw = ImageDraw.Draw(canvas)
                lx0, ly0, lx1, ly1 = bbox
                pad = 6
                draw.rounded_rectangle(
                    (lx0 - pad, ly0 - pad, lx1 + pad, ly1 + pad),
                    radius=16,
                    outline=(*color, 255),
                    width=4,
                )
        else:
            self._draw_centered_text(
                draw,
                cx,
                logo_top + PLINKO_VICTORY_LOGO_MAX_H / 2,
                name[:24],
                fill=(*color, 255),
                font=name_font,
            )

    def _draw_mode_overlay(self, draw: ImageDraw.ImageDraw) -> None:
        pass


def create_simulation(
    modo: str,
    spec: BatalhaSpec,
    avatar_1_path: Path,
    avatar_2_path: Path,
    *,
    logo_1_path: Path | None = None,
    logo_2_path: Path | None = None,
) -> BatalhaSimulationBase:
    modo_norm = normalize_batalha_modo(modo)
    if modo_norm == BATALHA_MOD_TERRITORIO:
        return TerritorioSimulation(spec, avatar_1_path, avatar_2_path)
    if modo_norm == BATALHA_MOD_PLINKO:
        return PlinkoSimulation(
            spec,
            avatar_1_path,
            avatar_2_path,
            logo_1_path=logo_1_path,
            logo_2_path=logo_2_path,
        )
    return TamanhoSimulation(spec, avatar_1_path, avatar_2_path)


def iter_simulation_frames(
    sim: BatalhaSimulationBase,
    *,
    fps: float = FPS,
    max_duration_sec: float = MAX_SIM_DURATION_SEC,
    cancel_check: Any | None = None,
) -> Iterator[bytes]:
    """Avança a simulação e produz frames RGB24 até vitória ou tempo máximo."""
    dt = 1.0 / fps
    max_steps = int(max_duration_sec * fps)
    for _ in range(max_steps):
        if cancel_check is not None:
            raise_if_cancelled(cancel_check)
        if sim.finished():
            hold_sec = PLINKO_END_ITER_HOLD_SEC if sim.modo == BATALHA_MOD_PLINKO else 0.8
            for _hold in range(int(fps * hold_sec)):
                yield sim.render_rgb_bytes()
            break
        sim.step(dt)
        yield sim.render_rgb_bytes()
    else:
        if sim.modo == BATALHA_MOD_PLINKO:
            if sim.winner_id is None and hasattr(sim, "_team_scores"):
                sim.winner_id = plinko_winner_from_scores(sim._team_scores)
            ensure_plinko_victory_screen(sim)
            hold_sec = max(
                PLINKO_END_ITER_HOLD_SEC,
                getattr(sim, "plinko_victory_screen_duration_sec", PLINKO_VICTORY_SCREEN_MIN_SEC),
            )
            for _hold in range(int(fps * hold_sec)):
                yield sim.render_rgb_bytes()
        elif sim.winner_id is None:
            alive = sim._alive_balls()
            if alive:
                sim.winner_id = max(alive, key=lambda b: b.radius).player_id


def run_batalha_simulation(
    assets: BatalhaAssets,
    modo: str | None = None,
    *,
    fps: float = FPS,
    max_duration_sec: float = MAX_SIM_DURATION_SEC,
    cancel_event: Any | None = None,
    collect_frames: bool = False,
) -> BatalhaSimulationResult:
    """
    Executa simulação completa; timestamps de colisão para SFX (Fase 3).

    Por padrão não acumula todos os frames em RAM (~6 MB/frame em 1080×1920).
    Use ``collect_frames=True`` ou ``iter_simulation_frames`` para FFmpeg stdin.
    """
    modo_norm = normalize_batalha_modo(modo)
    sim = create_simulation(
        modo_norm,
        assets.spec,
        assets.avatar_1_path,
        assets.avatar_2_path,
        logo_1_path=assets.logo_1_path,
        logo_2_path=assets.logo_2_path,
    )
    frames: list[bytes] = []
    preview_first: bytes | None = None
    preview_last: bytes | None = None
    frame_count = 0
    for frame_bytes in iter_simulation_frames(
        sim,
        fps=fps,
        max_duration_sec=max_duration_sec,
        cancel_check=cancel_event,
    ):
        frame_count += 1
        preview_last = frame_bytes
        if preview_first is None:
            preview_first = frame_bytes
        if collect_frames:
            frames.append(frame_bytes)
    if not collect_frames and preview_first and preview_last:
        frames = [preview_first, preview_last]
    return BatalhaSimulationResult(
        frames=frames,
        collision_times_sec=sim.collision_times_sec,
        winner_id=sim.winner_id,
        duration_sec=sim.sim_time,
        modo=modo_norm,
        frame_count=frame_count,
    )


def save_simulation_artifacts(
    result: BatalhaSimulationResult,
    work_dir: Path,
    *,
    save_preview_frames: bool = True,
) -> Path:
    """Grava collisions.json e opcionalmente primeiro/último frame PNG."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "winner_id": result.winner_id,
        "duration_sec": round(result.duration_sec, 3),
        "modo": result.modo,
        "frame_count": len(result.frames),
        "collision_times_sec": result.collision_times_sec,
        "width": result.frame_size[0],
        "height": result.frame_size[1],
        "fps": FPS,
    }
    (work_dir / "simulation_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if save_preview_frames and result.frames:
        w, h = result.frame_size
        result.frames[0]
        Image.frombytes("RGB", (w, h), result.frames[0]).save(work_dir / "frame_first.png")
        Image.frombytes("RGB", (w, h), result.frames[-1]).save(work_dir / "frame_last.png")
    return work_dir
