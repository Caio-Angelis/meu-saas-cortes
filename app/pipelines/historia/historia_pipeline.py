"""
Pipeline História — texto → cenas (Groq) → TTS + vídeo (ComfyUI) → FFmpeg → MP4 final.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from app.ai_integrations.groq_chat import groq_user_message_text
from app.pipelines.historia.comfyui_client import gerar_video_comfyui
from app.core.config import EDGE_TTS_VOICE_PT, FFMPEG_PATH, OUTPUT_DIR, OUTPUT_VIDEO_HEIGHT, OUTPUT_VIDEO_WIDTH, TEMP_DIR
from app.tts.tts_engine import synthesize_speech_to_path

_log = logging.getLogger("historia_pipeline")

HISTORIAS_DIR: Path = OUTPUT_DIR / "historias"

HISTORIA_LLM_MODEL = "llama-3.3-70b-versatile"
HISTORIA_LLM_TEMPERATURE = 0.35
HISTORIA_MAX_VIDEOS = 5

DEFAULT_PROMPT_VISUAL_FALLBACK = "cinematic shot, abstract background, dramatic lighting"

CENAS_SYSTEM_PROMPT = """You are a storyboard director and visual continuity supervisor for short-form vertical videos.
Split the user's story into sequential scenes for narration and AI image/video generation (ComfyUI).

Return STRICTLY a JSON array (no markdown, no code fences, no extra text) in this format:
[
  {"narracao": "texto em português para locução", "prompt_visual": "detailed english visual prompt"}
]

Rules for "narracao":
- Brazilian Portuguese narration text for TTS (natural spoken language).
- Each scene covers one story beat; keep narration concise per scene.
- Return AT MOST 5 scenes in the array — NEVER more than 5 items. Split the full story into exactly 3–5 major visual beats (beginning, development, climax, etc.). If the story is long, merge minor beats into these 5 chapters so the entire story is still narrated across them.

Rules for "prompt_visual" (English, for Stable Diffusion / video generation):
- Write RICH, SPECIFIC prompts (aim for 35–80 words per scene). Never use vague placeholders like "a person", "someone", "a man", "a woman", "a student" without concrete visual details.
- BEFORE writing scene prompts, mentally define a fixed "character sheet" for every recurring character in the story: approximate age, gender presentation, skin tone, hair (color, length, style), build/height, face traits (glasses, beard, mustache, freckles), and default outfit (colors, garment types). Invent plausible details if the story does not specify them — YOU must invent them once and reuse them verbatim.
- CHARACTER CONSISTENCY IS MANDATORY: whenever the same person appears again (narrator, teacher, protagonist, etc.), repeat the EXACT SAME character description block word-for-word in every prompt_visual where they appear. Only change pose, action, expression, and camera angle — NOT their core appearance or wardrobe unless the story explicitly says they changed clothes.
- LOCATION CONSISTENCY: if a scene returns to the same place (classroom, bedroom, street), reuse the same environmental descriptors (furniture, colors, lighting, time of day).
- Structure each prompt_visual as: [character block if present] + [specific action/pose/expression] + [environment/background] + [lighting/mood] + [camera/framing, e.g. medium shot, close-up] + [optional style tag, e.g. cinematic, photorealistic, dramatic]. Always compose for vertical 9:16 portrait framing (subject centered, room for captions at top/bottom).
- BAD example: "a person in a classroom looking sad"
- GOOD example: "medium shot, a tall white man in his 50s with gray short hair, thick mustache, dark brown tweed blazer and white shirt, tired eyes and furrowed brow, standing at a cluttered physics lab desk with chalkboard equations, warm fluorescent classroom lighting, cinematic photorealistic, shallow depth of field"
- For abstract narration, inner monologue, or dialogue without clear action: show the narrator or relevant character with a matching emotional expression in a concrete setting — still with full character and environment detail, never a generic silhouette alone.

REGRA CRÍTICA: O campo 'prompt_visual' NUNCA pode ser vazio. Se o trecho da narração for um conceito abstrato, um diálogo ou um desabafo sem ação clara, descreva visualmente as emoções do narrador, o clima do ambiente (ex: quarto escuro, escola, pessoa ansiosa) ou gere um cenário genérico e dramático que combine com o tom do texto — sempre com descrição física completa dos personagens envolvidos.

