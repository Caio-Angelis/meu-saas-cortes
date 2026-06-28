"""
Orquestração da Máquina de Quizzes (projeto.md §13.3).

Backend isolado da UI: gera perguntas via Groq, áudio via Edge-TTS, frames via Pillow
e montagem via FFmpeg (Etapa 4: um MP4 por pergunta + concat demuxer).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
import unicodedata
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from threading import Event
from typing import Any, TypedDict

from app.ai_integrations.groq_chat import groq_user_message_text
from app.ai_integrations.tiktok_caption import (
    generate_tiktok_post_caption,
    save_tiktok_caption_file,
)
from app.core.cancel import raise_if_cancelled
from app.core.clip_output_naming import sanitize_clip_output_stem
from app.core.config import (
    EDGE_TTS_MAX_CONCURRENT,
    EDGE_TTS_RETRIES,
    EDGE_TTS_VOICE_PT,
    FFMPEG_PATH,
    OUTPUT_DIR,
    OUTPUT_VIDEO_HEIGHT,
    OUTPUT_VIDEO_WIDTH,
    TEMP_DIR,
    USE_GPU_CLIP_ENCODE,
    clip_ffmpeg_threads_args,
    clip_gpu_uses_vaapi,
    ffmpeg_vaapi_hwdevice_args,
    ffmpeg_vaapi_vf_hwupload_suffix,
    gpu_clip_encoder_ffmpeg_args,
)
from app.gui.gui_export import ffprobe_duration_seconds
from app.pipelines.quiz.quiz_frames import (
    _resolve_font_path,
    normalize_quiz_bg_color,
    render_quiz_frame_pair,
    render_quiz_hook_frame,
    render_quiz_outro_frame,
    render_quiz_reward_frame,
)
from app.core.subprocess_utils import run_cancelable
from app.tts.tts_engine import synthesize_speech_to_path
from app.video_processing.tts_dubber import edge_tts_save_to_path
from app.tts.tts_voices import resolve_voice
_log = logging.getLogger("quiz_pipeline")

# --- Limites de layout (projeto.md §13.3.2 / §13.3.7) ---
MAX_PERGUNTA_CHARS = 120
MAX_OPCAO_CHARS = 35
NUM_OPCOES = 4
MAX_CURIOSIDADE_CHARS = 150
MIN_RESPOSTA_INDEX = 0
MAX_RESPOSTA_INDEX = 3

DEFAULT_QUESTION_COUNT = 5
DEFAULT_TIMER_SEC = 5

# Assets estáticos (Etapa 2)
DEFAULT_TICKING_ASSET = Path("assets/ticking_5s.mp3")
DEFAULT_DING_SFX_ASSET = Path("assets/ding.mp3")

# Locução após a última pergunta (encerramento do vídeo)
QUIZ_OUTRO_TTS_TEXT = "E aí, foi bem? Comenta quantas você acertou."

# Fase 2 — contagem regressiva sobreposta no frame (drawtext, um dígito por segundo)
TIMER_COUNTDOWN_FONT_SIZE = 160
TIMER_TICK_SAMPLE_SEC = 0.12
# Barra de progresso no topo (“Pergunta 2/5”) — checklist quiz
PROGRESS_HEADER_FONT_SIZE = 38
PROGRESS_BAR_Y = 118
PROGRESS_BAR_H = 14
PROGRESS_BAR_X0 = 150
PROGRESS_BAR_WIDTH = OUTPUT_VIDEO_WIDTH - 300
# Fase 3 — ding mesclado no início da locução da resposta (filter_complex)
DING_MIX_MAX_SEC = 0.65
DING_MIX_VOLUME = 1.15
# Flash + shake na revelação (2–3 frames @ 30 fps ≈ 0,07–0,10 s)
REVEAL_FLASH_SEC = 0.09
REVEAL_SHAKE_SEC = 0.10
# Gancho antes da pergunta 1 (checklist §5)
HOOK_MIN_DURATION_SEC = 2.5
HOOK_MAX_DURATION_SEC = 3.0
MAX_GANCHO_CHARS = 80
MAX_HOOK_SUBTITLE_CHARS = 70

QUIZ_DIFFICULTY_FACIL = "facil"
QUIZ_DIFFICULTY_MEDIO = "medio"
QUIZ_DIFFICULTY_DIFICIL = "dificil"
QUIZ_DIFFICULTY_VARIADO = "variado"
QUIZ_DIFFICULTY_DEFAULT = QUIZ_DIFFICULTY_VARIADO
VALID_QUIZ_DIFFICULTIES = frozenset(
    {
        QUIZ_DIFFICULTY_FACIL,
        QUIZ_DIFFICULTY_MEDIO,
        QUIZ_DIFFICULTY_DIFICIL,
        QUIZ_DIFFICULTY_VARIADO,
    }
)
# Micro-recompensas entre perguntas (checklist §6)
REWARD_SEGMENT_SEC = 1.0
REWARD_SFX_TRIM_SEC = 0.35
REWARD_MESSAGES = ("Acertou? 🎉", "Errou? 😅")

QUIZ_LLM_MODEL = "llama-3.3-70b-versatile"
QUIZ_GENERATION_TEMPERATURE = 0.2
QUIZ_VERIFY_TEMPERATURE = 0.05
QUIZ_VERIFY_MAX_ATTEMPTS = 2


def _quiz_reference_year() -> int:
    """Ano de referência para fatos «atuais» nos prompts (ano civil local)."""
    return datetime.now().year


def _quiz_fact_accuracy_block(
    year: int | None = None,
    *,
    difficulty: str = QUIZ_DIFFICULTY_MEDIO,
) -> str:
    y = year or _quiz_reference_year()
    diff = normalize_quiz_difficulty(difficulty)
    if diff == QUIZ_DIFFICULTY_DIFICIL:
        wrong_rule = (
            "Wrong options: near-miss decoys in the same category/era (all plausible to a fan); "
            "never absurd jokes — difficulty comes from similarity, not silly distractors."
        )
        stable_rule = (
            "Use precise, verifiable niche facts; hard does NOT mean invented — cite real names, dates, stats."
        )
    elif diff == QUIZ_DIFFICULTY_FACIL:
        wrong_rule = (
            "Wrong options: clearly weaker than the correct one; a casual viewer should spot them."
        )
        stable_rule = (
            "Prefer well-known facts most adults in Brazil would recognize without specialist knowledge."
        )
    else:
        wrong_rule = (
            "Wrong options: plausible but only one is objectively true; avoid absurd distractors."
        )
        stable_rule = (
            "Mix accessible and thoughtful facts; for current events use confirmed "
            f"{y - 1}–{y} information only when confident."
        )

    return f"""
FACTUAL ACCURACY (mandatory — wrong or outdated answers ruin the video):
- Anchor all facts to {y} unless the question explicitly names a past year (e.g. "em 2018").
- Before setting "resposta_correta", verify the chosen option is objectively true; only ONE option may be true.
- Update stale facts: no outdated records presented as "today".
- "curiosidade_extra" must be true and consistent with the correct option.
- {wrong_rule}
- {stable_rule}
- Do NOT invent statistics or names; if unsure, choose a different question you can verify mentally.
"""


QUIZ_LLM_SYSTEM_PROMPT = """You are an expert fact-checked quiz writer for short-form vertical video (TikTok / Reels).
Your ONLY output must be a valid JSON array. No markdown, no code fences, no explanation before or after.

STRICT SCHEMA — each array element is one question object with EXACTLY these keys:
- "pergunta" (string): the question text, MAXIMUM 120 characters.
- "opcoes" (array of exactly 4 strings): answer choices labeled A–D in order; EACH option MAXIMUM 35 characters.
- "resposta_correta" (integer): index of the correct option, MUST be 0, 1, 2, or 3 (0 = first option).
- "curiosidade_extra" (string): short fun fact after the reveal, MAXIMUM 150 characters.

RULES:
- Return EXACTLY the number of questions requested in the user message.
- All text must be in Brazilian Portuguese (pt-BR) unless the user asks for another language.
- Questions must match the theme/niche given by the user.
- Options must be plausible; exactly one correct answer per question.
- Do not use line breaks inside string values.
- Do not exceed character limits; truncate mentally before writing if needed.
- No emojis unless essential and still within limits.

JSON shape example only (do NOT copy this difficulty — follow DIFFICULTY in user request):
[{"pergunta":"...", "opcoes":["...","...","...","..."], "resposta_correta":0, "curiosidade_extra":"..."}]
"""

QUIZ_VERIFY_SYSTEM_PROMPT = """You are a strict fact-checker and editor for Brazilian Portuguese quiz JSON.
You receive a JSON array of quiz questions already drafted. Your job is to CORRECT them, not to praise them.

For EACH item you MUST:
1. Confirm the option at index "resposta_correta" is factually correct for the reference year given in the user message.
2. If the marked index is wrong, fix "resposta_correta" OR rewrite options/question so exactly one option is true.
3. Replace outdated facts (old presidents, wrong "current" champions, obsolete company/product facts, pre-2020 records stated as present).
4. Ensure "curiosidade_extra" is true and aligns with the correct option text.
5. Keep exactly 4 unique options; char limits (pergunta 120, option 35, curiosidade 150); pt-BR; same theme.
6. If a question is beyond repair, replace the whole object with a new accurate question on the same theme AND the same difficulty level requested.

