"""
Pipeline Batalha 1v1 — vídeos virais com simulação de física 2D.

Groq → imagens → TTS (gancho) → Pymunk/PIL → FFmpeg (stdin + SFX).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Event
from typing import Any, Callable, TypedDict

from app.ai_integrations.groq_chat import groq_user_message_text
from app.pipelines.batalha.batalha_images import (
    cleanup_batalha_downloaded_assets,
    ensure_logo_search_term,
    fetch_opponent_graphics,
    normalize_hex_color,
    save_avatar_png,
)
from app.core.cancel import raise_if_cancelled
from app.core.clip_output_naming import sanitize_clip_output_stem
from app.core.config import EDGE_TTS_VOICE_PT, OUTPUT_DIR, TEMP_DIR

_log = logging.getLogger("batalha_pipeline")

# Resolução alvo do vídeo (9:16) — usada nas fases de física/FFmpeg
BATALHA_VIDEO_WIDTH = 1080
BATALHA_VIDEO_HEIGHT = 1920

BATALHA_MOD_TAMANHO = "tamanho"
BATALHA_MOD_TERRITORIO = "territorio"
BATALHA_MOD_PLINKO = "plinko"
BATALHA_MODOS = (
    BATALHA_MOD_TAMANHO,
    BATALHA_MOD_TERRITORIO,
    BATALHA_MOD_PLINKO,
)
BATALHA_MODO_DEFAULT = BATALHA_MOD_TAMANHO

BATALHA_LLM_MODEL = "llama-3.3-70b-versatile"
BATALHA_LLM_TEMPERATURE = 0.45

MAX_OPPONENT_NAME_CHARS = 40
MAX_SEARCH_TERM_CHARS = 80
MAX_HOOK_CHARS = 120
MAX_LEGENDA_CHARS = 220
MAX_SCRIPT_NARRACAO_CHARS = 520
SCRIPT_NARRACAO_MIN_WORDS = 50
SCRIPT_NARRACAO_MAX_WORDS = 60


class BatalhaSpec(TypedDict):
    oponente_1: str
    oponente_2: str
    termo_busca_1: str
    termo_busca_2: str
    cor_1: str
    cor_2: str
    hook: str
    script_narracao: str
    legenda_tiktok: str


@dataclass(frozen=True)
class BatalhaAssets:
    """Artefatos da Fase 1 prontos para simulação (Fase 2+)."""

    spec: BatalhaSpec
    avatar_1_path: Path
    avatar_2_path: Path
    logo_1_path: Path
    logo_2_path: Path
    work_dir: Path


@dataclass(frozen=True)
class BatalhaPipelineResult:
    video_path: Path
    caption_path: Path | None
    work_dir: Path


BATALHA_LLM_SYSTEM_PROMPT = """You are a creative director for viral TikTok/Shorts "1v1 battle" videos in Brazilian Portuguese.
Return ONLY one JSON object (no markdown, no code fences) with exactly these keys:
- "oponente_1": short display name for contender 1 (max 40 chars)
- "oponente_2": short display name for contender 2 (max 40 chars)
- "termo_busca_1": English image search for contender 1 — prefer "{name} logo" (official logo/mark)
- "termo_busca_2": English image search for contender 2 — prefer "{name} logo"
- "cor_1": hex accent color #RRGGBB for contender 1 (must contrast with cor_2)
- "cor_2": hex accent color #RRGGBB for contender 2
- "hook": opening narration in pt-BR (max 120 chars), dramatic setup for the physics battle; do NOT reveal the winner
- "script_narracao": pt-BR voice-over during the physics simulation — quick, engaging curiosities comparing BOTH opponents (facts, contrasts, trivia). MUST be exactly 50 to 60 words (count words carefully) so TTS lasts ~25–35 seconds. Do NOT reveal the winner. One continuous paragraph, no lists.
- "legenda_tiktok": TikTok post caption in pt-BR (max 200 chars) ending with 3–5 relevant hashtags

