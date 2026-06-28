"""Testes unitários do quiz (sem Groq, Edge-TTS nem rede)."""

from pathlib import Path

from app.pipelines.quiz.quiz_frames import (
    normalize_quiz_bg_color,
    render_quiz_frame_pair,
    render_quiz_hook_frame,
    render_quiz_outro_frame,
)
import logging
from app.pipelines.quiz.quiz_pipeline import (
    QUIZ_DIFFICULTY_DIFICIL,
    QUIZ_DIFFICULTY_FACIL,
    QUIZ_DIFFICULTY_MEDIO,
    QUIZ_DIFFICULTY_VARIADO,
    QUIZ_OUTRO_TTS_TEXT,
    QuizQuestion,
    _build_per_question_filter_complex,
    _build_quiz_user_prompt,
    _difficulty_instruction,
    _fallback_quiz_opening,
    _format_log_for_gui,
    _format_pergunta_tts_text,
    _format_resposta_tts_text,
    _progress_header_overlay_filters,
    _resolve_ding_asset_path,
    _reveal_flash_drawbox_filters,
    _local_question_issues,
    _quiz_fact_accuracy_block,
    _quiz_reference_year,
    _sanitize_gancho_not_question,
    _timer_countdown_drawtext_filters,
    _validate_and_normalize_question,
    normalize_quiz_difficulty,
)


def test_timer_audio_filter_complex_amix_syntax():
    """Regressão: ';' antes de amix quebrava o FFmpeg (exit 8)."""
    segments = ["[0:a]atrim=0:0.120,adelay=0|0[tk0]"]
    labels = ["[tk0]", "[tk1]"]
    fc = ";".join(segments) + ";" + "".join(labels) + "amix=inputs=2[out]"
    assert "];amix" not in fc
    assert "[tk0][tk1]amix=inputs=2" in fc


def test_format_log_for_gui_error_prefix():
    block = _format_log_for_gui("falhou", logging.ERROR)
    assert "[ERRO]" in block
    assert "falhou" in block
    assert block.endswith("\n")


def test_format_pergunta_tts_only_question():
    q = QuizQuestion(
        pergunta="Qual planeta?",
        opcoes=["Terra", "Marte", "Júpiter", "Saturno"],
        resposta_correta=2,
        curiosidade_extra="Júpiter é enorme.",
    )
    text = _format_pergunta_tts_text(q)
    assert text == "Qual planeta?"
    assert "Alternativa" not in text


def test_format_resposta_tts_correct_answer_only():
    q = QuizQuestion(
        pergunta="Qual planeta?",
        opcoes=["Terra", "Marte", "Júpiter", "Saturno"],
        resposta_correta=2,
        curiosidade_extra="Júpiter é enorme.",
    )
    text = _format_resposta_tts_text(q)
    assert "A resposta correta é" in text
    assert "alternativa C" in text
    assert "Júpiter" in text
    assert "Júpiter é enorme" in text
    assert "Alternativa A" not in text
    assert "Alternativa D" not in text


def test_validate_question_truncates():
    raw = {
        "pergunta": "x" * 200,
        "opcoes": ["a" * 40, "b", "c", "d"],
        "resposta_correta": 0,
        "curiosidade_extra": "y" * 200,
    }
    q = _validate_and_normalize_question(raw, 1)
    assert len(q["pergunta"]) <= 120
    assert all(len(o) <= 35 for o in q["opcoes"])
    assert len(q["curiosidade_extra"]) <= 150


def test_timer_countdown_drawtext_shows_5_to_1():
    filters = _timer_countdown_drawtext_filters(5.0)
    assert "drawtext" in filters
    assert "text='5':enable='between(t,0.000,1.000)'" in filters
    assert "text='4':enable='between(t,1.000,2.000)'" in filters
    assert "text='1':enable='between(t,4.000,5.000)'" in filters


def test_progress_header_overlay_filters():
    fc = _progress_header_overlay_filters(2, 5)
    assert "Pergunta 2/5" in fc
    assert "drawbox" in fc
    assert "drawtext" in fc


def test_reveal_flash_drawbox_filters():
    fx = _reveal_flash_drawbox_filters()
    assert "white" in fx
    assert "enable=" in fx
    assert "crop" not in fx


def test_build_per_question_phase3_uses_overlay_not_crop_enable():
    fc, _, _ = _build_per_question_filter_complex(
        use_gpu_encoder=False,
        timer_duration_sec=5.0,
        include_ding_mix=False,
    )
    assert "overlay=" in fc
    # crop na fase 3 não aceita `enable` (FFmpeg 6.x) — só posição fixa 8:8
    assert "crop=iw-16:ih-16:8:8" in fc
    assert "crop=iw-16:ih-16:8:8,scale=" in fc


def test_normalize_quiz_difficulty():
    assert normalize_quiz_difficulty("Fácil") == QUIZ_DIFFICULTY_FACIL
    assert normalize_quiz_difficulty("Difícil") == QUIZ_DIFFICULTY_DIFICIL
    assert normalize_quiz_difficulty("Variado") == QUIZ_DIFFICULTY_VARIADO
    assert normalize_quiz_difficulty("") == QUIZ_DIFFICULTY_VARIADO