When user chose HARD/DIFÍCIL: do NOT simplify questions while fixing facts; keep obscure, expert-level wording and near-miss options.

Return ONLY the corrected JSON array with the SAME number of elements as the input. No markdown."""


class QuizQuestion(TypedDict):
    """Contrato de uma pergunta (projeto.md §13.3.7)."""

    pergunta: str
    opcoes: list[str]
    resposta_correta: int
    curiosidade_extra: str


class QuizOpening(TypedDict):
    """Gancho de abertura antes da pergunta 1 (cartão intro, não é pergunta)."""

    gancho_abertura: str
    subtitulo: str


class QuizJobPayload(TypedDict, total=False):
    """Payload da GUI / worker (projeto.md §13.1)."""

    job_type: str
    theme: str
    count: int
    timer_sec: int
    tts_voice: str
    difficulty: str


@dataclass(frozen=True)
class QuizAudioTrack:
    """Um dos três blocos de áudio por pergunta."""

    kind: str  # "pergunta" | "timer" | "resposta"
    path: Path
    duration_sec: float


@dataclass
class QuizQuestionAudioBundle:
    """Áudio completo de uma pergunta (Etapa 2)."""

    question_index: int
    pergunta: QuizAudioTrack
    timer: QuizAudioTrack
    resposta: QuizAudioTrack


@dataclass(frozen=True)
class QuizFramePair:
    """Dois PNG por pergunta (Etapa 3)."""

    frame_pergunta: Path
    frame_resposta: Path


@dataclass
class QuizPipelineResult:
    """Saída final do orquestrador."""

    video_path: Path
    caption_path: Path | None
    questions: list[QuizQuestion]
    run_id: str = field(default="")


def _check_cancel(cancel_event: Event | None) -> None:
    """Respeita cancelamento global (app.core.cancel) e evento opcional da GUI."""
    raise_if_cancelled()
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Operação cancelada pelo usuário.")


def _format_log_for_gui(message: str, level: int = logging.INFO) -> str:
    """Uma linha/bloco legível no painel de log da GUI (sempre termina com newline)."""
    text = (message or "").strip()
    if not text:
        return ""
    if level >= logging.ERROR:
        prefix = "[ERRO] "
    elif level >= logging.WARNING:
        prefix = "[!] "
    else:
        prefix = ""
    block = f"\n{prefix}{text}\n" if prefix else f"\n{text}\n"
    return block


def _emit_log(log_queue: Queue[Any] | None, message: str, level: int = logging.INFO) -> None:
    """Espelha mensagem no logger do app e, se houver, na fila da UI."""
    if level >= logging.ERROR:
        _log.error("%s", message)
    elif level >= logging.WARNING:
        _log.warning("%s", message)
    else:
        _log.info("%s", message)
    if log_queue is None:
        return
    try:
        formatted = _format_log_for_gui(message, level)
        if formatted:
            log_queue.put_nowait(formatted)
    except Exception:
        pass


def _extract_json_array(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Resposta do modelo não contém array JSON")
    return text[start : end + 1]


def _sanitize_json(raw: str) -> str:
    in_string = False
    escape_next = False
    chars: list[str] = []
    for char in raw:
        if escape_next:
            chars.append(char)
            escape_next = False
        elif char == "\\":
            chars.append(char)
            escape_next = True
        elif char == '"':
            chars.append(char)
            in_string = not in_string
        elif in_string and char in "\n\r\t":
            chars.append(" ")
        else:
            chars.append(char)
    return "".join(chars)


def _parse_questions_json(content: str) -> list[dict[str, Any]]:
    raw = _extract_json_array(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_sanitize_json(raw))
    if not isinstance(data, list):
        raise ValueError("JSON raiz deve ser um array")
    return data


def _truncate_field(value: str, max_len: int) -> str:
    s = re.sub(r"\s+", " ", (value or "").strip())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _validate_and_normalize_question(raw: dict[str, Any], index: int) -> QuizQuestion:
    """Rejeita ou repara entradas fora dos limites (projeto.md §13.3.2)."""
    if not isinstance(raw, dict):
        raise ValueError(f"Pergunta {index}: objeto inválido")

    pergunta = _truncate_field(str(raw.get("pergunta") or ""), MAX_PERGUNTA_CHARS)
    if not pergunta:
        raise ValueError(f"Pergunta {index}: 'pergunta' vazia")

    opcoes_raw = raw.get("opcoes")
    if not isinstance(opcoes_raw, list):
        raise ValueError(f"Pergunta {index}: 'opcoes' deve ser lista")
    opcoes = [_truncate_field(str(o), MAX_OPCAO_CHARS) for o in opcoes_raw[:NUM_OPCOES]]
    while len(opcoes) < NUM_OPCOES:
        opcoes.append(f"Opção {len(opcoes) + 1}"[:MAX_OPCAO_CHARS])
    if len(opcoes) != NUM_OPCOES:
        raise ValueError(f"Pergunta {index}: exatamente {NUM_OPCOES} opções")

    try:
        resposta = int(raw.get("resposta_correta"))
    except (TypeError, ValueError) as e:
        raise ValueError(f"Pergunta {index}: 'resposta_correta' inválida") from e
    if resposta < MIN_RESPOSTA_INDEX or resposta > MAX_RESPOSTA_INDEX:
        raise ValueError(
            f"Pergunta {index}: 'resposta_correta' deve estar entre "
            f"{MIN_RESPOSTA_INDEX} e {MAX_RESPOSTA_INDEX}"
        )

    curiosidade = _truncate_field(
        str(raw.get("curiosidade_extra") or ""), MAX_CURIOSIDADE_CHARS
    )
    if not curiosidade:
        curiosidade = "Fato curioso em breve."

    return QuizQuestion(
        pergunta=pergunta,
        opcoes=opcoes,
        resposta_correta=resposta,
        curiosidade_extra=curiosidade,
    )


def _local_question_issues(q: QuizQuestion) -> list[str]:
    """Checagens locais rápidas (não substituem revisão LLM)."""
    issues: list[str] = []
    normalized = [re.sub(r"\s+", " ", o.strip().lower()) for o in q["opcoes"]]
    if len(set(normalized)) < NUM_OPCOES:
        issues.append("opções duplicadas ou vazias")
    correct = q["opcoes"][q["resposta_correta"]].strip()
    if not correct:
        issues.append("texto da resposta correta vazio")
    if correct.lower() in ("opção 1", "opção 2", "opção 3", "opção 4"):
        issues.append("resposta correta é placeholder")
    return issues


def _questions_json_for_llm(questions: list[QuizQuestion]) -> str:
    return json.dumps(questions, ensure_ascii=False, indent=2)


def verify_quiz_questions_llm(
    questions: list[QuizQuestion],
    theme: str,
    *,
    difficulty: str = QUIZ_DIFFICULTY_DEFAULT,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> list[QuizQuestion]:
    """
    Segunda passagem Groq: corrige resposta errada, fatos desatualizados e inconsistências.
    """
    _check_cancel(cancel_event)
    n = len(questions)
    if n == 0:
        return questions

    year = _quiz_reference_year()
    theme_clean = (theme or "Quiz").strip()
    diff = normalize_quiz_difficulty(difficulty)
    input_json = _questions_json_for_llm(questions)

    prompt = (
        f"{QUIZ_VERIFY_SYSTEM_PROMPT}\n\n"
        f"--- USER REQUEST ---\n"
        f"Reference year for 'current' facts: {year}\n"
        f"Theme/niche: {theme_clean}\n"
        f"Number of questions to return: {n}\n"
        f"User-selected difficulty (preserve when fixing; do not make easier): {diff}\n"
        f"{_difficulty_instruction(diff, n)}\n"
        f"{_quiz_fact_accuracy_block(year, difficulty=diff)}\n\n"
        f"INPUT JSON ({n} questions):\n{input_json}\n\n"
        f"Return ONLY the corrected JSON array with exactly {n} objects."
    )

    last_err: Exception | None = None
    for attempt in range(1, QUIZ_VERIFY_MAX_ATTEMPTS + 1):
        _check_cancel(cancel_event)
        try:
            content = groq_user_message_text(
                prompt,
                temperature=QUIZ_VERIFY_TEMPERATURE,
                max_tokens=4096,
                none_as_empty=False,
                retry_label=f"quiz verificação (tentativa {attempt})",
                bad_request_runtime=lambda e: RuntimeError(
                    f"Groq recusou verificação do quiz: {e}"
                ),
                rate_limit_message=(
                    "Limite Groq na verificação factual do quiz. Aguarde e tente novamente."
                ),
                model=QUIZ_LLM_MODEL,
            )
            if not content or not str(content).strip():
                raise RuntimeError("resposta vazia na verificação")

            raw_list = _parse_questions_json(str(content))
            if len(raw_list) < n:
                raise ValueError(
                    f"verificação retornou {len(raw_list)} pergunta(s), esperado {n}"
                )

            verified: list[QuizQuestion] = []
            for i, item in enumerate(raw_list[:n], start=1):
                _check_cancel(cancel_event)
                verified.append(_validate_and_normalize_question(item, i))

            remaining_issues: list[str] = []
            for i, q in enumerate(verified, start=1):
                for msg in _local_question_issues(q):
                    remaining_issues.append(f"P{i}: {msg}")

            if remaining_issues:
                _emit_log(
                    log_queue,
                    f"[quiz 1/4] Após verificação ainda há avisos: {', '.join(remaining_issues)}",
                    logging.WARNING,
                )
            else:
                _emit_log(log_queue, "[quiz 1/4] Verificação factual concluída (sem avisos locais).")

            return verified
        except Exception as e:
            last_err = e
            _log.warning(
                "Quiz verificação tentativa %s/%s falhou: %s",
                attempt,
                QUIZ_VERIFY_MAX_ATTEMPTS,
                e,
            )

    _emit_log(
        log_queue,
        f"Verificação factual falhou ({last_err}); a usar perguntas da geração inicial.",
        logging.WARNING,
    )
    return questions


def _fold_accents(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return "".join(c for c in folded if not unicodedata.combining(c))


def normalize_quiz_difficulty(raw: str | None) -> str:
    """Normaliza rótulo da GUI (Fácil, medio, …) para chave interna."""
    key = _fold_accents(re.sub(r"\s+", " ", (raw or "").strip().lower()))
    aliases = {
        "facil": QUIZ_DIFFICULTY_FACIL,
        "fácil": QUIZ_DIFFICULTY_FACIL,
        "easy": QUIZ_DIFFICULTY_FACIL,
        "medio": QUIZ_DIFFICULTY_MEDIO,
        "médio": QUIZ_DIFFICULTY_MEDIO,
        "medium": QUIZ_DIFFICULTY_MEDIO,
        "dificil": QUIZ_DIFFICULTY_DIFICIL,
        "difícil": QUIZ_DIFFICULTY_DIFICIL,
        "hard": QUIZ_DIFFICULTY_DIFICIL,
        "variado": QUIZ_DIFFICULTY_VARIADO,
        "mixed": QUIZ_DIFFICULTY_VARIADO,
        "misto": QUIZ_DIFFICULTY_VARIADO,
    }
    return aliases.get(key, QUIZ_DIFFICULTY_DEFAULT)


def _difficulty_instruction(difficulty: str, count: int) -> str:
    n = max(1, int(count))
    if difficulty == QUIZ_DIFFICULTY_FACIL:
        return f"""