Return only the JSON array."""


class HistoriaCena(TypedDict):
    narracao: str
    prompt_visual: str


@dataclass(frozen=True)
class HistoriaPipelineResult:
    video_path: Path
    work_dir: Path
    scenes: list[HistoriaCena]


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


def _parse_cenas_json(content: str) -> list[HistoriaCena]:
    raw = _extract_json_array(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_sanitize_json(raw))
    if not isinstance(data, list) or not data:
        raise ValueError("JSON raiz deve ser um array não vazio")

    scenes: list[HistoriaCena] = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Cena {i}: objeto inválido")
        narracao = re.sub(r"\s+", " ", str(item.get("narracao") or "")).strip()
        prompt_visual = re.sub(r"\s+", " ", str(item.get("prompt_visual") or "")).strip()
        if not narracao:
            raise ValueError(f"Cena {i}: 'narracao' vazia")
        scenes.append({"narracao": narracao, "prompt_visual": prompt_visual})
    return scenes


def _merge_scenes_to_max(
    scenes: list[HistoriaCena],
    max_videos: int = HISTORIA_MAX_VIDEOS,
) -> list[HistoriaCena]:
    """Agrupa cenas excedentes em no máximo ``max_videos`` blocos de narração."""
    if len(scenes) <= max_videos:
        return scenes

    _log.warning(
        "Groq retornou %s cena(s); a fundir em %s bloco(s) de vídeo.",
        len(scenes),
        max_videos,
    )
    merged: list[HistoriaCena] = []
    total = len(scenes)
    for slot in range(max_videos):
        start = (slot * total) // max_videos
        end = ((slot + 1) * total) // max_videos
        chunk = scenes[start:end]
        if not chunk:
            continue
        narracao = " ".join(part["narracao"] for part in chunk).strip()
        prompt_visual = ""
        for part in reversed(chunk):
            candidate = (part.get("prompt_visual") or "").strip()
            if candidate:
                prompt_visual = candidate
                break
        merged.append({"narracao": narracao, "prompt_visual": prompt_visual})
    return merged


def quebrar_historia_em_cenas(story_text: str) -> list[HistoriaCena]:
    """Etapa 1 — Groq divide a história em cenas (narração + prompt visual)."""
    story_clean = (story_text or "").strip()
    if not story_clean:
        raise ValueError("Texto da história vazio")

    prompt = (
        f"{CENAS_SYSTEM_PROMPT}\n\n"
        f"História:\n{story_clean}\n\n"
        "Return ONLY the JSON array."
    )
    content = groq_user_message_text(
        prompt,
        temperature=HISTORIA_LLM_TEMPERATURE,
        max_tokens=8192,
        none_as_empty=False,
        retry_label="historia cenas",
        bad_request_runtime=lambda e: RuntimeError(f"Groq recusou a quebra de cenas: {e}"),
        rate_limit_message=(
            "Limite de requisições Groq atingido ao dividir a história. Aguarde e tente novamente."
        ),
        model=HISTORIA_LLM_MODEL,
    )
    if not content or not str(content).strip():
        raise RuntimeError("Groq retornou resposta vazia para as cenas")

    scenes = _parse_cenas_json(str(content))
    scenes = _merge_scenes_to_max(scenes, HISTORIA_MAX_VIDEOS)
    _log.info("História dividida em %s cena(s) (máx. %s vídeos)", len(scenes), HISTORIA_MAX_VIDEOS)
    return scenes


def _mux_cena_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    """Etapa 3 — loop do vídeo até o áudio TTS acabar; escala para 9:16 de saída."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    vf = (
        f"scale={OUTPUT_VIDEO_WIDTH}:{OUTPUT_VIDEO_HEIGHT}:flags=lanczos,"
        f"setsar=1"
    )
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-vf",
        vf,
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"FFmpeg falhou ao sincronizar cena ({video_path.name} + {audio_path.name}): "
            f"{detail or result.returncode}"
        )
    if not output_path.is_file() or output_path.stat().st_size < 256:
        raise RuntimeError(f"FFmpeg não gerou MP4 de cena: {output_path}")


def _concat_cenas_prontas(segment_paths: list[Path], output_path: Path, work_dir: Path) -> None:
    """Etapa 4 — concat demuxer com lista.txt e -c copy."""
    if not segment_paths:
        raise ValueError("Nenhuma cena pronta para concatenar")

    lista_txt = work_dir / "lista.txt"
    lines: list[str] = []
    for p in segment_paths:
        escaped = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    lista_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lista_txt),
        "-c",
        "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"FFmpeg falhou na concatenação final: {detail or result.returncode}")
    if not output_path.is_file() or output_path.stat().st_size < 1024:
        raise RuntimeError(f"MP4 final inválido após concat: {output_path}")


def _cleanup_temporarios(work_dir: Path, scene_count: int, *, videos_gerados: int) -> None:
    """Etapa 6 — remove MP3, MP4 raw e lista.txt da pasta temporária."""
    for i in range(scene_count):
        try:
            (work_dir / f"cena_{i}.mp3").unlink(missing_ok=True)
        except OSError:
            pass
    for k in range(videos_gerados):
        try:
            (work_dir / f"video_{k}_raw.mp4").unlink(missing_ok=True)
        except OSError:
            pass
    try:
        (work_dir / "lista.txt").unlink(missing_ok=True)
    except OSError:
        pass