def test_sanitize_gancho_strips_question_style():
    assert "?" not in _sanitize_gancho_not_question("Qual é o maior planeta do sistema?")
    assert _sanitize_gancho_not_question("Só 1% acerta tudo!") == "Só 1% acerta tudo!"


def test_build_quiz_user_prompt_includes_difficulty():
    p = _build_quiz_user_prompt("Futebol", 5, difficulty=QUIZ_DIFFICULTY_DIFICIL)
    assert "DIFÍCIL" in p or "DIFICIL" in p
    assert "maior planeta" in p
    assert "near-miss" in p.lower() or "near-misses" in p
    assert "FACTUAL ACCURACY" in p
    assert str(_quiz_reference_year()) in p
    assert "MIXED" in _difficulty_instruction(QUIZ_DIFFICULTY_VARIADO, 6)


def test_normalize_quiz_difficulty_accented_gui_labels():
    assert normalize_quiz_difficulty("Difícil") == QUIZ_DIFFICULTY_DIFICIL
    assert normalize_quiz_difficulty("Fácil") == QUIZ_DIFFICULTY_FACIL
    assert normalize_quiz_difficulty("Médio") == QUIZ_DIFFICULTY_MEDIO


def test_local_question_issues_detects_duplicate_options():
    q = QuizQuestion(
        pergunta="Teste?",
        opcoes=["Igual", "Igual", "Outro", "Mais"],
        resposta_correta=0,
        curiosidade_extra="Fato.",
    )
    assert "duplicadas" in " ".join(_local_question_issues(q))


def test_quiz_fact_accuracy_block_mentions_year():
    block = _quiz_fact_accuracy_block(2026)
    assert "2026" in block
    assert "resposta_correta" in block


def test_fallback_quiz_opening():
    o = _fallback_quiz_opening("Futebol", 5)
    assert o["gancho_abertura"]
    assert "5 perguntas" in o["subtitulo"]
    assert "Futebol" in o["subtitulo"]


def test_build_per_question_filter_complex_concat_chain():
    from app.core.config import clip_gpu_uses_vaapi

    fc, vmap, comment = _build_per_question_filter_complex(
        use_gpu_encoder=False,
        timer_duration_sec=5.0,
        include_ding_mix=False,
        question_num=2,
        total_questions=5,
    )
    assert "concat=n=3:v=1:a=1" in fc
    assert "drawtext" in fc
    assert "drawbox" in fc
    assert "Pergunta 2/5" in fc
    assert "amix" not in fc
    assert "[v0][3:a][v1][4:a][v2][5:a]" in fc
    assert vmap == "[vcat]"
    assert "countdown" in comment.lower()
    assert "0x00E5FF:t=6" not in fc

    fc_ding, _, c_ding = _build_per_question_filter_complex(
        use_gpu_encoder=False,
        timer_duration_sec=5.0,
        include_ding_mix=True,
    )
    assert "amix" in fc_ding
    assert "[6:a]" in fc_ding
    assert "[v0][3:a][v1][4:a][v2][a2]" in fc_ding
    assert "ding" in c_ding

    fc_va, vmap_va, _ = _build_per_question_filter_complex(
        use_gpu_encoder=True,
        timer_duration_sec=5.0,
        include_ding_mix=False,
    )
    assert "concat=n=3:v=1:a=1" in fc_va
    if clip_gpu_uses_vaapi():
        assert "hwupload" in fc_va
        assert vmap_va == "[vout]"
    else:
        assert vmap_va == "[vcat]"


def test_render_quiz_hook_frame_writes_png(tmp_path: Path):
    path = render_quiz_hook_frame(
        tmp_path,
        gancho="Só 1% acerta tudo!",
        subtitulo="5 perguntas • Astronomia",
    )
    assert path.name == "frame_hook.png"
    assert path.is_file() and path.stat().st_size > 500


def test_resolve_ding_asset_path_missing(tmp_path: Path):
    assert _resolve_ding_asset_path(tmp_path / "nao_existe.mp3") is None


def test_quiz_outro_tts_text():
    assert "acertou" in QUIZ_OUTRO_TTS_TEXT.lower()
    assert "comenta" in QUIZ_OUTRO_TTS_TEXT.lower()


def test_render_quiz_outro_frame_writes_png(tmp_path: Path):
    path = render_quiz_outro_frame(tmp_path, message=QUIZ_OUTRO_TTS_TEXT)
    assert path.name == "frame_outro.png"
    assert path.is_file() and path.stat().st_size > 500


def test_normalize_quiz_bg_color():
    assert normalize_quiz_bg_color("#112233") == "#112233"
    assert normalize_quiz_bg_color("445566") == "#445566"
    assert normalize_quiz_bg_color("invalid") == "#1A1A1A"


def test_render_quiz_frame_pair_writes_png(tmp_path: Path):
    q = QuizQuestion(
        pergunta="Pergunta curta?",
        opcoes=["Um", "Dois", "Três", "Quatro"],
        resposta_correta=0,
        curiosidade_extra="Curiosidade.",
    )
    f1, f2 = render_quiz_frame_pair(q, 1, tmp_path)
    assert f1.is_file() and f1.stat().st_size > 1000
    assert f2.is_file() and f2.stat().st_size > 1000
