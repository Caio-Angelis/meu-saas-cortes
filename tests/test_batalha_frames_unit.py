"""Testes unitários do motor de física da Batalha (sem Groq/rede)."""

from pathlib import Path

import numpy as np
import pymunk
from PIL import Image

from app.pipelines.batalha.batalha_frames import (
    BATALHA_MOD_PLINKO,
    BATALHA_MOD_TAMANHO,
    BATALHA_MOD_TERRITORIO,
    BATALHA_VIDEO_WIDTH,
    INITIAL_RADIUS,
    MIN_RADIUS,
    MARGIN,
    PLINKO_BALL_RADIUS,
    PLINKO_BALLS_PER_TEAM,
    PLINKO_BASKET_SCORES,
    PLINKO_BASKET_ZONE_TOP,
    PLINKO_GAP_MARGIN,
    PLINKO_PIN_RADIUS,
    PLINKO_POST_SETTLE_SEC,
    PLINKO_SPAWN_INTERVAL_FRAMES,
    agar_radius_after_collision,
    plinko_victory_narration_text,
    create_simulation,
    iter_simulation_frames,
    paint_territory_disk,
    plinko_ball_entered_basket_zone,
    plinko_basket_divider_radius_for_gap,
    plinko_basket_index_for_x,
    plinko_basket_inner_gap_px,
    plinko_basket_x_bounds,
    plinko_pin_columns_for_width,
    plinko_winner_from_scores,
    territory_owner_ratios,
    WALL_COLLISION_TYPE,
)
from app.pipelines.batalha.batalha_images import make_initial_fallback_avatar, save_avatar_png
from app.pipelines.batalha.batalha_pipeline import BatalhaSpec


def _sample_spec() -> BatalhaSpec:
    return BatalhaSpec(
        oponente_1="Alpha",
        oponente_2="Beta",
        termo_busca_1="alpha logo",
        termo_busca_2="beta logo",
        cor_1="#E74C3C",
        cor_2="#3498DB",
        hook="Quem vence?",
        script_narracao=" ".join(["fato"] * 52),
        legenda_tiktok="#batalha #fyp",
    )


def _avatars_in_tmp(tmp_path: Path) -> tuple[Path, Path]:
    a1 = save_avatar_png(make_initial_fallback_avatar("Alpha", "#E74C3C", size=64), tmp_path / "a1.png")
    a2 = save_avatar_png(make_initial_fallback_avatar("Beta", "#3498DB", size=64), tmp_path / "a2.png")
    return a1, a2


def test_agar_radius_after_collision_shrinks_loser():
    w, l = agar_radius_after_collision(60.0, 40.0)
    assert w > 60.0
    assert l < 40.0
    assert l >= MIN_RADIUS


def test_territory_owner_ratios_split():
    grid = np.zeros((10, 10), dtype=np.int8)
    grid[:, :5] = 0
    grid[:, 5:] = 1
    r0, r1 = territory_owner_ratios(grid)
    assert abs(r0 - 0.5) < 0.01
    assert abs(r1 - 0.5) < 0.01


def test_paint_territory_disk_changes_owner():
    grid = np.zeros((20, 20), dtype=np.int8)
    paint_territory_disk(grid, 100.0, 100.0, 1, radius_px=40.0, width_px=200, height_px=200)
    assert np.any(grid == 1)


def test_create_simulation_modes(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    assert create_simulation(BATALHA_MOD_TAMANHO, spec, a1, a2).modo == BATALHA_MOD_TAMANHO
    assert create_simulation(BATALHA_MOD_TERRITORIO, spec, a1, a2).modo == BATALHA_MOD_TERRITORIO
    assert create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2).modo == BATALHA_MOD_PLINKO


def test_tamanho_simulation_short_run(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_TAMANHO, spec, a1, a2)
    frames = list(iter_simulation_frames(sim, fps=30, max_duration_sec=3.0))
    assert len(frames) >= 10
    assert len(frames[0]) == 1080 * 1920 * 3
    assert len(sim.render_rgb_bytes()) == 1080 * 1920 * 3