DIFFICULTY: EASY / FÁCIL (mandatory for all {n} questions):
- Mainstream trivia most Brazilian adults know without specialist study.
- Wrong options should be obviously weaker to a casual viewer.
- Avoid trick questions, obscure dates, or expert-only knowledge.
"""
    if difficulty == QUIZ_DIFFICULTY_DIFICIL:
        return f"""
DIFFICULTY: HARD / DIFÍCIL (mandatory for ALL {n} questions — user chose maximum challenge):
- Audience already knows beginner facts about the theme; do NOT ask clichés (e.g. "maior planeta", capital óbvia, cor da bandeira, nome mais famoso).
- Each question must need specialist, historical, statistical, or second-tier knowledge.
- All 4 options highly plausible near-misses (same category/era); only experts should be sure.
- Include precise details: dates, middle names, lesser-known records, technical terms, runners-up, not just #1 mainstream answers.
- A random viewer should likely pick wrong; if a teenager would get it right, replace the question.
- Forbidden: joke options, absurd distractors, or questions answerable by pure common sense.
- Every single one of the {n} questions must feel genuinely hard — no easy warm-up questions.
"""
    if difficulty == QUIZ_DIFFICULTY_VARIADO:
        hard_n = max(1, n - n // 3 - n // 2)
        return f"""