async def _gerar_ativos_cena(
    scene: HistoriaCena,
    index: int,
    work_dir: Path,
    *,
    voice: str,
) -> Path:
    """Etapa 2a — TTS da cena (áudio)."""
    audio_path = work_dir / f"cena_{index}.mp3"

    _log.info("[historia 2/4] Cena %s — TTS…", index)
    await synthesize_speech_to_path(scene["narracao"], audio_path, voice)

    if not audio_path.is_file() or audio_path.stat().st_size < 32:
        raise RuntimeError(f"TTS gerou arquivo inválido: {audio_path}")

    return audio_path


async def _gerar_video_slot(
    prompt_visual: str,
    slot: int,
    work_dir: Path,
) -> Path:
    """Etapa 2b — um vídeo ComfyUI por slot (máx. ``HISTORIA_MAX_VIDEOS``)."""
    raw_video_path = work_dir / f"video_{slot}_raw.mp4"

    _log.info(
        "[historia 2/4] Vídeo %s/%s — ComfyUI…",
        slot + 1,
        HISTORIA_MAX_VIDEOS,
    )
    await asyncio.to_thread(
        gerar_video_comfyui,
        prompt_visual,
        raw_video_path,
    )

    if not raw_video_path.is_file() or raw_video_path.stat().st_size < 256:
        raise RuntimeError(f"ComfyUI não gerou vídeo válido: {raw_video_path}")

    return raw_video_path


async def run_historia_pipeline_async(
    story_text: str,
    *,
    voice: str = EDGE_TTS_VOICE_PT,
    work_dir: Path | None = None,
) -> HistoriaPipelineResult:
    """Executa o pipeline completo (async — TTS + ComfyUI em thread)."""
    HISTORIAS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    run_id = time.strftime("%Y%m%d_%H%M%S")
    temp = work_dir or (TEMP_DIR / f"historia_{run_id}")
    temp.mkdir(parents=True, exist_ok=True)

    _log.info("[historia 1/4] A dividir história em cenas via Groq…")
    scenes = quebrar_historia_em_cenas(story_text)

    prontas: list[Path] = []
    resolved_scenes: list[HistoriaCena] = []
    ultimo_prompt_visual_valido = DEFAULT_PROMPT_VISUAL_FALLBACK
    ultimo_raw_video: Path | None = None
    ultimo_prompt_gerado = ""
    videos_gerados = 0

    for i, scene in enumerate(scenes):
        prompt_raw = (scene.get("prompt_visual") or "").strip()
        if prompt_raw:
            ultimo_prompt_visual_valido = prompt_raw
            prompt_visual = prompt_raw
        else:
            prompt_visual = ultimo_prompt_visual_valido
            _log.warning(
                "[historia] Cena %s com prompt_visual vazio; a usar fallback: %s",
                i,
                prompt_visual,
            )

        resolved_scene: HistoriaCena = {
            "narracao": scene["narracao"],
            "prompt_visual": prompt_visual,
        }
        resolved_scenes.append(resolved_scene)

        audio_path = await _gerar_ativos_cena(
            resolved_scene,
            i,
            temp,
            voice=voice,
        )

        trocar_video = (
            ultimo_raw_video is None
            or (prompt_visual != ultimo_prompt_gerado and videos_gerados < HISTORIA_MAX_VIDEOS)
        )
        if trocar_video:
            ultimo_raw_video = await _gerar_video_slot(prompt_visual, videos_gerados, temp)
            ultimo_prompt_gerado = prompt_visual
            videos_gerados += 1
        else:
            _log.info(
                "[historia 2/4] Cena %s — reutiliza vídeo %s (loop até áudio acabar)",
                i,
                videos_gerados,
            )

        assert ultimo_raw_video is not None

        pronta_path = temp / f"cena_{i}_pronta.mp4"
        _log.info("[historia 3/4] Cena %s — sincronizar vídeo + áudio…", i)
        _mux_cena_video_audio(ultimo_raw_video, audio_path, pronta_path)
        prontas.append(pronta_path)

    final_path = HISTORIAS_DIR / f"historia_final_{run_id}.mp4"
    _log.info("[historia 4/4] Concatenação final (%s cena(s))…", len(prontas))
    _concat_cenas_prontas(prontas, final_path, temp)

    _cleanup_temporarios(temp, len(resolved_scenes), videos_gerados=videos_gerados)
    _log.info("História concluída: %s", final_path)

    return HistoriaPipelineResult(video_path=final_path, work_dir=temp, scenes=resolved_scenes)


def run_historia_pipeline(
    story_text: str,
    *,
    voice: str = EDGE_TTS_VOICE_PT,
    work_dir: Path | None = None,
) -> HistoriaPipelineResult:
    """Entrada síncrona — envolve ``run_historia_pipeline_async``."""
    return asyncio.run(
        run_historia_pipeline_async(story_text, voice=voice, work_dir=work_dir)
    )