Rules:
- Both opponents must clearly relate to the user theme.
- Search terms must be recognizable (brand/movie/character names + "logo" or "poster" when helpful).
- hook, script_narracao and legenda_tiktok in pt-BR; search terms in English.
- script_narracao MUST stay between 50 and 60 words — this length is mandatory for audio sync.
- No line breaks inside string values."""


def split_theme_opponents(theme: str) -> tuple[str, str] | None:
    """Extrai «Batman vs Superman» → («Batman», «Superman»)."""
    parts = re.split(r"\s+(?:vs\.?|versus|x|×)\s+", (theme or "").strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    a, b = (parts[0].strip(), parts[1].strip())
    if not a or not b:
        return None
    return a, b


def normalize_batalha_modo(modo: str | None) -> str:
    raw = (modo or BATALHA_MODO_DEFAULT).strip().lower()
    aliases = {
        "agar": BATALHA_MOD_TAMANHO,
        "size": BATALHA_MOD_TAMANHO,
        "territory": BATALHA_MOD_TERRITORIO,
        "territorio": BATALHA_MOD_TERRITORIO,
        "race": BATALHA_MOD_PLINKO,
        "corrida": BATALHA_MOD_PLINKO,
    }
    return aliases.get(raw, raw if raw in BATALHA_MODOS else BATALHA_MODO_DEFAULT)


def _truncate_field(value: str, max_len: int) -> str:
    s = re.sub(r"\s+", " ", (value or "").strip())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _word_count_pt(text: str) -> int:
    return len(re.findall(r"\w+", text or "", flags=re.UNICODE))


def _truncate_to_word_count(text: str, max_words: int) -> str:
    words = re.findall(r"\S+", (text or "").strip())
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def _normalize_script_narracao(
    raw: str,
    *,
    oponente_1: str,
    oponente_2: str,
    theme: str,
) -> str:
    """Garante script de narração do meio (~50–60 palavras) para cobrir a simulação."""
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        theme_bit = f" no tema {theme.strip()}" if (theme or "").strip() else ""
        s = (
            f"Enquanto a física decide o duelo{theme_bit}, vale lembrar curiosidades sobre "
            f"{oponente_1} e {oponente_2}. "
            f"{oponente_1} conquistou fãs com estilo e impacto cultural marcante, "
            f"enquanto {oponente_2} brilha por outra identidade forte e memorável. "
            f"Os dois têm trunfos diferentes: um aposta em emoção e escala, o outro em "
            f"personalidade e presença. Agora é a hora de ver quem leva a melhor nesta arena."
        )
    wc = _word_count_pt(s)
    if wc > SCRIPT_NARRACAO_MAX_WORDS:
        s = _truncate_to_word_count(s, SCRIPT_NARRACAO_MAX_WORDS)
    elif wc < SCRIPT_NARRACAO_MIN_WORDS:
        pad = (
            f" A disputa segue intensa: {oponente_1} e {oponente_2} dividem torcedores "
            f"por estética, história e legado, e cada detalhe pode inclinar o resultado final."
        )
        s = _truncate_field(s + pad, MAX_SCRIPT_NARRACAO_CHARS)
        s = _truncate_to_word_count(s, SCRIPT_NARRACAO_MAX_WORDS)
    return _truncate_field(s, MAX_SCRIPT_NARRACAO_CHARS)


def _sanitize_json_object(raw: str) -> str:
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


def _extract_json_object(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, count=1).rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Resposta do modelo não contém objeto JSON")
    return text[start : end + 1]


def _parse_batalha_json(content: str) -> dict[str, Any]:
    raw = _extract_json_object(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_sanitize_json_object(raw))
    if not isinstance(data, dict):
        raise ValueError("JSON da batalha deve ser um objeto")
    return data


def _validate_and_normalize_spec(data: dict[str, Any], theme: str) -> BatalhaSpec:
    o1 = _truncate_field(str(data.get("oponente_1") or ""), MAX_OPPONENT_NAME_CHARS)
    o2 = _truncate_field(str(data.get("oponente_2") or ""), MAX_OPPONENT_NAME_CHARS)
    if not o1 or not o2:
        raise ValueError("oponente_1 e oponente_2 são obrigatórios")
    if o1.lower() == o2.lower():
        o2 = _truncate_field(f"{o2} B", MAX_OPPONENT_NAME_CHARS)

    theme_ops = split_theme_opponents(theme)
    if theme_ops:
        ta, tb = theme_ops
        t1 = _truncate_field(ensure_logo_search_term(ta, ""), MAX_SEARCH_TERM_CHARS)
        t2 = _truncate_field(ensure_logo_search_term(tb, ""), MAX_SEARCH_TERM_CHARS)
    else:
        raw_t1 = str(data.get("termo_busca_1") or o1)
        raw_t2 = str(data.get("termo_busca_2") or o2)
        t1 = _truncate_field(ensure_logo_search_term(o1, raw_t1), MAX_SEARCH_TERM_CHARS)
        t2 = _truncate_field(ensure_logo_search_term(o2, raw_t2), MAX_SEARCH_TERM_CHARS)
    c1 = normalize_hex_color(str(data.get("cor_1") or ""), default="#E74C3C")
    c2 = normalize_hex_color(str(data.get("cor_2") or ""), default="#3498DB")
    if c1 == c2:
        c2 = "#2ECC71" if c1 != "#2ECC71" else "#9B59B6"

    hook = _truncate_field(str(data.get("hook") or ""), MAX_HOOK_CHARS)
    if not hook:
        theme_clean = (theme or "Batalha").strip()
        hook = _truncate_field(
            f"Quem vence: {o1} ou {o2}? A física decide agora!",
            MAX_HOOK_CHARS,
        )
        if theme_clean and theme_clean.lower() not in hook.lower():
            hook = _truncate_field(f"{theme_clean}: {hook}", MAX_HOOK_CHARS)

    script_narracao = _normalize_script_narracao(
        str(data.get("script_narracao") or data.get("narracao") or ""),
        oponente_1=o1,
        oponente_2=o2,
        theme=theme,
    )

    legenda = _truncate_field(
        str(data.get("legenda_tiktok") or data.get("legenda") or ""),
        MAX_LEGENDA_CHARS,
    )
    if not legenda:
        legenda = _truncate_field(
            f"{o1} vs {o2} — quem você torce? #batalha #1v1 #fyp",
            MAX_LEGENDA_CHARS,
        )

    return BatalhaSpec(
        oponente_1=o1,
        oponente_2=o2,
        termo_busca_1=t1,
        termo_busca_2=t2,
        cor_1=c1,
        cor_2=c2,
        hook=hook,
        script_narracao=script_narracao,
        legenda_tiktok=legenda,
    )


def _emit_log(log_queue: Queue[Any] | None, message: str, level: int = logging.INFO) -> None:
    if level >= logging.ERROR:
        _log.error("%s", message)
    elif level >= logging.WARNING:
        _log.warning("%s", message)
    else:
        _log.info("%s", message)
    if log_queue is None:
        return
    try:
        prefix = "[ERRO] " if level >= logging.ERROR else ("[!] " if level >= logging.WARNING else "")
        text = (message or "").strip()
        if text:
            log_queue.put_nowait(f"\n{prefix}{text}\n")
    except Exception:
        pass


def generate_batalha_spec_llm(
    theme: str,
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> BatalhaSpec:
    """
    Etapa 1 — Groq: oponentes, termos de busca, cores, hook e legenda TikTok.
    """
    raise_if_cancelled(cancel_event)
    theme_clean = (theme or "Batalha").strip()
    _emit_log(log_queue, f"[batalha 1/5] A gerar duelo via Groq (tema: {theme_clean!r})…")

    prompt = (
        f"{BATALHA_LLM_SYSTEM_PROMPT}\n\n"
        f"User theme: {theme_clean}\n"
        "Return ONLY the JSON object."
    )
    content = groq_user_message_text(
        prompt,
        temperature=BATALHA_LLM_TEMPERATURE,
        max_tokens=1024,
        none_as_empty=False,
        retry_label="batalha LLM",
        bad_request_runtime=lambda e: RuntimeError(f"Groq recusou a geração da batalha: {e}"),
        rate_limit_message=(
            "Limite de requisições Groq atingido ao gerar a batalha. Aguarde e tente novamente."
        ),
        model=BATALHA_LLM_MODEL,
    )
    raise_if_cancelled(cancel_event)

    if not content or not str(content).strip():
        raise RuntimeError("Groq retornou resposta vazia para a batalha")

    raw = _parse_batalha_json(str(content))
    spec = _validate_and_normalize_spec(raw, theme_clean)
    _emit_log(
        log_queue,
        f"[batalha 1/5] Duelo: {spec['oponente_1']} vs {spec['oponente_2']}",
    )
    return spec


def prepare_batalha_assets(
    spec: BatalhaSpec,
    *,
    work_dir: Path | None = None,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> BatalhaAssets:
    """Baixa avatares circulares dos dois oponentes (Fase 1)."""
    raise_if_cancelled(cancel_event)
    base = work_dir or (TEMP_DIR / "batalha" / _safe_dir_name(f"{spec['oponente_1']}_vs_{spec['oponente_2']}"))
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)

    _emit_log(log_queue, "[batalha 2/5] A buscar logos dos oponentes (web)…")
    _emit_log(log_queue, f"  · {spec['oponente_1']}: {spec['termo_busca_1']!r}")
    _emit_log(log_queue, f"  · {spec['oponente_2']}: {spec['termo_busca_2']!r}")
    raise_if_cancelled(cancel_event)

    av1, logo1 = fetch_opponent_graphics(
        spec["termo_busca_1"],
        spec["oponente_1"],
        spec["cor_1"],
    )
    raise_if_cancelled(cancel_event)
    av2, logo2 = fetch_opponent_graphics(
        spec["termo_busca_2"],
        spec["oponente_2"],
        spec["cor_2"],
    )

    p1 = save_avatar_png(av1, base / "avatar_1.png")
    p2 = save_avatar_png(av2, base / "avatar_2.png")
    l1 = save_avatar_png(logo1, base / "logo_1.png")
    l2 = save_avatar_png(logo2, base / "logo_2.png")
    _emit_log(log_queue, f"[batalha 2/5] Logos guardados em {base} (removidos após o vídeo)")

    return BatalhaAssets(
        spec=spec,
        avatar_1_path=p1,
        avatar_2_path=p2,
        logo_1_path=l1,
        logo_2_path=l2,
        work_dir=base,
    )


async def _synthesize_batalha_tts_async(
    text: str,
    out_path: Path,
    voice: str,
    *,
    log_label: str = "gancho",
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    from app.tts.tts_engine import synthesize_speech_to_path
    from app.tts.tts_voices import resolve_voice
    from app.video_processing.tts_dubber import edge_tts_save_to_path

    raise_if_cancelled(cancel_event)
    clean = (text or "").strip()
    if not clean:
        raise ValueError(f"Texto vazio para TTS ({log_label})")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)

    _emit_log(log_queue, f"[batalha 3/5] TTS de {log_label}…")
    opt = resolve_voice(voice)
    try:
        if opt.provider in ("gemini", "local"):
            await synthesize_speech_to_path(
                clean,
                out_path,
                voice,
                allow_edge_fallback=True,
            )
        else:
            await edge_tts_save_to_path(clean, out_path, opt.engine_voice)
    except Exception as e:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"TTS de {log_label} falhou: {e}") from e

    if not out_path.is_file() or out_path.stat().st_size < 32:
        raise RuntimeError(f"TTS de {log_label} gerou arquivo inválido: {out_path}")
    return out_path


async def _synthesize_hook_tts_async(
    hook_text: str,
    out_path: Path,
    voice: str,
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
) -> Path:
    return await _synthesize_batalha_tts_async(
        hook_text,
        out_path,
        voice,
        log_label="gancho de abertura",
        cancel_event=cancel_event,
        log_queue=log_queue,
    )


def _safe_dir_name(text: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", (text or "batalha").strip(), flags=re.UNICODE)
    return (s[:80] or "batalha").strip("_")


def _resolve_output_paths(theme: str, run_id: str) -> tuple[Path, Path]:
    stem = sanitize_clip_output_stem(f"batalha_{theme}_{run_id}")
    video_path = OUTPUT_DIR / f"{stem}.mp4"
    work_dir = TEMP_DIR / f"batalha_{run_id}"
    return video_path, work_dir


def _normalize_payload(payload: dict[str, Any]) -> tuple[str, str, str]:
    theme = str(payload.get("theme") or payload.get("tema") or "Batalha").strip()
    modo = normalize_batalha_modo(
        str(payload.get("modo") or payload.get("modo_jogo") or payload.get("game_mode") or "")
    )
    voice = str(
        payload.get("tts_voice") or payload.get("voice") or EDGE_TTS_VOICE_PT
    ).strip()
    return theme, modo, voice


def _save_batalha_caption(video_path: Path, legenda: str) -> Path:
    caption_path = video_path.with_suffix(".txt")
    caption_path.write_text(legenda.strip() + "\n", encoding="utf-8")
    return caption_path


def run_batalha_pipeline(
    theme: str,
    *,
    modo: str | None = None,
    tts_voice: str | None = None,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
    progress: Callable[[float], None] | None = None,
) -> BatalhaPipelineResult:
    """
    Orquestrador completo: Groq → imagens → TTS → simulação → FFmpeg MP4.

    ``progress`` recebe frações 0.0–1.0 (opcional, ex. barra da GUI).
    """
    payload = {
        "theme": theme,
        "modo": modo,
        "tts_voice": tts_voice,
    }
    return run_batalha_pipeline_from_payload(
        payload,
        cancel_event=cancel_event,
        log_queue=log_queue,
        progress=progress,
    )


def run_batalha_pipeline_from_payload(
    payload: dict[str, Any],
    *,
    cancel_event: Event | None = None,
    log_queue: Queue[Any] | None = None,
    progress: Callable[[float], None] | None = None,
) -> BatalhaPipelineResult:
    """Entrada usada pela GUI (`job_type`: ``batalha``)."""

    def _prog(frac: float) -> None:
        if progress is not None:
            try:
                progress(max(0.0, min(1.0, float(frac))))
            except Exception:
                pass

    theme, modo_norm, voice = _normalize_payload(payload)
    if payload.get("modo"):
        modo_norm = normalize_batalha_modo(str(payload.get("modo")))

    run_id = time.strftime("%Y%m%d_%H%M%S")
    video_path, work_dir = _resolve_output_paths(theme, run_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    _emit_log(
        log_queue,
        f"Início batalha — tema={theme!r}, modo={modo_norm}",
    )
    _prog(0.02)

    spec = generate_batalha_spec_llm(theme, cancel_event=cancel_event, log_queue=log_queue)
    _prog(0.12)

    assets = prepare_batalha_assets(
        spec,
        work_dir=work_dir,
        cancel_event=cancel_event,
        log_queue=log_queue,
    )
    _prog(0.22)

    legenda_path = assets.work_dir / "legenda_tiktok.txt"
    legenda_path.write_text(spec["legenda_tiktok"].strip() + "\n", encoding="utf-8")
    hook_path = assets.work_dir / "audio_hook.mp3"

    intro_path = asyncio.run(
        _synthesize_hook_tts_async(
            spec["hook"],
            hook_path,
            voice,
            cancel_event=cancel_event,
            log_queue=log_queue,
        )
    )
    _prog(0.28)

    mid_narration_path = assets.work_dir / "audio_narracao.mp3"
    mid_narration_path = asyncio.run(
        _synthesize_batalha_tts_async(
            spec["script_narracao"],
            mid_narration_path,
            voice,
            log_label="narração do meio",
            cancel_event=cancel_event,
            log_queue=log_queue,
        )
    )
    _prog(0.32)

    from app.pipelines.batalha.batalha_ffmpeg import assemble_batalha_video_ffmpeg, probe_audio_duration_sec
    from app.pipelines.batalha.batalha_frames import (
        BATALHA_MOD_PLINKO,
        create_simulation,
        plinko_victory_narration_text,
        plinko_victory_screen_duration_sec,
        save_simulation_artifacts,
    )

    victory_narration_paths: tuple[Path, Path] | None = None
    if modo_norm == BATALHA_MOD_PLINKO:
        _emit_log(log_queue, "[batalha 3/5] TTS de vitória (ambos oponentes, mesma voz)…")
        raise_if_cancelled(cancel_event)
        v0_path = assets.work_dir / "audio_victory_0.mp3"
        v1_path = assets.work_dir / "audio_victory_1.mp3"
        asyncio.run(
            _synthesize_hook_tts_async(
                plinko_victory_narration_text(spec["oponente_1"]),
                v0_path,
                voice,
                cancel_event=cancel_event,
                log_queue=log_queue,
            )
        )
        raise_if_cancelled(cancel_event)
        asyncio.run(
            _synthesize_hook_tts_async(
                plinko_victory_narration_text(spec["oponente_2"]),
                v1_path,
                voice,
                cancel_event=cancel_event,
                log_queue=log_queue,
            )
        )
        victory_narration_paths = (v0_path, v1_path)

    _emit_log(log_queue, "[batalha 4/5] A simular física e codificar vídeo (FFmpeg stdin)…")
    raise_if_cancelled(cancel_event)

    sim = create_simulation(
        modo_norm,
        assets.spec,
        assets.avatar_1_path,
        assets.avatar_2_path,
        logo_1_path=assets.logo_1_path,
        logo_2_path=assets.logo_2_path,
    )

    victory_narration_path: Path | None = None
    if modo_norm == BATALHA_MOD_PLINKO and victory_narration_paths is not None:
        d0 = probe_audio_duration_sec(victory_narration_paths[0], label="vitória 1")
        d1 = probe_audio_duration_sec(victory_narration_paths[1], label="vitória 2")
        sim.plinko_victory_screen_duration_sec = plinko_victory_screen_duration_sec(
            max(d0, d1)
        )

    frame_est = int(42 * 30)

    def _frame_progress(n: int) -> None:
        _prog(0.32 + 0.48 * min(1.0, n / max(frame_est, 1)))

    silent_path = assets.work_dir / "video_silent.mp4"

    if modo_norm == BATALHA_MOD_PLINKO and victory_narration_paths is not None:
        from app.pipelines.batalha.batalha_ffmpeg import encode_simulation_to_silent_mp4, mux_batalha_video_with_audio

        encode_simulation_to_silent_mp4(
            sim,
            silent_path,
            cancel_check=cancel_event,
            progress_callback=_frame_progress,
        )
        raise_if_cancelled(cancel_event)
        winner = sim.winner_id if sim.winner_id in (0, 1) else 0
        victory_narration_path = victory_narration_paths[winner]
        _emit_log(
            log_queue,
            f"[batalha 4/5] Narração final: {plinko_victory_narration_text(spec[f'oponente_{winner + 1}'])}",
        )
        mux_batalha_video_with_audio(
            silent_path,
            intro_path,
            sim.collision_times_sec,
            video_path,
            work_dir=assets.work_dir,
            mid_narration_path=mid_narration_path,
            victory_narration_path=victory_narration_path,
            victory_start_sec=getattr(sim, "plinko_victory_screen_start_sec", None),
            cancel_check=cancel_event,
        )
    else:
        assemble_batalha_video_ffmpeg(
            sim,
            intro_path,
            video_path,
            work_dir=assets.work_dir,
            mid_narration_path=mid_narration_path,
            cancel_check=cancel_event,
            progress_callback=_frame_progress,
        )

    from app.pipelines.batalha.batalha_frames import BatalhaSimulationResult

    sim_meta = BatalhaSimulationResult(
        collision_times_sec=sim.collision_times_sec,
        winner_id=sim.winner_id,
        duration_sec=sim.sim_time,
        modo=modo_norm,
        frame_count=0,
        frames=[],
    )
    save_simulation_artifacts(sim_meta, assets.work_dir, save_preview_frames=False)

    winner = sim.winner_id
    winner_name = (
        spec["oponente_1"]
        if winner == 0
        else spec["oponente_2"]
        if winner == 1
        else "—"
    )
    _emit_log(
        log_queue,
        f"[batalha 4/5] Vencedor: {winner_name} · {len(sim.collision_times_sec)} impactos (SFX)",
    )
    _prog(0.88)

    caption_out = _save_batalha_caption(video_path, spec["legenda_tiktok"])
    cleanup_batalha_downloaded_assets(assets.work_dir)
    _emit_log(log_queue, "[batalha 5/5] Logos temporários removidos do disco")
    _emit_log(log_queue, f"[batalha 5/5] Vídeo pronto: {video_path}")
    _prog(1.0)

    return BatalhaPipelineResult(
        video_path=video_path,
        caption_path=caption_out,
        work_dir=assets.work_dir,
    )