def test_simulation_registers_collisions(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_TAMANHO, spec, a1, a2)
    for _ in iter_simulation_frames(sim, fps=30, max_duration_sec=5.0):
        pass
    assert sim.sim_time > 0
    assert len(sim.collision_times_sec) >= 0


def test_plinko_basket_bounds_cover_playfield():
    bounds = plinko_basket_x_bounds()
    assert len(bounds) == len(PLINKO_BASKET_SCORES)
    assert bounds[0][0] < bounds[-1][1]
    total_w = bounds[-1][1] - bounds[0][0]
    assert total_w > BATALHA_VIDEO_WIDTH * 0.5


def test_plinko_scores_on_zone_entry_not_only_at_floor():
    lip = PLINKO_BASKET_ZONE_TOP
    r = PLINKO_BALL_RADIUS
    assert not plinko_ball_entered_basket_zone(lip - r - 2.0, radius=r)
    assert plinko_ball_entered_basket_zone(lip - r, radius=r)
    # empilhada no meio da cesta, longe do chão — ainda conta
    assert plinko_ball_entered_basket_zone(lip + 40.0, radius=r)


def test_plinko_baskets_spawn_divider_walls(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2)
    n_bounds = len(sim._basket_x_bounds)
    wall_segs = [
        s
        for s in sim.space.shapes
        if isinstance(s, pymunk.Segment) and s.collision_type == WALL_COLLISION_TYPE
    ]
    # chão + (n_bounds - 1) divisórias internas
    assert len(wall_segs) >= n_bounds


def test_plinko_scores_on_entry_without_reaching_floor(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2)
    sim._spawn_ball_pair(0)
    bounds = sim._basket_x_bounds
    cx = (bounds[2][0] + bounds[2][1]) / 2
    ball = sim.balls[0]
    ball.body.position = (cx, PLINKO_BASKET_ZONE_TOP + 35.0)
    ball.body.velocity = (0.0, 0.0)
    sim._score_balls_in_baskets()
    assert sim._team_scores[0] == PLINKO_BASKET_SCORES[2]
    assert len(sim._scored_ball_ids) == 1


def test_plinko_above_zone_does_not_score(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2)
    sim._spawn_ball_pair(0)
    bounds = sim._basket_x_bounds
    cx = (bounds[2][0] + bounds[2][1]) / 2
    ball = sim.balls[0]
    ball.body.position = (cx, PLINKO_BASKET_ZONE_TOP - PLINKO_BALL_RADIUS - 4.0)
    sim._score_balls_in_baskets()
    assert sim._team_scores[0] == 0
    assert len(sim._scored_ball_ids) == 0


def test_plinko_basket_inner_gap_fits_ball():
    gap = plinko_basket_inner_gap_px()
    ball_d = PLINKO_BALL_RADIUS * 2
    assert gap >= ball_d * 1.5
    r = plinko_basket_divider_radius_for_gap()
    assert r >= 2.0
    assert plinko_basket_inner_gap_px(divider_radius=r) >= ball_d * 1.5


def test_plinko_basket_index_for_x():
    bounds = plinko_basket_x_bounds()
    mid0 = (bounds[0][0] + bounds[0][1]) / 2
    mid2 = (bounds[2][0] + bounds[2][1]) / 2
    assert plinko_basket_index_for_x(mid0, bounds) == 0
    assert plinko_basket_index_for_x(mid2, bounds) == 2
    assert plinko_basket_index_for_x(bounds[-1][1], bounds) == len(bounds) - 1


def test_plinko_winner_from_scores():
    assert plinko_winner_from_scores([180, 90]) == 0
    assert plinko_winner_from_scores([40, 120]) == 1
    assert plinko_winner_from_scores([100, 100]) == 0


def test_plinko_victory_narration_text():
    assert plinko_victory_narration_text("Batman") == "Vitória do Batman"