DIFFICULTY: MIXED / VARIADO (mandatory distribution for {n} questions):
- Include roughly {max(1, n // 3)} EASY, {max(1, n // 2)} MEDIUM, and {hard_n} HARD questions.
- Mark difficulty by content: hard = niche/near-miss options; easy = widely known facts.
- Do not make all questions medium; vary clearly within the set.
"""
    return f"""
DIFFICULTY: MEDIUM / MÉDIO (mandatory for all {n} questions):
- General knowledge adults may know with some thought; not expert-only.
- Wrong options plausible; one clearly best answer after reasoning.
- Harder than EASY but avoid obscure expert-only trivia.
"""


def _build_quiz_user_prompt(theme: str, count: int, *, difficulty: str) -> str:
    theme_clean = (theme or "Conhecimentos gerais").strip()
    n = max(1, min(20, int(count)))
    diff = normalize_quiz_difficulty(difficulty)
    year = _quiz_reference_year()
    diff_block = _difficulty_instruction(diff, n)
    return (
        f"{QUIZ_LLM_SYSTEM_PROMPT}\n"
        f"{_quiz_fact_accuracy_block(year, difficulty=diff)}\n\n"
        f"--- USER REQUEST ---\n"
        f"Theme/niche: {theme_clean}\n"
        f"Number of questions: {n}\n"
        f"Reference year for current facts: {year}\n"
        f"PRIORITY: follow this difficulty block over any generic quiz habit:\n"
        f"{diff_block}\n"
        f"Return ONLY the JSON array with exactly {n} question objects."
    )


def generate_quiz_questions_llm(
    theme: str,
    count: int,
    *,
    difficulty: str = QUIZ_DIFFICULTY_DEFAULT,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> list[QuizQuestion]:
    """
    Etapa 1 — Geração de dados via Groq (projeto.md §13.3.2).

    Chama `groq_user_message_text` com o system prompt e limites de caracteres;
    faz parse do JSON, validação e normalização.
    """
    _check_cancel(cancel_event)
    n = max(1, min(20, int(count)))
    diff = normalize_quiz_difficulty(difficulty)
    _emit_log(
        log_queue,
        f"[quiz 1/4] A gerar {n} pergunta(s) via Groq (tema: {theme!r}, dificuldade: {diff})…",
    )

    prompt = _build_quiz_user_prompt(theme, n, difficulty=diff)
    gen_temp = 0.35 if diff == QUIZ_DIFFICULTY_DIFICIL else QUIZ_GENERATION_TEMPERATURE

    content = groq_user_message_text(
        prompt,
        temperature=gen_temp,
        max_tokens=4096,
        none_as_empty=False,
        retry_label="quiz LLM",
        bad_request_runtime=lambda e: RuntimeError(f"Groq recusou a geração do quiz: {e}"),
        rate_limit_message=(
            "Limite de requisições Groq atingido ao gerar perguntas do quiz. "
            "Aguarde e tente novamente."
        ),
        model=QUIZ_LLM_MODEL,
    )
    _check_cancel(cancel_event)

    if not content or not str(content).strip():
        raise RuntimeError("Groq retornou resposta vazia para o quiz")

    raw_list = _parse_questions_json(str(content))
    if len(raw_list) != n:
        _log.warning(
            "Quiz: modelo retornou %s pergunta(s), esperado %s — a usar as primeiras válidas",
            len(raw_list),
            n,
        )

    questions: list[QuizQuestion] = []
    for i, item in enumerate(raw_list[:n], start=1):
        _check_cancel(cancel_event)
        questions.append(_validate_and_normalize_question(item, i))

    if len(questions) < n:
        raise RuntimeError(
            f"Quiz: apenas {len(questions)} pergunta(s) válida(s) de {n} solicitada(s)"
        )

    pre_issues: list[str] = []
    for i, q in enumerate(questions, start=1):
        for msg in _local_question_issues(q):
            pre_issues.append(f"P{i}: {msg}")
    if pre_issues:
        _emit_log(
            log_queue,
            f"[quiz 1/4] Rascunho com avisos locais: {', '.join(pre_issues)}",
            logging.WARNING,
        )

    _emit_log(log_queue, "[quiz 1/4] Verificação factual (Groq)…")
    questions = verify_quiz_questions_llm(
        questions,
        theme,
        difficulty=diff,
        cancel_event=cancel_event,
        log_queue=log_queue,
    )

    _emit_log(log_queue, f"[quiz 1/4] {len(questions)} pergunta(s) geradas e verificadas.")
    return questions


QUIZ_OPENING_SYSTEM_PROMPT = """You are a hook writer for viral TikTok quiz videos in Brazilian Portuguese.
Return ONLY a JSON object (no markdown). Keys:
- "gancho_abertura" (string, MAX 80 chars): short IMPACT statement or challenge — NOT a quiz question.
  Good: "Só 1% acerta tudo!", "90% erram a pergunta 2", "Se acertar 4, você é nerd demais".
  Bad: full questions with "?", or copying question text — the real Q1 comes AFTER this intro.
- "subtitulo" (string, MAX 70 chars): context line only, e.g. "5 perguntas • Futebol" or "Quiz difícil • História".

Rules: pt-BR, no line breaks inside strings, match the theme, gancho must NOT end with "?" unless rhetorical one-liner."""


def _parse_opening_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Resposta do modelo não contém objeto JSON de abertura")
    raw = text[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_sanitize_json(raw))
    if not isinstance(data, dict):
        raise ValueError("JSON de abertura deve ser um objeto")
    return data


def _fallback_quiz_opening(theme: str, count: int) -> QuizOpening:
    theme_clean = (theme or "Quiz").strip()
    n = max(1, int(count))
    return QuizOpening(
        gancho_abertura="90% erram a pergunta 2!",
        subtitulo=_truncate_field(f"{n} perguntas • {theme_clean}", MAX_HOOK_SUBTITLE_CHARS),
    )


def _sanitize_gancho_not_question(text: str) -> str:
    """Evita gancho que pareça pergunta do quiz (sem '?', sem 'qual/quem/o que')."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return "Só 1% acerta tudo!"
    if "?" in s and len(s) > 40:
        s = s.split("?")[0].strip() or s.replace("?", ".")
    lower = s.lower()
    if lower.startswith(("qual ", "quem ", "quando ", "onde ", "o que ", "quantos ")):
        s = f"Desafio: {s.rstrip('?')}…"
    return _truncate_field(s, MAX_GANCHO_CHARS)


def generate_quiz_opening_llm(
    theme: str,
    count: int,
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> QuizOpening:
    """Gancho de intro via Groq (2–3 s antes da P1; visual separado das perguntas)."""
    _check_cancel(cancel_event)
    _emit_log(log_queue, "[quiz 1/4] A gerar gancho de abertura via Groq…")
    theme_clean = (theme or "Quiz").strip()
    n = max(1, int(count))
    prompt = (
        f"{QUIZ_OPENING_SYSTEM_PROMPT}\n\n"
        f"Theme: {theme_clean}\n"
        f"Number of questions in video: {n}\n"
        "Do NOT include the text of any quiz question in gancho_abertura.\n"
        "Return ONLY the JSON object."
    )
    try:
        content = groq_user_message_text(
            prompt,
            temperature=0.55,
            max_tokens=512,
            none_as_empty=False,
            retry_label="quiz opening",
            bad_request_runtime=lambda e: RuntimeError(f"Groq recusou gancho do quiz: {e}"),
            rate_limit_message=(
                "Limite Groq ao gerar gancho do quiz. Aguarde e tente novamente."
            ),
            model=QUIZ_LLM_MODEL,
        )
        _check_cancel(cancel_event)
        if not content or not str(content).strip():
            raise ValueError("resposta vazia")
        raw = _parse_opening_json(str(content))
        gancho = _sanitize_gancho_not_question(str(raw.get("gancho_abertura") or ""))
        subtitulo = _truncate_field(
            str(raw.get("subtitulo") or raw.get("pergunta_teaser") or ""),
            MAX_HOOK_SUBTITLE_CHARS,
        )
        if not subtitulo:
            subtitulo = _fallback_quiz_opening(theme_clean, n)["subtitulo"]
        opening = QuizOpening(gancho_abertura=gancho, subtitulo=subtitulo)
    except Exception as e:
        _emit_log(
            log_queue,
            f"Gancho LLM falhou ({e}); a usar gancho padrão.",
            logging.WARNING,
        )
        opening = _fallback_quiz_opening(theme_clean, n)
    _emit_log(log_queue, f"[quiz 1/4] Gancho: {opening['gancho_abertura']!r}")
    return opening


OPTION_LABELS_TTS = ("A", "B", "C", "D")


def _format_pergunta_tts_text(q: QuizQuestion) -> str:
    """Texto para Áudio 1: só a pergunta (alternativas aparecem no vídeo)."""
    return (q["pergunta"] or "").strip()


def _format_resposta_tts_text(q: QuizQuestion) -> str:
    """Texto para Áudio 3: resposta correta + curiosidade (sem reler as 4 opções)."""
    idx = q["resposta_correta"]
    correct = q["opcoes"][idx]
    letter = OPTION_LABELS_TTS[idx]
    return (
        f"A resposta correta é a alternativa {letter}, {correct}. "
        f"{q['curiosidade_extra']}"
    )


def _quiz_drawtext_font_clause() -> str:
    """Cláusula `fontfile=` para drawtext no FFmpeg (Linux)."""
    path = _resolve_font_path()
    if path is None:
        return ""
    esc = (
        str(path.resolve())
        .replace("\\", "\\\\")
        .replace("'", r"\'")
        .replace(":", "\\:")
    )
    return f"fontfile='{esc}':"


def _probe_audio_duration_sec(path: Path, *, label: str) -> float:
    """Duração do áudio via ffprobe (`gui_export`); falha com erro claro se inválido."""
    if not path.is_file() or path.stat().st_size < 32:
        raise RuntimeError(f"Áudio {label} ausente ou vazio: {path}")
    dur = ffprobe_duration_seconds(str(path))
    if dur is None or dur <= 0.01:
        raise RuntimeError(
            f"Não foi possível obter duração de {label} ({path}). "
            "Verifique ffprobe e o arquivo gerado pelo TTS."
        )
    return float(dur)


async def _synthesize_quiz_tts_async(
    text: str,
    out_path: Path,
    voice: str,
    *,
    label: str,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    """
    TTS para o quiz. Edge usa `edge_tts_save_to_path` (timeout + retentativas já lá);
    Gemini mantém `synthesize_speech_to_path` — evita loop duplo de retry que podia
    parecer “travado” no Telegram por vários minutos.
    """
    _check_cancel(cancel_event)
    clean = (text or "").strip()
    if not clean:
        raise ValueError(f"Texto TTS vazio para {label}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)

    _emit_log(log_queue, f"[quiz 2/4] TTS {label}…")
    opt = resolve_voice(voice)
    try:
        if opt.provider in ("gemini", "local"):
            await synthesize_speech_to_path(clean, out_path, voice)
        else:
            await edge_tts_save_to_path(clean, out_path, opt.engine_voice)
    except Exception as e:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"TTS falhou em {label}: {e}") from e

    if not out_path.is_file() or out_path.stat().st_size < 32:
        out_path.unlink(missing_ok=True)
        size = out_path.stat().st_size if out_path.is_file() else 0
        raise RuntimeError(
            f"TTS gerou arquivo vazio em {label} ({size} B). Tente de novo em instantes."
        )
    return out_path


def _prepare_timer_audio(
    out_path: Path,
    timer_sec: float,
    ticking_asset: Path,
    *,
    cancel_event: Event | None = None,
) -> Path:
    """
    Áudio 2 — um tick por segundo (0s, 1s, …) durante a fase de espera.

    Usa o início do asset de ticking, se existir; senão um beep curto (sine).
    """
    _check_cancel(cancel_event)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, float(timer_sec))
    n_ticks = max(1, int(round(duration)))
    tick_dur = TIMER_TICK_SAMPLE_SEC

    if ticking_asset.is_file():
        input_args = ["-i", str(ticking_asset.resolve())]
        tick_src = "[0:a]"
    else:
        _emit_log(
            None,
            f"Asset de timer não encontrado ({ticking_asset}); ticks sintéticos.",
            logging.WARNING,
        )
        input_args = [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:sample_rate=44100:duration={tick_dur:.3f}",
        ]
        tick_src = "[0:a]"

    segments: list[str] = []
    labels: list[str] = []
    for i in range(n_ticks):
        delay_ms = i * 1000
        lab = f"tk{i}"
        segments.append(
            f"{tick_src}atrim=0:{tick_dur:.3f},asetpts=PTS-STARTPTS,"
            f"adelay={delay_ms}|{delay_ms}[{lab}]"
        )
        labels.append(f"[{lab}]")

    # Entradas do amix: [tk0][tk1]… sem ';' antes de amix (senão FFmpeg exit 8).
    fc = (
        ";".join(segments)
        + ";"
        + "".join(labels)
        + f"amix=inputs={n_ticks}:duration=longest:dropout_transition=0:normalize=0,"
        f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[out]"
    )
    cmd = [
        FFMPEG_PATH,
        "-y",
        *input_args,
        "-filter_complex",
        fc,
        "-map",
        "[out]",
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        str(out_path),
    ]

    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(
            f"FFmpeg falhou ao preparar áudio do timer: {detail or e}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"FFmpeg falhou ao preparar áudio do timer: {e}") from e

    return out_path


async def _generate_one_question_audio_async(
    question: QuizQuestion,
    question_index: int,
    *,
    work_dir: Path,
    voice: str,
    timer_sec: float,
    ticking_asset: Path,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> QuizQuestionAudioBundle:
    """
    Gera os três blocos de áudio de uma pergunta (Etapa 2).

    TTS na pergunta (curto) e na revelação; timer (ticks/s) via FFmpeg.
    """
    _check_cancel(cancel_event)
    work_dir.mkdir(parents=True, exist_ok=True)
    idx = question_index

    pergunta_path = work_dir / f"audio_pergunta_{idx}.mp3"
    resposta_path = work_dir / f"audio_resposta_{idx}.mp3"
    timer_path = work_dir / f"audio_timer_{idx}.mp3"

    pergunta_text = _format_pergunta_tts_text(question)
    resposta_text = _format_resposta_tts_text(question)

    # Sequencial: menos chamadas simultâneas ao Edge-TTS (evita 403 e MP3 vazio).
    await _synthesize_quiz_tts_async(
        pergunta_text,
        pergunta_path,
        voice,
        label=f"pergunta {idx}",
        cancel_event=cancel_event,
        log_queue=log_queue,
    )
    await _synthesize_quiz_tts_async(
        resposta_text,
        resposta_path,
        voice,
        label=f"resposta {idx}",
        cancel_event=cancel_event,
        log_queue=log_queue,
    )

    _check_cancel(cancel_event)
    _prepare_timer_audio(
        timer_path,
        timer_sec,
        ticking_asset,
        cancel_event=cancel_event,
    )

    pergunta_dur = _probe_audio_duration_sec(pergunta_path, label=f"pergunta {idx}")
    timer_dur = _probe_audio_duration_sec(timer_path, label=f"timer {idx}")
    resposta_dur = _probe_audio_duration_sec(resposta_path, label=f"resposta {idx}")

    return QuizQuestionAudioBundle(
        question_index=question_index,
        pergunta=QuizAudioTrack(kind="pergunta", path=pergunta_path, duration_sec=pergunta_dur),
        timer=QuizAudioTrack(kind="timer", path=timer_path, duration_sec=timer_dur),
        resposta=QuizAudioTrack(kind="resposta", path=resposta_path, duration_sec=resposta_dur),
    )


async def generate_quiz_audio_async(
    questions: list[QuizQuestion],
    *,
    work_dir: Path,
    voice: str,
    timer_sec: float = DEFAULT_TIMER_SEC,
    ticking_asset: Path | None = None,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> tuple[list[QuizQuestionAudioBundle], Path]:
    """
    Etapa 2 — Áudio e timestamps (projeto.md §13.3.3).

    Por pergunta: TTS pergunta → TTS resposta → timer FFmpeg (sequencial).
    Até `EDGE_TTS_MAX_CONCURRENT` perguntas em paralelo; encerramento TTS no fim.
    """
    _check_cancel(cancel_event)
    asset = ticking_asset or DEFAULT_TICKING_ASSET
    n = len(questions)
    _emit_log(log_queue, f"[quiz 2/4] A gerar áudio para {n} pergunta(s) (timer {timer_sec}s)…")

    max_concurrent = max(1, EDGE_TTS_MAX_CONCURRENT)
    sem = asyncio.Semaphore(max_concurrent)

    async def _bounded(i: int, q: QuizQuestion) -> QuizQuestionAudioBundle:
        async with sem:
            if max_concurrent > 1 and i > 1:
                await asyncio.sleep(0.35 * (i - 1))
            try:
                bundle = await _generate_one_question_audio_async(
                    q,
                    i,
                    work_dir=work_dir,
                    voice=voice,
                    timer_sec=timer_sec,
                    ticking_asset=asset,
                    cancel_event=cancel_event,
                    log_queue=log_queue,
                )
                _emit_log(
                    log_queue,
                    f"[quiz 2/4] Áudio pergunta {i}/{n} pronto.",
                )
                return bundle
            except Exception as e:
                _emit_log(
                    log_queue,
                    f"[quiz 2/4] Falha no áudio da pergunta {i}: {e}",
                    logging.ERROR,
                )
                raise

    bundles = await asyncio.gather(
        *[_bounded(i, q) for i, q in enumerate(questions, start=1)]
    )
    _emit_log(log_queue, "[quiz 2/4] A gerar áudio de encerramento…")
    outro_path = await _generate_outro_audio_async(
        work_dir=work_dir,
        voice=voice,
        cancel_event=cancel_event,
    )
    _emit_log(
        log_queue,
        f"[quiz 2/4] Áudio gerado ({len(bundles)} pergunta(s) + encerramento).",
    )
    return list(bundles), outro_path


def generate_quiz_frames(
    questions: list[QuizQuestion],
    *,
    work_dir: Path,
    bg_color: str | None = None,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> list[QuizFramePair]:
    """
    Etapa 3 — Geração visual com Pillow (projeto.md §13.3.4).

    Delega o desenho a `app.pipelines.quiz.quiz_frames.render_quiz_frame_pair`.
    """
    _check_cancel(cancel_event)
    n = len(questions)
    bg = normalize_quiz_bg_color(bg_color or "")
    _emit_log(log_queue, f"[quiz 3/4] A gerar {n * 2} frame(s) PNG (Pillow)…")

    pairs: list[QuizFramePair] = []
    for i, q in enumerate(questions, start=1):
        _check_cancel(cancel_event)
        try:
            f1, f2 = render_quiz_frame_pair(
                q, i, work_dir, total_questions=n, bg_color=bg
            )
        except Exception as e:
            _emit_log(
                log_queue,
                f"[quiz 3/4] Falha ao renderizar frames da pergunta {i}: {e}",
                logging.ERROR,
            )
            raise
        pairs.append(QuizFramePair(frame_pergunta=f1, frame_resposta=f2))
        _log.debug("Frames pergunta %s: %s | %s", i, f1.name, f2.name)

    _emit_log(log_queue, f"[quiz 3/4] {len(pairs)} par(es) de frames gerados.")
    return pairs


def _quiz_use_gpu_encoder() -> bool:
    return USE_GPU_CLIP_ENCODE


def _quiz_frame_scale_vf() -> str:
    """Escala PNG 9:16 para a resolução de saída e fixa 30 fps."""
    return (
        f"scale={OUTPUT_VIDEO_WIDTH}:{OUTPUT_VIDEO_HEIGHT}:flags=lanczos,"
        "setsar=1,fps=30"
    )


def _resolve_ding_asset_path(ding_sfx: Path | None) -> Path | None:
    """Retorna caminho absoluto do ding se existir; senão None (pipeline segue sem SFX)."""
    candidate = ding_sfx or DEFAULT_DING_SFX_ASSET
    if candidate.is_file():
        return candidate.resolve()
    repo_root = Path(__file__).resolve().parent.parent
    alt = (repo_root / candidate).resolve()
    if alt.is_file():
        return alt
    return None


def _progress_header_overlay_filters(question_num: int, total_questions: int) -> str:
    """Barra + texto «Pergunta N/Total» no topo (todas as fases da pergunta)."""
    total = max(1, int(total_questions))
    num = max(1, min(int(question_num), total))
    font_clause = _quiz_drawtext_font_clause()
    label = f"Pergunta {num}/{total}"
    pct = num / total
    fill_w = max(8, int(PROGRESS_BAR_WIDTH * pct))
    bar_bg = (
        f"drawbox=x={PROGRESS_BAR_X0}:y={PROGRESS_BAR_Y}:"
        f"w={PROGRESS_BAR_WIDTH}:h={PROGRESS_BAR_H}:color=black@0.55:t=fill"
    )
    bar_fill = (
        f"drawbox=x={PROGRESS_BAR_X0}:y={PROGRESS_BAR_Y}:"
        f"w={fill_w}:h={PROGRESS_BAR_H}:color=0x00E5FF@0.95:t=fill"
    )
    label_dt = (
        f"drawtext={font_clause}fontsize={PROGRESS_HEADER_FONT_SIZE}:"
        f"fontcolor=white:borderw=3:bordercolor=black@0.5:"
        f"x={PROGRESS_BAR_X0}:y={PROGRESS_BAR_Y - 52}:text='{label}'"
    )
    return f"{bar_bg},{bar_fill},{label_dt}"


def _reveal_flash_drawbox_filters() -> str:
    """Flash branco nos primeiros frames da revelação (ding). Cadeia vírgula-segura."""
    return (
        f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.5:t=fill:"
        f"enable='lt(t,{REVEAL_FLASH_SEC:.3f})'"
    )


def _reveal_phase3_filter_graph(scale_vf: str, header: str) -> str:
    """
    Fase 3: escala → shake (overlay, crop sem `enable`) → flash → header → [v2].

    O filtro `crop` não aceita `enable` no FFmpeg 6.x — shake via split/overlay.
    """
    flash = _reveal_flash_drawbox_filters()
    w, h = OUTPUT_VIDEO_WIDTH, OUTPUT_VIDEO_HEIGHT
    return (
        f"[2:v]{scale_vf}[v2sc];"
        f"[v2sc]split[v2m][v2s];"
        f"[v2s]crop=iw-16:ih-16:8:8,scale={w}:{h}[v2crop];"
        f"[v2m][v2crop]overlay=x='8+8*sin(55*PI*t)':y='8+8*cos(55*PI*t)':"
        f"enable='lt(t,{REVEAL_SHAKE_SEC:.3f})'[v2sh];"
        f"[v2sh]{flash},{header}[v2]"
    )


def _timer_countdown_drawtext_filters(timer_duration_sec: float) -> str:
    """
    Contagem regressiva central: N, N-1, …, 1 (um número por segundo de espera).

    Com timer de 5 s: exibe 5 em t∈[0,1), 4 em [1,2), …, 1 em [4,5).
    """
    t_total = max(0.5, float(timer_duration_sec))
    steps = max(1, int(round(t_total)))
    font_clause = _quiz_drawtext_font_clause()
    base = (
        f"drawtext={font_clause}fontsize={TIMER_COUNTDOWN_FONT_SIZE}:"
        "fontcolor=white:borderw=5:bordercolor=black@0.65:"
        "x=(w-text_w)/2:y=(h-text_h)/2"
    )
    parts: list[str] = []
    for i in range(steps):
        number = steps - i
        t0 = float(i)
        t1 = float(i + 1) if i < steps - 1 else t_total
        parts.append(f"{base}:text='{number}':enable='between(t,{t0:.3f},{t1:.3f})'")
    return ",".join(parts)


def _build_per_question_filter_complex(
    *,
    use_gpu_encoder: bool,
    timer_duration_sec: float,
    include_ding_mix: bool = False,
    question_num: int = 1,
    total_questions: int = 1,
) -> tuple[str, str, str]:
    """
    Monta `-filter_complex` para uma pergunta (3 segmentos vídeo+áudio → 1 clipe).

    Entradas FFmpeg (índices):
      0 — `frame_1` loop, fase 1 (pergunta + opções), duração = áudio pergunta
      1 — `frame_1` loop, fase 2 (timer), duração = áudio timer
      2 — `frame_2` loop, fase 3 (revelação), duração = áudio resposta
      3 — `audio_pergunta.mp3`
      4 — `audio_timer.mp3`
      5 — `audio_resposta.mp3` (TTS: “A resposta correta é…”)
      6 — `assets/ding.mp3` (opcional; `include_ding_mix=True`)

    Cadeia de vídeo:
      - Fase 1 e 3: escala 1080×1920 @ 30 fps → [v0], [v2].
      - Fase 2: escala + `drawtext` (contagem regressiva 5→1 por segundo) → [v1].

    Cadeia de áudio (fase 3):
      - Sem ding: [5:a] → `concat`.
      - Com ding: `amix` de [5:a] + trecho de [6:a] (`duration=first`).

    Concatenação:
      `[v0][3:a][v1][4:a][v2][a?]concat=n=3:v=1:a=1[vcat][acat]` — ordem temporal das 3 fases.

    Pós-concat (VA-API):
      `[vcat]format=nv12,hwupload=…[vout]` quando `clip_gpu_uses_vaapi()` e encode GPU ativo.

    Retorna (filter_complex, rótulo do map de vídeo, comentário legível para logs).
    """
    scale = _quiz_frame_scale_vf()
    countdown = _timer_countdown_drawtext_filters(timer_duration_sec)
    header = _progress_header_overlay_filters(question_num, total_questions)
    va_tail = ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu_encoder)

    phase2_extra = countdown

    phase3 = _reveal_phase3_filter_graph(scale, header)

    if include_ding_mix:
        audio_phase3 = (
            f"[6:a]atrim=0:{DING_MIX_MAX_SEC:.3f},volume={DING_MIX_VOLUME:.2f}[ding];"
            f"[5:a][ding]amix=inputs=2:duration=first:dropout_transition=0[a2]"
        )
        concat_audio = "[a2]"
        ding_note = " + amix ding"
    else:
        audio_phase3 = ""
        concat_audio = "[5:a]"
        ding_note = ""

    fc = (
        f"[0:v]{scale},{header}[v0];"
        f"[1:v]{scale},{phase2_extra},{header}[v1];"
        f"{phase3};"
    )
    if audio_phase3:
        fc += f"{audio_phase3};"
    fc += f"[v0][3:a][v1][4:a][v2]{concat_audio}concat=n=3:v=1:a=1[vcat][acat]"

    if va_tail:
        fc += f";[vcat]{va_tail.lstrip(',')}[vout]"
        vmap = "[vout]"
        hw_note = " + hwupload VA-API"
    else:
        vmap = "[vcat]"
        hw_note = ""

    comment = (
        "scale×3; header+countdown; reveal flash/shake; "
        f"concat n=3 (pergunta|timer|resposta){ding_note}{hw_note}"
    )
    return fc, vmap, comment


def _ffmpeg_run_with_gpu_fallback(
    cmd_gpu: list[str],
    cmd_cpu: list[str],
    *,
    label: str,
    cancel_event: Event | None = None,
) -> None:
    _check_cancel(cancel_event)
    use_gpu = _quiz_use_gpu_encoder()
    try:
        run_cancelable(cmd_gpu if use_gpu else cmd_cpu, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        if not use_gpu:
            detail = (e.stderr or e.stdout or "").strip()
            raise RuntimeError(f"FFmpeg falhou em {label}: {detail or e}") from e
        _emit_log(
            None,
            f"[quiz 4/4] Encode GPU falhou em {label}; a tentar CPU…",
            logging.WARNING,
        )
        try:
            run_cancelable(cmd_cpu, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e2:
            detail = (e2.stderr or e2.stdout or "").strip()
            raise RuntimeError(
                f"FFmpeg falhou em {label} (fallback CPU): {detail or e2}"
            ) from e2


def _render_one_question_mp4(
    frame_pair: QuizFramePair,
    audio: QuizQuestionAudioBundle,
    out_path: Path,
    *,
    total_questions: int = 1,
    ding_sfx: Path | None = None,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    """Gera `question_{idx}.mp4` com filter_complex concat (3 fases) e encode GPU/CPU."""
    _check_cancel(cancel_event)
    idx = audio.question_index
    d_pergunta = audio.pergunta.duration_sec
    d_timer = audio.timer.duration_sec
    d_resposta = audio.resposta.duration_sec
    ding_path = _resolve_ding_asset_path(ding_sfx)
    use_ding = ding_path is not None

    use_gpu = _quiz_use_gpu_encoder()
    _, _, fc_comment = _build_per_question_filter_complex(
        use_gpu_encoder=use_gpu,
        timer_duration_sec=d_timer,
        include_ding_mix=use_ding,
        question_num=idx,
        total_questions=total_questions,
    )
    ding_note = f", ding={ding_path.name}" if use_ding else ""
    _emit_log(
        log_queue,
        f"[quiz 4/4] Pergunta {idx}: filter_complex — {fc_comment}{ding_note} "
        f"(d={d_pergunta:.2f}+{d_timer:.2f}+{d_resposta:.2f}s)",
    )

    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    audio_enc = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100"]

    def _inputs_tail() -> list[str]:
        tail: list[str] = [
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            f"{d_pergunta:.3f}",
            "-i",
            str(frame_pair.frame_pergunta.resolve()),
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            f"{d_timer:.3f}",
            "-i",
            str(frame_pair.frame_pergunta.resolve()),
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            f"{d_resposta:.3f}",
            "-i",
            str(frame_pair.frame_resposta.resolve()),
            "-t",
            f"{d_pergunta:.3f}",
            "-i",
            str(audio.pergunta.path.resolve()),
            "-t",
            f"{d_timer:.3f}",
            "-i",
            str(audio.timer.path.resolve()),
            "-t",
            f"{d_resposta:.3f}",
            "-i",
            str(audio.resposta.path.resolve()),
        ]
        if use_ding and ding_path is not None:
            tail.extend(["-i", str(ding_path)])
        return tail

    def _base_cmd(*, gpu: bool) -> list[str]:
        fc, vmap, _ = _build_per_question_filter_complex(
            use_gpu_encoder=gpu,
            timer_duration_sec=d_timer,
            include_ding_mix=use_ding,
            question_num=idx,
            total_questions=total_questions,
        )
        va = ffmpeg_vaapi_hwdevice_args() if (gpu and clip_gpu_uses_vaapi()) else []
        enc = gpu_clip_encoder_ffmpeg_args() if gpu else cpu_venc
        return [
            FFMPEG_PATH,
            "-y",
            *va,
            *clip_ffmpeg_threads_args(use_gpu_encoder=gpu),
            *_inputs_tail(),
            "-filter_complex",
            fc,
            "-map",
            vmap,
            "-map",
            "[acat]",
            *enc,
            *audio_enc,
            str(out_path),
        ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd_gpu = _base_cmd(gpu=True)
    cmd_cpu = _base_cmd(gpu=False)
    _ffmpeg_run_with_gpu_fallback(
        cmd_gpu,
        cmd_cpu,
        label=f"pergunta {idx}",
        cancel_event=cancel_event,
    )
    if not out_path.is_file() or out_path.stat().st_size < 256:
        raise RuntimeError(f"FFmpeg não gerou MP4 válido: {out_path}")
    return out_path


def _prepare_reward_sfx(out_path: Path, ding_path: Path, *, cancel_event: Event | None = None) -> Path:
    """Trecho curto do ding para micro-recompensa entre perguntas."""
    _check_cancel(cancel_event)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i",
        str(ding_path.resolve()),
        "-t",
        f"{REWARD_SFX_TRIM_SEC:.3f}",
        "-af",
        "volume=1.2",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        str(out_path),
    ]
    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(f"FFmpeg falhou no SFX de recompensa: {detail or e}") from e
    return out_path


def _render_static_image_mp4(
    frame_path: Path,
    audio_path: Path | None,
    out_path: Path,
    duration_sec: float,
    *,
    label: str,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    """Segmento curto: um PNG em loop + áudio opcional (gancho / micro-recompensa)."""
    _check_cancel(cancel_event)
    duration = max(0.5, float(duration_sec))
    scale = _quiz_frame_scale_vf()
    use_gpu = _quiz_use_gpu_encoder()
    va_tail = ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu)
    if va_tail:
        fc = f"[0:v]{scale}{va_tail}[vout]"
        vmap = "[vout]"
    else:
        fc = f"[0:v]{scale}[vout]"
        vmap = "[vout]"

    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    audio_enc = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100"]

    def _base_cmd(*, gpu: bool) -> list[str]:
        va = ffmpeg_vaapi_hwdevice_args() if (gpu and clip_gpu_uses_vaapi()) else []
        enc = gpu_clip_encoder_ffmpeg_args() if gpu else cpu_venc
        cmd: list[str] = [
            FFMPEG_PATH,
            "-y",
            *va,
            *clip_ffmpeg_threads_args(use_gpu_encoder=gpu),
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(frame_path.resolve()),
        ]
        if audio_path is not None and audio_path.is_file():
            cmd.extend(["-i", str(audio_path.resolve()), "-filter_complex", fc, "-map", vmap, "-map", "1:a"])
        else:
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r=44100:cl=stereo:d={duration:.3f}",
                    "-filter_complex",
                    fc,
                    "-map",
                    vmap,
                    "-map",
                    "1:a",
                ]
            )
        cmd.extend([*enc, *audio_enc, str(out_path)])
        return cmd

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _emit_log(log_queue, f"[quiz 4/4] {label}: {duration:.2f}s")
    _ffmpeg_run_with_gpu_fallback(
        _base_cmd(gpu=True),
        _base_cmd(gpu=False),
        label=label,
        cancel_event=cancel_event,
    )
    if not out_path.is_file() or out_path.stat().st_size < 256:
        raise RuntimeError(f"FFmpeg não gerou segmento {label}: {out_path}")
    return out_path


async def _generate_hook_audio_async(
    opening: QuizOpening,
    work_dir: Path,
    voice: str,
    *,
    cancel_event: Event | None = None,
) -> tuple[Path, float]:
    """TTS só do gancho (pergunta teaser fica no vídeo antes do TTS da P1)."""
    path = work_dir / "audio_hook.mp3"
    text = (opening["gancho_abertura"] or "").strip()
    await _synthesize_quiz_tts_async(
        text,
        path,
        voice,
        label="gancho abertura",
        cancel_event=cancel_event,
    )
    dur = _probe_audio_duration_sec(path, label="gancho")
    target = min(HOOK_MAX_DURATION_SEC, max(HOOK_MIN_DURATION_SEC, dur))
    return path, target


def _render_quiz_hook_mp4(
    frame_path: Path,
    audio_path: Path,
    duration_sec: float,
    out_path: Path,
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    return _render_static_image_mp4(
        frame_path,
        audio_path,
        out_path,
        duration_sec,
        label="Gancho de abertura",
        cancel_event=cancel_event,
        log_queue=log_queue,
    )


def _render_micro_reward_mp4(
    frame_path: Path,
    sfx_path: Path | None,
    out_path: Path,
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    audio = sfx_path if sfx_path is not None and sfx_path.is_file() else None
    return _render_static_image_mp4(
        frame_path,
        audio,
        out_path,
        REWARD_SEGMENT_SEC,
        label="Micro-recompensa",
        cancel_event=cancel_event,
        log_queue=log_queue,
    )


async def _generate_outro_audio_async(
    *,
    work_dir: Path,
    voice: str,
    cancel_event: Event | None = None,
) -> Path:
    """TTS do encerramento após todas as perguntas."""
    out_path = work_dir / "audio_outro.mp3"
    await _synthesize_quiz_tts_async(
        QUIZ_OUTRO_TTS_TEXT,
        out_path,
        voice,
        label="encerramento",
        cancel_event=cancel_event,
    )
    return out_path


def _render_quiz_outro_mp4(
    frame_path: Path,
    audio_path: Path,
    out_path: Path,
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    """Um segmento 9:16: frame estático + locução de encerramento."""
    _check_cancel(cancel_event)
    duration = _probe_audio_duration_sec(audio_path, label="encerramento")
    scale = _quiz_frame_scale_vf()
    use_gpu = _quiz_use_gpu_encoder()
    va_tail = ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu)
    if va_tail:
        fc = f"[0:v]{scale}{va_tail}[vout]"
        vmap = "[vout]"
    else:
        fc = f"[0:v]{scale}[vout]"
        vmap = "[vout]"

    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    audio_enc = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100"]

    def _base_cmd(*, gpu: bool) -> list[str]:
        va = ffmpeg_vaapi_hwdevice_args() if (gpu and clip_gpu_uses_vaapi()) else []
        enc = gpu_clip_encoder_ffmpeg_args() if gpu else cpu_venc
        return [
            FFMPEG_PATH,
            "-y",
            *va,
            *clip_ffmpeg_threads_args(use_gpu_encoder=gpu),
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(frame_path.resolve()),
            "-i",
            str(audio_path.resolve()),
            "-filter_complex",
            fc,
            "-map",
            vmap,
            "-map",
            "1:a",
            *enc,
            *audio_enc,
            str(out_path),
        ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _emit_log(
        log_queue,
        f"[quiz 4/4] Encerramento: {duration:.2f}s — “{QUIZ_OUTRO_TTS_TEXT}”",
    )
    _ffmpeg_run_with_gpu_fallback(
        _base_cmd(gpu=True),
        _base_cmd(gpu=False),
        label="encerramento",
        cancel_event=cancel_event,
    )
    if not out_path.is_file() or out_path.stat().st_size < 256:
        raise RuntimeError(f"FFmpeg não gerou MP4 de encerramento: {out_path}")
    return out_path


def _concat_question_segments(
    segment_paths: list[Path],
    output_path: Path,
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    """Concatena `question_*.mp4` com demuxer (`-c copy`) — passe rápido sem reencode."""
    _check_cancel(cancel_event)
    if not segment_paths:
        raise ValueError("Nenhum segmento de pergunta para concatenar")

    work_dir = segment_paths[0].parent
    concat_list = work_dir / "concat.txt"
    lines = ["ffconcat version 1.0"]
    for p in segment_paths:
        escaped = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output_path),
    ]
    _emit_log(
        log_queue,
        f"[quiz 4/4] Concatenação final ({len(segment_paths)} segmento(s), -c copy)…",
    )
    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(f"FFmpeg falhou na concatenação final: {detail or e}") from e
    finally:
        try:
            concat_list.unlink(missing_ok=True)
        except OSError:
            pass

    if not output_path.is_file() or output_path.stat().st_size < 1024:
        raise RuntimeError(f"MP4 final inválido após concat: {output_path}")
    return output_path


def _cleanup_question_intermediates(segment_paths: list[Path]) -> None:
    for p in segment_paths:
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            _log.warning("Não foi possível remover intermediário %s: %s", p, e)


def assemble_quiz_video_ffmpeg(
    questions: list[QuizQuestion],
    frames: list[QuizFramePair],
    audio_bundles: list[QuizQuestionAudioBundle],
    *,
    output_path: Path,
    timer_sec: float,
    outro_audio_path: Path,
    work_dir: Path | None = None,
    bg_color: str | None = None,
    ding_sfx: Path | None = None,
    hook_frame_path: Path | None = None,
    hook_audio_path: Path | None = None,
    hook_duration_sec: float | None = None,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    """
    Etapa 4 — Montagem FFmpeg (projeto.md §13.3.5).

    Por pergunta: loop PNG + áudios → `question_{idx}.mp4` via `filter_complex` concat.
    Depois: demuxer `concat.txt` com `-c copy` para o MP4 final 9:16.
    Encode com VA-API quando configurado; fallback CPU como nos clipes virais.

    Retorno: caminho do MP4 final.
    """
    _check_cancel(cancel_event)
    _emit_log(log_queue, "[quiz 4/4] Montagem FFmpeg…")
    bg = normalize_quiz_bg_color(bg_color or "")

    if len(questions) != len(frames) or len(questions) != len(audio_bundles):
        raise ValueError(
            "questions, frames e audio_bundles devem ter o mesmo tamanho "
            f"({len(questions)}, {len(frames)}, {len(audio_bundles)})"
        )

    temp = work_dir or (TEMP_DIR / "quiz_ffmpeg")
    temp.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ding_resolved = _resolve_ding_asset_path(ding_sfx)
    if ding_resolved is None:
        _emit_log(
            log_queue,
            f"SFX ding não encontrado ({ding_sfx or DEFAULT_DING_SFX_ASSET}); "
            "montagem sem efeito na revelação.",
            logging.WARNING,
        )
    else:
        _emit_log(log_queue, f"[quiz 4/4] SFX ding: {ding_resolved.name}")

    _log.debug(
        "Quiz FFmpeg: %s perguntas, timer=%ss, ding=%s, out=%s, temp=%s",
        len(questions),
        timer_sec,
        ding_resolved,
        output_path,
        temp,
    )

    reward_sfx: Path | None = None
    if ding_resolved is not None:
        reward_sfx = temp / "reward_sfx.mp3"
        try:
            _prepare_reward_sfx(reward_sfx, ding_resolved, cancel_event=cancel_event)
        except Exception as e:
            _emit_log(
                log_queue,
                f"SFX micro-recompensa indisponível ({e}); segmentos sem som.",
                logging.WARNING,
            )
            reward_sfx = None

    segment_paths: list[Path] = []
    total_q = len(questions)

    if (
        hook_frame_path is not None
        and hook_frame_path.is_file()
        and hook_audio_path is not None
        and hook_audio_path.is_file()
        and hook_duration_sec is not None
    ):
        _check_cancel(cancel_event)
        hook_seg = temp / "hook.mp4"
        _render_quiz_hook_mp4(
            hook_frame_path,
            hook_audio_path,
            hook_duration_sec,
            hook_seg,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )
        segment_paths.append(hook_seg)

    for q_idx, (frame_pair, audio) in enumerate(zip(frames, audio_bundles, strict=True)):
        _check_cancel(cancel_event)
        seg = temp / f"question_{audio.question_index}.mp4"
        _render_one_question_mp4(
            frame_pair,
            audio,
            seg,
            total_questions=total_q,
            ding_sfx=ding_sfx,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )
        segment_paths.append(seg)

        if q_idx < total_q - 1:
            reward_msg = REWARD_MESSAGES[q_idx % len(REWARD_MESSAGES)]
            reward_frame = render_quiz_reward_frame(
                temp, message=reward_msg, index=q_idx + 1, bg_color=bg
            )
            reward_seg = temp / f"reward_{q_idx + 1}.mp4"
            _render_micro_reward_mp4(
                reward_frame,
                reward_sfx,
                reward_seg,
                cancel_event=cancel_event,
                log_queue=log_queue,
            )
            segment_paths.append(reward_seg)

    _check_cancel(cancel_event)
    outro_frame = render_quiz_outro_frame(
        temp, message=QUIZ_OUTRO_TTS_TEXT, bg_color=bg
    )
    outro_seg = temp / "outro.mp4"
    _render_quiz_outro_mp4(
        outro_frame,
        outro_audio_path,
        outro_seg,
        cancel_event=cancel_event,
        log_queue=log_queue,
    )
    segment_paths.append(outro_seg)

    _concat_question_segments(
        segment_paths,
        output_path,
        cancel_event=cancel_event,
        log_queue=log_queue,
    )
    _cleanup_question_intermediates(segment_paths)

    _emit_log(log_queue, f"[quiz 4/4] Vídeo final: {output_path}")
    return output_path


def _build_quiz_caption_context(theme: str, questions: list[QuizQuestion]) -> str:
    """Texto de contexto para a LLM de legenda TikTok (tema + perguntas)."""
    lines = [f"Quiz interativo sobre: {theme.strip()}", ""]
    for i, q in enumerate(questions, start=1):
        lines.append(f"Pergunta {i}: {q['pergunta']}")
    lines.append("")
    lines.append("O vídeo é um quiz de múltipla escolha com timer e revelação da resposta.")
    return "\n".join(lines)


def _save_quiz_tiktok_caption(
    video_path: Path,
    theme: str,
    questions: list[QuizQuestion],
    *,
    log_queue: Queue[Any] | None = None,
) -> Path | None:
    """Legenda de postagem via Groq + `save_tiktok_caption_file` (projeto.md §13.3.5)."""
    _emit_log(log_queue, "[quiz] A gerar legenda TikTok…")
    context = _build_quiz_caption_context(theme, questions)
    try:
        caption = generate_tiktok_post_caption(context, language="pt")
    except Exception as e:
        _emit_log(
            log_queue,
            f"Legenda TikTok falhou ({e}); a usar resumo mínimo.",
            logging.WARNING,
        )
        caption = f"Quiz: {theme.strip()}\n#quiz #fyp #fy"
    out = Path(save_tiktok_caption_file(str(video_path), caption))
    _emit_log(log_queue, f"Legenda TikTok: {out}")
    return out


def _resolve_output_paths(theme: str, run_id: str) -> tuple[Path, Path]:
    stem = sanitize_clip_output_stem(f"quiz_{theme}_{run_id}")
    video_path = OUTPUT_DIR / f"{stem}.mp4"
    return video_path, TEMP_DIR / f"quiz_{run_id}"


def _normalize_payload(
    payload: dict[str, Any],
) -> tuple[str, int, float, str, str, str]:
    theme = str(payload.get("theme") or payload.get("tema") or "Quiz").strip()
    count = int(payload.get("count") or payload.get("quantidade") or DEFAULT_QUESTION_COUNT)
    timer_sec = float(payload.get("timer_sec") or payload.get("timer") or DEFAULT_TIMER_SEC)
    voice = str(
        payload.get("tts_voice") or payload.get("voice") or EDGE_TTS_VOICE_PT
    ).strip()
    difficulty = normalize_quiz_difficulty(
        str(payload.get("difficulty") or payload.get("dificuldade") or "")
    )
    bg_color = normalize_quiz_bg_color(
        str(
            payload.get("cor_fundo")
            or payload.get("bg_color")
            or payload.get("quiz_bg_color")
            or ""
        )
    )
    return theme, count, timer_sec, voice, difficulty, bg_color


def run_quiz_pipeline(
    payload: dict[str, Any],
    log_queue: Queue[Any] | None = None,
    cancel_event: Event | None = None,
) -> QuizPipelineResult:
    """
    Orquestrador principal da Máquina de Quizzes.

    Recebe o job da GUI, por exemplo::

        {"job_type": "quiz", "theme": "Futebol", "count": 5,
         "timer_sec": 5, "tts_voice": "pt-BR-AntonioNeural"}

    Executa as quatro etapas em sequência e retorna caminhos de saída.
    """
    run_id = time.strftime("%Y%m%d_%H%M%S")
    theme, count, timer_sec, voice, difficulty, bg_color = _normalize_payload(payload)

    _emit_log(
        log_queue,
        f"Início quiz — tema={theme!r}, perguntas={count}, timer={timer_sec}s, "
        f"dificuldade={difficulty}, fundo={bg_color}",
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    video_path, work_dir = _resolve_output_paths(theme, run_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # --- Etapa 1: LLM ---
        questions = generate_quiz_questions_llm(
            theme,
            count,
            difficulty=difficulty,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )
        opening = generate_quiz_opening_llm(
            theme,
            count,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )

        # --- Etapa 2: Áudio (async) ---
        async def _all_audio() -> tuple[
            list[QuizQuestionAudioBundle], Path, Path, float
        ]:
            bundles, outro = await generate_quiz_audio_async(
                questions,
                work_dir=work_dir,
                voice=voice,
                timer_sec=timer_sec,
                cancel_event=cancel_event,
                log_queue=log_queue,
            )
            hook_path, hook_dur = await _generate_hook_audio_async(
                opening,
                work_dir,
                voice,
                cancel_event=cancel_event,
            )
            return bundles, outro, hook_path, hook_dur

        audio_bundles, outro_audio_path, hook_audio_path, hook_duration_sec = (
            asyncio.run(_all_audio())
        )

        # --- Etapa 3: Imagens ---
        hook_frame_path = render_quiz_hook_frame(
            work_dir,
            gancho=opening["gancho_abertura"],
            subtitulo=opening["subtitulo"],
            bg_color=bg_color,
        )
        frames = generate_quiz_frames(
            questions,
            work_dir=work_dir,
            bg_color=bg_color,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )

        # --- Etapa 4: FFmpeg ---
        assemble_quiz_video_ffmpeg(
            questions,
            frames,
            audio_bundles,
            output_path=video_path,
            timer_sec=timer_sec,
            outro_audio_path=outro_audio_path,
            work_dir=work_dir,
            bg_color=bg_color,
            hook_frame_path=hook_frame_path,
            hook_audio_path=hook_audio_path,
            hook_duration_sec=hook_duration_sec,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )

        caption_path = _save_quiz_tiktok_caption(
            video_path, theme, questions, log_queue=log_queue
        )

        _emit_log(log_queue, f"Quiz concluído: {video_path}")
        return QuizPipelineResult(
            video_path=video_path,
            caption_path=caption_path,
            questions=questions,
            run_id=run_id,
        )
    except Exception as e:
        _log.exception("Falha no pipeline de quiz (run_id=%s)", run_id)
        _emit_log(
            log_queue,
            f"Quiz interrompido: {type(e).__name__}: {e}",
            logging.ERROR,
        )
        raise
