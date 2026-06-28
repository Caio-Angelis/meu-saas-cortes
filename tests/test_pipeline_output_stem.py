"""Nomes de saída em resultados/ (índice + stem do vídeo)."""

from app.core.clip_output_naming import sanitize_clip_output_stem


def test_sanitize_clip_output_stem_trims_and_replaces() -> None:
    assert sanitize_clip_output_stem("  Meu Vídeo  ") == "Meu Vídeo"
    assert sanitize_clip_output_stem("a/b:c") == "a_b_c"


def test_sanitize_clip_output_stem_empty_fallback() -> None:
    assert sanitize_clip_output_stem("") == "video"
    assert sanitize_clip_output_stem("///") == "video"


def test_sanitize_clip_output_stem_max_len() -> None:
    long = "x" * 200
    out = sanitize_clip_output_stem(long, max_len=10)
    assert len(out) == 10