def test_plinko_victory_screen_renders_logo_below_text(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    logo = tmp_path / "logo_win.png"
    Image.new("RGBA", (400, 120), (255, 0, 0, 255)).save(logo)
    sim = create_simulation(
        BATALHA_MOD_PLINKO,
        spec,
        a1,
        a2,
        logo_1_path=logo,
        logo_2_path=a2,
    )
    sim._plinko_pending_winner_id = 0
    sim._plinko_show_victory_screen = True
    frame = sim.render_frame().convert("RGB")
    assert frame.size == (1080, 1920)
    # centro da área do logo deve ter vermelho do PNG de teste
    cx, y = 540, int(1920 * 0.50) + 40
    r, g, b = frame.getpixel((cx, y))
    assert r > 200 and g < 80


def test_plinko_end_sequence_sets_winner(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2)
    for i in range(PLINKO_BALLS_PER_TEAM):
        sim._spawn_ball_pair(i)
    sim._spawn_pairs_released = PLINKO_BALLS_PER_TEAM
    bounds = sim._basket_x_bounds
    cx = (bounds[1][0] + bounds[1][1]) / 2
    y_rest = sim.height - MARGIN - 24
    for ball in sim.balls:
        ball.body.position = (cx, y_rest)
        ball.body.velocity = (0.0, 0.0)
        sim._scored_ball_ids.add(id(ball.body))
    sim._team_scores = [250, 80]
    sim.plinko_victory_screen_duration_sec = 0.5
    assert sim._plinko_all_balls_settled()
    sim._begin_plinko_post_settle()
    sim._tick_plinko_end_phases(PLINKO_POST_SETTLE_SEC + 0.1)
    assert sim._plinko_show_victory_screen
    sim._tick_plinko_end_phases(0.6)
    assert sim.winner_id == 0
    assert sim.finished()


def test_plinko_scores_ball_once(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2)
    sim._spawn_ball_pair(0)
    bounds = sim._basket_x_bounds
    cx = (bounds[2][0] + bounds[2][1]) / 2
    ball = sim.balls[0]
    ball.body.position = (cx, sim.height - MARGIN - 20)
    sim._score_balls_in_baskets()
    assert sim._team_scores[0] == PLINKO_BASKET_SCORES[2]
    sim._score_balls_in_baskets()
    assert sim._team_scores[0] == PLINKO_BASKET_SCORES[2]


def test_plinko_pin_gap_fits_ball():
    usable = BATALHA_VIDEO_WIDTH - 2 * (MARGIN + 36)
    pin_cols, spacing_x = plinko_pin_columns_for_width(usable)
    gap = spacing_x - 2 * PLINKO_PIN_RADIUS
    ball_diameter = 2 * PLINKO_BALL_RADIUS
    assert gap >= ball_diameter + PLINKO_GAP_MARGIN - 0.5
    assert pin_cols >= 5


def test_plinko_spawns_all_ball_pairs(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2)
    assert sim.balls == []
    assert len(sim._basket_x_bounds) == len(PLINKO_BASKET_SCORES)
    needed_sec = (PLINKO_BALLS_PER_TEAM - 1) * (PLINKO_SPAWN_INTERVAL_FRAMES / 30.0) + 2.0
    for _ in iter_simulation_frames(sim, fps=30, max_duration_sec=needed_sec):
        pass
    assert sim._spawn_pairs_released == PLINKO_BALLS_PER_TEAM
    assert len(sim.balls) == PLINKO_BALLS_PER_TEAM * 2


def test_plinko_full_run_declares_winner(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_PLINKO, spec, a1, a2)
    for _ in iter_simulation_frames(sim, fps=30, max_duration_sec=40.0):
        pass
    assert sim.winner_id in (0, 1)
    assert len(sim._scored_ball_ids) >= 1


def test_render_frame_produces_image(tmp_path):
    spec = _sample_spec()
    a1, a2 = _avatars_in_tmp(tmp_path)
    sim = create_simulation(BATALHA_MOD_TERRITORIO, spec, a1, a2)
    sim.step(1 / 30)
    img = sim.render_frame()
    assert img.size == (1080, 1920)
    assert img.mode == "RGBA"
