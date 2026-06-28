"""Testes unitários da Batalha 1v1 (sem Groq, rede nem Pymunk)."""

import json

from PIL import Image

from app.pipelines.batalha.batalha_images import (
    apply_circular_mask,
    cleanup_batalha_downloaded_assets,
    collect_logo_image_urls,
    ensure_logo_search_term,
    hex_to_rgb,
    make_initial_fallback_avatar,
    normalize_hex_color,
)
from app.pipelines.batalha.batalha_pipeline import (
    SCRIPT_NARRACAO_MAX_WORDS,
    SCRIPT_NARRACAO_MIN_WORDS,
    _parse_batalha_json,
    _validate_and_normalize_spec,
    _word_count_pt,
    normalize_batalha_modo,
    split_theme_opponents,
)


def test_normalize_hex_color_short_and_long():
    assert normalize_hex_color("#abc") == "#AABBCC"
    assert normalize_hex_color("E74C3C") == "#E74C3C"
    assert normalize_hex_color("", default="#111111") == "#111111"


def test_hex_to_rgb():
    assert hex_to_rgb("#FF0000") == (255, 0, 0)


def test_apply_circular_mask_alpha_corners():
    img = Image.new("RGB", (200, 100), color=(255, 0, 0))
    out = apply_circular_mask(img, size=64)
    assert out.size == (64, 64)
    assert out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0


def test_initial_fallback_avatar():
    av = make_initial_fallback_avatar("Interstellar", "#3498DB", size=48)
    assert av.size == (48, 48)
    assert av.getpixel((24, 24))[3] > 0


def test_parse_batalha_json_with_fences():
    raw = """```json
{"oponente_1": "A", "oponente_2": "B", "termo_busca_1": "a logo",
 "termo_busca_2": "b logo", "cor_1": "#111111", "cor_2": "#222222",
 "hook": "Duelo!", "legenda_tiktok": "Legenda #fyp"}
```"""
    data = _parse_batalha_json(raw)
    spec = _validate_and_normalize_spec(data, "filmes")
    assert spec["oponente_1"] == "A"
    assert spec["cor_1"] == "#111111"
    assert "Duelo" in spec["hook"]


def test_validate_spec_distinct_opponents_and_colors():
    data = {
        "oponente_1": "X",
        "oponente_2": "x",
        "termo_busca_1": "x",
        "termo_busca_2": "y",
        "cor_1": "#FF0000",
        "cor_2": "#FF0000",
        "hook": "",
        "legenda_tiktok": "",
    }
    spec = _validate_and_normalize_spec(data, "tema")
    assert spec["oponente_2"].lower() != spec["oponente_1"].lower() or " B" in spec["oponente_2"]
    assert spec["cor_1"] != spec["cor_2"]


def test_split_theme_opponents():
    assert split_theme_opponents("Batman vs Superman") == ("Batman", "Superman")
    assert split_theme_opponents("Naruto x Sasuke") == ("Naruto", "Sasuke")
    assert split_theme_opponents("filmes de ação") is None


def test_ensure_logo_search_term():
    assert ensure_logo_search_term("Batman", "") == "Batman logo"
    assert "logo" in ensure_logo_search_term("X", "X official logo").lower()
    assert "poster" in ensure_logo_search_term("Y", "Y movie poster").lower()


def test_collect_logo_image_urls_batman_superman():
    batman = collect_logo_image_urls("Batman", "Batman logo", max_urls=3)
    superman = collect_logo_image_urls("Superman", "Superman logo", max_urls=3)
    assert batman
    assert superman
    assert any("wikimedia" in u for u in batman)
    assert any("logo" in u.casefold() or "shield" in u.casefold() for u in superman)


def test_cleanup_batalha_downloaded_assets(tmp_path):
    a1 = tmp_path / "avatar_1.png"
    a2 = tmp_path / "avatar_2.png"
    a1.write_bytes(b"x")
    a2.write_bytes(b"y")
    cleanup_batalha_downloaded_assets(tmp_path)
    assert not a1.exists()
    assert not a2.exists()


def test_validate_spec_theme_vs_forces_logo_search():
    data = {
        "oponente_1": "Batman",
        "oponente_2": "Superman",
        "termo_busca_1": "ignored",
        "termo_busca_2": "ignored",
        "cor_1": "#111111",
        "cor_2": "#222222",
        "hook": "Duelo!",
        "legenda_tiktok": "#fyp",
    }
    spec = _validate_and_normalize_spec(data, "Batman vs Superman")
    assert spec["termo_busca_1"].lower() == "batman logo"
    assert spec["termo_busca_2"].lower() == "superman logo"


def test_normalize_batalha_modo_aliases():
    assert normalize_batalha_modo("agar") == "tamanho"
    assert normalize_batalha_modo("corrida") == "plinko"
    assert normalize_batalha_modo("invalid") == "tamanho"


def test_parse_batalha_json_roundtrip():
    payload = {
        "oponente_1": "Interstellar",
        "oponente_2": "Oppenheimer",
        "termo_busca_1": "Interstellar movie poster",
        "termo_busca_2": "Oppenheimer movie poster",
        "cor_1": "#1A1A2E",
        "cor_2": "#C0392B",
        "hook": "Só um sobrevive!",
        "script_narracao": " ".join(["palavra"] * 55),
        "legenda_tiktok": "Quem ganha? #cinema #fyp",
    }
    parsed = _parse_batalha_json(json.dumps(payload))
    spec = _validate_and_normalize_spec(parsed, "filmes")
    assert spec["oponente_1"] == "Interstellar"
    assert "#" in spec["cor_2"]
    wc = _word_count_pt(spec["script_narracao"])
    assert SCRIPT_NARRACAO_MIN_WORDS <= wc <= SCRIPT_NARRACAO_MAX_WORDS


def test_validate_spec_script_narracao_fallback_and_trim():
    long_script = " ".join(["curiosidade"] * 80)
    data = {
        "oponente_1": "A",
        "oponente_2": "B",
        "termo_busca_1": "a logo",
        "termo_busca_2": "b logo",
        "cor_1": "#111111",
        "cor_2": "#222222",
        "hook": "Duelo!",
        "script_narracao": long_script,
        "legenda_tiktok": "#fyp",
    }
    spec = _validate_and_normalize_spec(data, "tema")
    assert _word_count_pt(spec["script_narracao"]) <= SCRIPT_NARRACAO_MAX_WORDS

    data2 = {**data, "script_narracao": ""}
    spec2 = _validate_and_normalize_spec(data2, "tema")
    assert _word_count_pt(spec2["script_narracao"]) >= SCRIPT_NARRACAO_MIN_WORDS
