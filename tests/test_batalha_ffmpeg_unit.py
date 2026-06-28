"""Testes unitários do FFmpeg da Batalha 1v1 (filter_complex, sem subprocess)."""

from app.pipelines.batalha.batalha_ffmpeg import (
    DEFAULT_HIT_SFX_ASSET,
    build_batalha_audio_filter_complex,
    build_collision_sfx_filter,
    resolve_hit_sfx_path,
)


def test_resolve_hit_sfx_prefers_ball_mp3():
    path = resolve_hit_sfx_path()
    assert path is not None
    assert path.name == DEFAULT_HIT_SFX_ASSET.name == "ball.mp3"
    assert path.is_file()


def test_build_collision_sfx_filter_empty():
    assert build_collision_sfx_filter([]) is None


def test_build_collision_sfx_filter_delays():
    fc, label = build_collision_sfx_filter([0.5, 1.2], hit_input_index=2)
    assert label == "[sfx]"
    assert "adelay=500|500" in fc
    assert "adelay=1200|1200" in fc
    assert "amix=inputs=2" in fc


def test_build_batalha_audio_filter_intro_only():
    fc, label = build_batalha_audio_filter_complex([])
    assert label == "[aout]"
    assert "[intro]" in fc
    assert "volume=" in fc


def test_build_batalha_audio_filter_with_hits():
    fc, label = build_batalha_audio_filter_complex([0.0, 2.5])
    assert label == "[aout]"
    assert "[sfx]" in fc
    assert "[intro]" in fc
    assert "amix=inputs=2" in fc


def test_build_batalha_audio_filter_with_mid_and_victory():
    fc, label = build_batalha_audio_filter_complex(
        [0.5],
        mid_narration_input_index=3,
        mid_narration_delay_sec=3.2,
        victory_input_index=4,
        victory_start_sec=12.5,
    )
    assert label == "[aout]"
    assert "adelay=3200|3200" in fc
    assert "adelay=12500|12500" in fc
    assert "[mid]" in fc
    assert "[victory]" in fc
