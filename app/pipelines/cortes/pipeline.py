import bisect
import logging
import math
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from app.ai_integrations.tiktok_caption import (
    append_source_attribution_to_caption,
    generate_tiktok_post_caption,
    save_tiktok_caption_file,
)
from app.ai_integrations.transcriber import transcribe_audio
from app.ai_integrations.translator import translate_segments
from app.ai_integrations.viral_analyzer import (
    VIRAL_ANALYZER_VERSION,
    ViralAnalysisResult,
    analyze_viral_moments,
)
from app.analytics.content_profile import load_content_performance_profile
from app.analytics.retention_loop import load_growth_profile
from app.core.cache import fingerprint_file, write_json
from app.core.cache_pipeline import (
    load_cached_moment_analysis,
    load_cached_moments,
    load_cached_segments,
    load_cached_translated_segments,
    save_cached_moment_analysis,
    save_cached_moments,
    save_cached_segments,
    save_cached_translated_segments,
)
from app.core.cancel import raise_if_cancelled, reset_cancel
from app.core.clip_output_naming import sanitize_clip_output_stem
from app.core.config import (
    CLIP_DURATION_EXPLICIT,
    CLIP_ENCODE_PARALLEL_CPU,
    CLIP_ENCODE_PARALLEL_GPU,
    CLIP_SPEED_UP_PERCENT,
    DUB_TRIM_SILENCE,
    OUTPUT_DIR,
    TEMP_DIR,
    USE_GPU_CLIP_ENCODE,
    VIRAL_CANDIDATE_COUNT,
    VIRAL_CLIPS_COUNT,
    VIRAL_SELECTION_PROFILE,
    pipeline_thread_pool_max_workers,
)
from app.download.ytdlp_download import VideoSourceAttribution, lookup_source_attribution
from app.subtitle.srt_generator import generate_srt
from app.video_processing.audio_extractor import extract_audio
from app.video_processing.subtitle_burner import cut_and_burn_subtitles
from app.video_processing.tts_dubber import (
    build_english_dub_audio,
    build_portuguese_dub_audio,
    mux_video_with_new_audio,
    remove_long_silence_from_video,
)
from app.video_processing.video_splitter import (
    cleanup_split_directory,
    split_video_into_chunks,
)

_log = logging.getLogger("pipeline")

_cpu_enc_sem = threading.BoundedSemaphore(max(1, CLIP_ENCODE_PARALLEL_CPU))
_gpu_enc_sem = threading.BoundedSemaphore(max(1, CLIP_ENCODE_PARALLEL_GPU))
_TRANSLATION_ERROR_MARKERS = (
    "error 500",
    "server error",
    "there was an error",
    "please try again later",
    "erro 500",
    "erro do servidor",
    "servidor falhou",
    "tente novamente mais tarde",
)


def _safe_progress(cb: Callable[[float], None] | None, x: float) -> None:
    if cb is None:
        return
    try:
        cb(max(0.0, min(1.0, float(x))))
    except Exception:
        pass


def _write_run_manifest(
    *,
    video_path: str,
    video_name: str,
    video_fp: str,
    options: dict,
    cache_hits: dict,
    moments: list[dict],
    outputs: list[str],
) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    manifest_path = OUTPUT_DIR / f"{video_name}__run_manifest_{ts}.json"
    selection_info = cache_hits.get("_selection") if isinstance(cache_hits, dict) else None
    public_cache_hits = {
        key: value for key, value in cache_hits.items() if key != "_selection"
    }
    payload = {
        "video_path": video_path,
        "video_name": video_name,
        "video_fingerprint": video_fp,
        "options": options,
        "cache_hits": public_cache_hits,
        "moments": moments,
        "selection": selection_info or {
            "profile": VIRAL_SELECTION_PROFILE,
            "candidates_considered": moments,
        },
        "outputs": outputs,
        "created_at": ts,
    }
    try:
        write_json(manifest_path, payload)
    except Exception as e:
        _log.warning("Falha ao salvar manifest (%s): %s", manifest_path, e)
        return ""
    return str(manifest_path)


def _segments_for_clip(
    segments: list[dict], clip_start: float, clip_end: float, *, precomputed: tuple[list[float], list[float]] | None = None
) -> list[dict]:
    """
    Segmentos que intersectam [clip_start, clip_end], com tempos limitados ao clipe.
    Inclui falas cortadas na borda do corte viral (antes só entravam segmentos 100% dentro
    da janela, o que sumia o começo/fim das frases e fazia legenda/dublagem 'pular').
    `precomputed`: (starts, ends) pré-calculados para evitar reconstrução por clipe.
    """
    if not segments:
        return []
    if precomputed is not None:
        starts, ends = precomputed
    else:
        starts = [float(s["start"]) for s in segments]
        ends = [float(s["end"]) for s in segments]
    i0 = bisect.bisect_right(ends, clip_start)
    i1 = bisect.bisect_left(starts, clip_end) - 1
    if i0 > i1:
        return []

    out: list[dict] = []
    for seg in segments[i0 : i1 + 1]:
        s0 = float(seg["start"])
        s1 = float(seg["end"])
        if s1 <= clip_start or s0 >= clip_end:
            continue
        overlap_s = max(s0, clip_start)
        overlap_e = min(s1, clip_end)
        if overlap_e - overlap_s < 0.04:
            continue
        text = (seg.get("text") or "").strip()
        seg_dur = s1 - s0
        if seg_dur <= 0:
            continue
        partial = s0 < clip_start - 1e-6 or s1 > clip_end + 1e-6
        if partial and text:
            words = text.split()
            if len(words) >= 2:
                lo = max(0.0, min(1.0, (overlap_s - s0) / seg_dur))
                hi = max(lo, min(1.0, (overlap_e - s0) / seg_dur))
                i_w0 = int(lo * len(words))
                i_w1 = int(math.ceil(hi * len(words)))
                i_w1 = max(i_w0 + 1, min(len(words), i_w1))
                text = " ".join(words[i_w0:i_w1]).strip()
        if not text:
            continue
        out.append({"start": overlap_s, "end": overlap_e, "text": text})
    out.sort(key=lambda x: float(x["start"]))
    return out


def _cleanup(*paths: str) -> None:
    for p in paths:
        if os.path.exists(p):
            os.remove(p)


def _translation_segments_are_usable(
    translated: list[dict] | None,
    original: list[dict],
) -> bool:
    if translated is None or len(translated) != len(original):
        return False
    for segment in translated:
        text = " ".join(str(segment.get("text") or "").casefold().split())
        if not text or any(marker in text for marker in _TRANSLATION_ERROR_MARKERS):
            return False
    return True


def _clip_uses_gpu_encoder(clip_index: int, total_clips: int) -> bool:
    """Todos os clipes usam o encoder de GPU (NVENC) quando habilitado."""
    return USE_GPU_CLIP_ENCODE


def _process_clip_task(
    clip_index: int,
    total_clips: int,
    moment: dict,
    video_path: str,
    video_name: str,
    segments: list[dict],
    video_fp: str,
    target_language: str,
    posicao: str,
    fonte: str,
    cor_letra: str,
    cor_fundo: str,
    opacidade: int,
    dub_to: str | None,
    tts_voice: str | None,
    outro_text: str | None = None,
    source_attribution: VideoSourceAttribution | None = None,
    precomputed_se: tuple[list[float], list[float]] | None = None,
) -> str:
    start, end = moment["start"], moment["end"]
    use_gpu = _clip_uses_gpu_encoder(clip_index, total_clips)

    clip_segments = _segments_for_clip(segments, start, end, precomputed=precomputed_se)
    translated = load_cached_translated_segments(
        video_fp=video_fp,
        clip_index=clip_index,
        target=target_language,
        segments=clip_segments,
    )
    if not _translation_segments_are_usable(translated, clip_segments):
        if translated is not None:
            _log.warning(
                "Cache de tradução inválido para o clipe %s/%s; refazendo sem a resposta de erro.",
                clip_index,
                total_clips,
            )
        _log.info(
            "Clipe %s/%s: a traduzir segmentos para %s…",
            clip_index,
            total_clips,
            target_language,
        )
        translated = translate_segments(clip_segments, source="auto", target=target_language)
        if not _translation_segments_are_usable(translated, clip_segments):
            _log.warning(
                "Tradução indisponível para o clipe %s/%s; usando a transcrição original.",
                clip_index,
                total_clips,
            )
            translated = clip_segments
        save_cached_translated_segments(
            video_fp=video_fp,
            clip_index=clip_index,
            target=target_language,
            input_segments=clip_segments,
            translated=translated,
        )

    srt_path = str(TEMP_DIR / f"{video_name}_clip_{clip_index}.srt")
    out_stem = f"{clip_index}_{sanitize_clip_output_stem(video_name)}"
    final_path = str(OUTPUT_DIR / f"{out_stem}.mp4")

    playback_speed = 1.0 + CLIP_SPEED_UP_PERCENT / 100.0
    _log.info(
        "Clipe %s/%s: a gerar arquivo de legendas (.srt) alinhado ao corte…",
        clip_index,
        total_clips,
    )
    generate_srt(translated, srt_path, offset=start, playback_speed=playback_speed)

    clip_plain_for_caption = " ".join(
        (seg.get("text") or "").strip() for seg in translated if seg.get("text")
    )

    burned_out = (
        str(TEMP_DIR / f"{video_name}_clip_{clip_index}_burned.mp4")
        if dub_to
        else final_path
    )

    _log.info(
        "Clipe %s/%s: a pedir legenda de postagem (TikTok) à API em paralelo com o encode do vídeo…",
        clip_index,
        total_clips,
    )
    cap_holder: list[str | None] = [None]
    cap_error: list[BaseException | None] = [None]

    def _caption_worker() -> None:
        try:
            cap_holder[0] = generate_tiktok_post_caption(
                clip_plain_for_caption,
                target_language,
                hook=moment.get("hook") or None,
                category=moment.get("category") or None,
                topic=moment.get("topic") or None,
            )
        except BaseException as e:
            cap_error[0] = e

    cap_thread = threading.Thread(target=_caption_worker, daemon=True)
    cap_thread.start()
    sem = _gpu_enc_sem if use_gpu else _cpu_enc_sem
    with sem:
        _log.info(
            "Clipe %s/%s: a cortar o trecho e a queimar legendas no vídeo (FFmpeg)…",
            clip_index,
            total_clips,
        )
        cut_and_burn_subtitles(
            video_path,
            start,
            end,
            srt_path,
            burned_out,
            posicao,
            fonte,
            cor_letra,
            cor_fundo,
            opacidade,
            hook_phrase=moment.get("hook") or None,
            target_language=target_language,
            cta_text=moment.get("cta"),
            outro_text=outro_text,
            use_gpu_encoder=use_gpu,
        )

    if dub_to:
        _log.info(
            "Clipe %s/%s: dublagem em %s (Edge-TTS + mux)…",
            clip_index,
            total_clips,
            dub_to,
        )
        segments_dub = load_cached_translated_segments(
            video_fp=video_fp,
            clip_index=clip_index,
            target=dub_to,
            segments=clip_segments,
        )
        if segments_dub is None:
            segments_dub = translate_segments(clip_segments, source="auto", target=dub_to)
            save_cached_translated_segments(
                video_fp=video_fp,
                clip_index=clip_index,
                target=dub_to,
                input_segments=clip_segments,
                translated=segments_dub,
            )
        dub_audio = str(TEMP_DIR / f"{video_name}_clip_{clip_index}_dub.m4a")
        if dub_to == "pt":
            build_portuguese_dub_audio(
                segments_dub,
                start,
                end,
                playback_speed,
                dub_audio,
                voice=tts_voice,
                temp_tag=f"{video_name}_{clip_index}",
            )
        else:
            build_english_dub_audio(
                segments_dub,
                start,
                end,
                playback_speed,
                dub_audio,
                voice=tts_voice,
                temp_tag=f"{video_name}_{clip_index}",
            )
        muxed = str(TEMP_DIR / f"{video_name}_clip_{clip_index}_dub_muxed.mp4")
        mux_video_with_new_audio(burned_out, dub_audio, muxed)
        if DUB_TRIM_SILENCE:
            remove_long_silence_from_video(muxed, final_path)
        else:
            shutil.copyfile(muxed, final_path)
        _cleanup(burned_out, dub_audio, muxed)

    cap_thread.join()
    if cap_error[0] is not None:
        raise cap_error[0]
    caption_text = cap_holder[0] or ""

    caption_text = append_source_attribution_to_caption(
        caption_text,
        source_attribution,
        language=target_language,
    )

    caption_path = save_tiktok_caption_file(final_path, caption_text)

    _cleanup(srt_path)

    _log.info("Clipe %s/%s gerado: %s", clip_index, total_clips, final_path)
    _log.info("Legenda de postagem (TikTok): %s", caption_path)

    return final_path


def _prepare_transcription_and_moments(
    video_path: str,
    video_name: str,
    target_language: str,
    progress_local: Callable[[float], None] | None = None,
    manual_start: float | None = None,
    manual_end: float | None = None,
    hook_text: str | None = None,
) -> tuple[str, list[dict], list[dict], dict]:
    raise_if_cancelled()
    audio_path = str(TEMP_DIR / f"{video_name}.mp3")
    video_fp = fingerprint_file(video_path)
    cache_hits = {"segments": False, "moments": False}

    _safe_progress(progress_local, 0.02)

    transcribe_opts = {"impl": "groq_whisper", "language": None}
    segments = load_cached_segments(video_fp, transcribe_opts=transcribe_opts)
    if segments is None:
        raise_if_cancelled()
        _log.info("[1/5] A extrair áudio do vídeo (para transcrição)…")
        _safe_progress(progress_local, 0.05)
        extract_audio(video_path, audio_path)

        raise_if_cancelled()
        _log.info("[2/5] A transcrever o áudio (Groq Whisper)…")
        _safe_progress(progress_local, 0.12)
        segments = transcribe_audio(audio_path, source_video_path=video_path)
        _cleanup(audio_path)
        save_cached_segments(video_fp, segments, transcribe_opts=transcribe_opts)
        _safe_progress(progress_local, 0.48)
    else:
        cache_hits["segments"] = True
        _log.info("[cache] Transcrição já existente — a saltar extração e transcrição.")
        _safe_progress(progress_local, 0.48)

    if (manual_start is None) != (manual_end is None):
        raise ValueError("Preencha manual_start e manual_end juntos para um corte manual.")
    if manual_start is not None and manual_end is not None:
        start = float(manual_start)
        end = float(manual_end)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("A faixa manual precisa ter segundos finitos, com end maior que start.")
        if end - start < 4.0:
            raise ValueError("O corte manual precisa ter pelo menos 4 segundos.")
        transcript_end = max(
            (float(segment.get("end") or 0.0) for segment in segments),
            default=end,
        )
        if start >= transcript_end:
            raise ValueError(
                f"manual_start ({start:.3f}s) está além do fim da transcrição ({transcript_end:.3f}s)."
            )
        end = min(end, transcript_end)
        if end - start < 4.0:
            raise ValueError("A faixa manual ficou curta após limitar ao fim da transcrição.")
        moment = {
            "start": round(start, 3),
            "end": round(end, 3),
            "reason": "Trecho escolhido manualmente pelo editor.",
            "hook": (hook_text or "").strip(),
            "cta": "",
            "category": "controversy_opinion",
            "topic": "palhetada e riff de metal",
            "entities": [],
            "viral_score": 10.0,
            "hook_strength": 10.0,
            "standalone_clarity": 10.0,
            "controversy": 9.0,
            "comment_potential": 10.0,
            "ending_payoff": 8.0,
        }
        cache_hits["_selection"] = {
            "profile": "manual",
            "performance_profile_used": False,
            "manual_start": moment["start"],
            "manual_end": moment["end"],
            "candidates_considered": [moment],
        }
        _safe_progress(progress_local, 0.58)
        _log.info(
            "[3/5] Faixa manual selecionada: %.3fs–%.3fs (%.1fs).",
            moment["start"],
            moment["end"],
            moment["end"] - moment["start"],
        )
        return video_fp, segments, [moment], cache_hits

    _log.info("[3/5] A analisar os melhores momentos virais (Groq)…")
    _safe_progress(progress_local, 0.5)
    try:
        performance_profile = load_content_performance_profile()
    except Exception as exc:
        _log.warning("Perfil histórico indisponível; seguindo sem ele: %s", exc)
        performance_profile = None
    # Feedback loop de retenção: o growth profile sugere a duração-alvo dos clipes,
    # a menos que a pessoa tenha fixado CLIP_DURATION manualmente no ambiente.
    target_clip_duration: float | None = None
    if not CLIP_DURATION_EXPLICIT:
        growth_rec = (load_growth_profile() or {}).get("recommended_clip_duration_sec")
        if growth_rec:
            target_clip_duration = float(growth_rec)
            _log.info(
                "[feedback-loop] Duração-alvo de %.0fs vinda do growth profile.",
                target_clip_duration,
            )
    moments_opts = {
        "analyzer_version": VIRAL_ANALYZER_VERSION,
        "output_language": target_language,
        "selection_profile": VIRAL_SELECTION_PROFILE,
        "candidate_count": max(VIRAL_CLIPS_COUNT, VIRAL_CANDIDATE_COUNT),
        "performance_profile_key": (
            performance_profile.cache_key if performance_profile is not None else None
        ),
        "target_clip_duration": target_clip_duration,
    }
    cached_analysis = load_cached_moment_analysis(video_fp, moments_opts=moments_opts)
    selection_candidates: list[dict] | None = None
    if cached_analysis is not None:
        moments = cached_analysis["selected"]
        selection_candidates = cached_analysis["candidates"]
        cache_hits["moments"] = True
        _log.info("[cache] Ranking viral e candidatos já existentes — a saltar análise.")
    else:
        # O namespace legado continua sendo escrito para compatibilidade, mas a versão do
        # analisador invalida listas produzidas antes do ranking ponderado.
        moments = load_cached_moments(video_fp, moments_opts=moments_opts)
    if cached_analysis is None and moments is None:
        raise_if_cancelled()
        analysis = analyze_viral_moments(
            segments,
            output_language=target_language,
            selection_profile=VIRAL_SELECTION_PROFILE,
            performance_profile=performance_profile,
            return_metadata=True,
            target_clip_duration=target_clip_duration,
        )
        if isinstance(analysis, ViralAnalysisResult):
            moments = analysis.selected
            selection_candidates = analysis.candidates
        else:
            # Compatibilidade com implementações/test doubles antigos que retornam uma lista.
            moments = analysis
            selection_candidates = list(analysis)
        _log.info("[3/5] Análise viral concluída (%s momento(s)) — a gravar em cache.", len(moments))
        save_cached_moment_analysis(
            video_fp,
            selected=moments,
            candidates=selection_candidates or moments,
            moments_opts=moments_opts,
        )
        # Mantém também o formato legado para outros processos/versões que só conhecem listas.
        save_cached_moments(video_fp, moments, moments_opts=moments_opts)
        _safe_progress(progress_local, 0.58)
    elif cached_analysis is None:
        cache_hits["moments"] = True
        selection_candidates = list(moments)
        _log.info("[cache] Momentos virais legados já existentes — a saltar análise.")
    if selection_candidates is None:
        selection_candidates = list(moments)
    cache_hits["_selection"] = {
        "profile": VIRAL_SELECTION_PROFILE,
        "performance_profile_used": performance_profile is not None,
        "performance_profile_key": (
            performance_profile.cache_key if performance_profile is not None else None
        ),
        "candidates_considered": selection_candidates,
    }
    if cached_analysis is not None:
        _safe_progress(progress_local, 0.58)

    return video_fp, segments, moments, cache_hits


def _run_clip_stage(
    video_path: str,
    video_name: str,
    video_fp: str,
    segments: list[dict],
    moments: list[dict],
    cache_hits: dict,
    *,
    target_language: str,
    posicao: str,
    fonte: str,
    cor_letra: str,
    cor_fundo: str,
    opacidade: int,
    dub_to: str | None,
    tts_voice: str | None,
    progress_local: Callable[[float], None] | None = None,
    source_attribution: VideoSourceAttribution | None = None,
    outro_text: str | None = None,
) -> list[str]:
    raise_if_cancelled()
    n = len(moments)
    clip_lo = 0.58
    clip_hi = 1.0
    _safe_progress(progress_local, clip_lo)

    max_parallel = pipeline_thread_pool_max_workers()
    workers = max(1, min(n, max_parallel)) if n else 1
    _log.info(
        "[4/5] A gerar %s clipe(s): cortes, legendas no vídeo e textos TikTok em paralelo "
        "(até %s tarefas; CPU/GPU conforme configuração)…",
        n,
        workers,
    )

    if n == 0:
        _safe_progress(progress_local, clip_hi)
        _log.info("[5/5] Concluído — nenhum clipe gerado (sem momentos virais).")
        options = {
            "target_language": target_language,
            "posicao": posicao,
            "fonte": fonte,
            "cor_letra": cor_letra,
            "cor_fundo": cor_fundo,
            "opacidade": opacidade,
            "dub_to": dub_to,
            "tts_voice": tts_voice,
            "outro_text": outro_text,
        }
        manifest = _write_run_manifest(
            video_path=video_path,
            video_name=video_name,
            video_fp=video_fp,
            options=options,
            cache_hits=cache_hits,
            moments=moments,
            outputs=[],
        )
        if manifest:
            _log.info("Manifest salvo: %s", manifest)
        return []

    precomputed_se: tuple[list[float], list[float]] | None = None
    if segments:
        precomputed_se = (
            [float(s["start"]) for s in segments],
            [float(s["end"]) for s in segments],
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futs: dict[Future, int] = {}
        for clip_index in range(1, n + 1):
            fut = executor.submit(
                _process_clip_task,
                clip_index,
                n,
                moments[clip_index - 1],
                video_path,
                video_name,
                segments,
                video_fp,
                target_language,
                posicao,
                fonte,
                cor_letra,
                cor_fundo,
                opacidade,
                dub_to,
                tts_voice,
                outro_text,
                source_attribution,
                precomputed_se,
            )
            futs[fut] = clip_index
        results_by_idx: dict[int, str] = {}
        done = 0
        span = clip_hi - clip_lo
        for fut in as_completed(futs):
            idx = futs[fut]
            results_by_idx[idx] = fut.result()
            done += 1
            _safe_progress(progress_local, clip_lo + span * (done / n))
        final_videos = [results_by_idx[i] for i in range(1, n + 1)]

    _safe_progress(progress_local, clip_hi)
    _log.info("[5/5] Tudo concluído — clipes e legendas .txt prontos.")

    options = {
        "target_language": target_language,
        "posicao": posicao,
        "fonte": fonte,
        "cor_letra": cor_letra,
        "cor_fundo": cor_fundo,
        "opacidade": opacidade,
        "dub_to": dub_to,
        "tts_voice": tts_voice,
        "outro_text": outro_text,
    }
    manifest = _write_run_manifest(
        video_path=video_path,
        video_name=video_name,
        video_fp=video_fp,
        options=options,
        cache_hits=cache_hits,
        moments=moments,
        outputs=final_videos,
    )
    if manifest:
        _log.info("Manifest salvo: %s", manifest)
    return final_videos


def _run_single_pipeline(
    video_path: str,
    video_name_override: str | None = None,
    target_language: str = "pt",
    posicao: str = "bottom",
    fonte: str = "Arial",
    cor_letra: str = "#FFFF00",
    cor_fundo: str = "#000000",
    opacidade: int = 75,
    dub_to: str | None = None,
    tts_voice: str | None = None,
    progress_local: Callable[[float], None] | None = None,
    source_by_path: dict[str, VideoSourceAttribution] | None = None,
    manual_start: float | None = None,
    manual_end: float | None = None,
    hook_text: str | None = None,
    outro_text: str | None = None,
) -> list[str]:
    raise_if_cancelled()
    video_name = (video_name_override or Path(video_path).stem).strip() or Path(video_path).stem
    _log.info("A processar: %s", video_path)
    _safe_progress(progress_local, 0.0)
    video_fp, segments, moments, cache_hits = _prepare_transcription_and_moments(
        video_path,
        video_name,
        target_language,
        progress_local=progress_local,
        manual_start=manual_start,
        manual_end=manual_end,
        hook_text=hook_text,
    )
    source_attribution = lookup_source_attribution(video_path, source_by_path)
    return _run_clip_stage(
        video_path,
        video_name,
        video_fp,
        segments,
        moments,
        cache_hits,
        target_language=target_language,
        posicao=posicao,
        fonte=fonte,
        cor_letra=cor_letra,
        cor_fundo=cor_fundo,
        opacidade=opacidade,
        dub_to=dub_to,
        tts_voice=tts_voice,
        progress_local=progress_local,
        source_attribution=source_attribution,
        outro_text=outro_text,
    )


def _expand_long_video_inputs(
    video_paths: list[str],
    source_by_path: dict[str, VideoSourceAttribution] | None,
) -> tuple[list[str], dict[str, VideoSourceAttribution] | None, Path | None]:
    """Expande cada fonte longa em blocos completos antes da preparação do pipeline."""
    chunk_root = TEMP_DIR / f"_long_video_chunks_{uuid.uuid4().hex[:12]}"
    expanded_paths: list[str] = []
    expanded_sources: dict[str, VideoSourceAttribution] = {}
    was_split = False

    try:
        for source_index, video in enumerate(video_paths, start=1):
            raise_if_cancelled()
            result = split_video_into_chunks(
                video,
                chunk_root / f"source_{source_index:03d}",
            )
            if result.was_split:
                was_split = True
                remainder_min = result.discarded_remainder_sec / 60.0
                _log.info(
                    "Vídeo longo (%.1f min): dividido em %s bloco(s) de 20 min; "
                    "%.1f min restantes descartados.",
                    result.source_duration_sec / 60.0,
                    len(result.paths),
                    remainder_min,
                )
            expanded_paths.extend(result.paths)

            attribution = lookup_source_attribution(video, source_by_path)
            if attribution:
                for path in result.paths:
                    expanded_sources[str(Path(path).resolve())] = attribution
    except Exception:
        cleanup_split_directory(chunk_root)
        raise

    if not was_split:
        cleanup_split_directory(chunk_root)

    return expanded_paths, (expanded_sources or None), (chunk_root if was_split else None)


def _run_pipeline_expanded(
    video_path: str | list[str] | tuple[str, ...],
    target_language: str = "pt",
    posicao: str = "bottom",
    fonte: str = "Arial",
    cor_letra: str = "#FFFF00",
    cor_fundo: str = "#000000",
    opacidade: int = 75,
    dub_to: str | None = None,
    tts_voice: str | None = None,
    progress: Callable[[float], None] | None = None,
    source_by_path: dict[str, VideoSourceAttribution] | None = None,
    manual_start: float | None = None,
    manual_end: float | None = None,
    hook_text: str | None = None,
    outro_text: str | None = None,
) -> list[str]:
    """
    Aceita 1 vídeo (str) ou uma lista/tupla de vídeos.

    Com múltiplos vídeos, a preparação (transcrição + momentos) do próximo arquivo
    pode rodar em paralelo com a etapa de clipes do vídeo atual.

    source_by_path: mapa caminho absoluto do vídeo → metadados do canal (ex.: após yt-dlp).
    """
    if isinstance(video_path, (list, tuple)):
        videos = [str(v).strip() for v in video_path if str(v).strip()]
        if not videos:
            return []

        out: list[str] = []
        total = len(videos)
        prep_future: Future | None = None
        used_names: dict[str, int] = {}
        video_names: list[str] = []
        for vp in videos:
            stem = Path(vp).stem
            count = used_names.get(stem, 0) + 1
            used_names[stem] = count
            suffix = f"__{count}" if count > 1 else ""
            video_names.append(f"{stem}{suffix}")

        def _scoped_progress(video_index: int) -> Callable[[float], None]:
            def scope_local(t: float) -> None:
                _safe_progress(progress, (video_index + t) / total)

            return scope_local

        with ThreadPoolExecutor(max_workers=2) as prep_pool:
            for i, vp in enumerate(videos):
                video_name_override = video_names[i]

                _log.info(
                    "Fila: vídeo %s de %s — %s",
                    i + 1,
                    total,
                    vp,
                )

                try:
                    if prep_future is None:
                        video_fp, segments, moments, cache_hits = _prepare_transcription_and_moments(
                            vp,
                            video_name_override,
                            target_language,
                            progress_local=_scoped_progress(i),
                            manual_start=manual_start,
                            manual_end=manual_end,
                            hook_text=hook_text,
                        )
                    else:
                        video_fp, segments, moments, cache_hits = prep_future.result()
                except Exception as exc:
                    raise RuntimeError(
                        f"Falha ao preparar o vídeo/bloco {i + 1}/{total}: {vp}\n{exc}"
                    ) from exc

                if i + 1 < len(videos):
                    vp_next = videos[i + 1]
                    prep_future = prep_pool.submit(
                        _prepare_transcription_and_moments,
                        vp_next,
                        video_names[i + 1],
                        target_language,
                        _scoped_progress(i + 1),
                        manual_start,
                        manual_end,
                        hook_text,
                    )
                else:
                    prep_future = None

                try:
                    out.extend(
                        _run_clip_stage(
                            vp,
                            video_name_override,
                            video_fp,
                            segments,
                            moments,
                            cache_hits,
                            target_language=target_language,
                            posicao=posicao,
                            fonte=fonte,
                            cor_letra=cor_letra,
                            cor_fundo=cor_fundo,
                            opacidade=opacidade,
                            dub_to=dub_to,
                            tts_voice=tts_voice,
                            progress_local=_scoped_progress(i),
                            source_attribution=lookup_source_attribution(vp, source_by_path),
                            outro_text=outro_text,
                        )
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Falha ao gerar os cortes do vídeo/bloco {i + 1}/{total}: {vp}\n{exc}"
                    ) from exc
        return out

    return _run_single_pipeline(
        video_path=str(video_path),
        video_name_override=None,
        target_language=target_language,
        posicao=posicao,
        fonte=fonte,
        cor_letra=cor_letra,
        cor_fundo=cor_fundo,
        opacidade=opacidade,
        dub_to=dub_to,
        tts_voice=tts_voice,
        progress_local=progress,
        source_by_path=source_by_path,
        manual_start=manual_start,
        manual_end=manual_end,
        hook_text=hook_text,
        outro_text=outro_text,
    )


def run_pipeline(
    video_path: str | list[str] | tuple[str, ...],
    target_language: str = "pt",
    posicao: str = "bottom",
    fonte: str = "Arial",
    cor_letra: str = "#FFFF00",
    cor_fundo: str = "#000000",
    opacidade: int = 75,
    dub_to: str | None = None,
    tts_voice: str | None = None,
    progress: Callable[[float], None] | None = None,
    source_by_path: dict[str, VideoSourceAttribution] | None = None,
    manual_start: float | None = None,
    manual_end: float | None = None,
    hook_text: str | None = None,
    outro_text: str | None = None,
) -> list[str]:
    """Executa o pipeline, particionando fontes acima de 20 minutos primeiro."""
    reset_cancel()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    is_sequence = isinstance(video_path, (list, tuple))
    if is_sequence:
        input_paths = [str(v).strip() for v in video_path if str(v).strip()]
    else:
        input_paths = [str(video_path).strip()]
    if not input_paths:
        return []

    expanded_paths: list[str] = []
    expanded_sources: dict[str, VideoSourceAttribution] | None = None
    chunk_root: Path | None = None
    try:
        expanded_paths, expanded_sources, chunk_root = _expand_long_video_inputs(
            input_paths,
            source_by_path,
        )
        pipeline_input: str | list[str]
        if is_sequence or len(expanded_paths) != 1:
            pipeline_input = expanded_paths
        else:
            pipeline_input = expanded_paths[0]
        return _run_pipeline_expanded(
            video_path=pipeline_input,
            target_language=target_language,
            posicao=posicao,
            fonte=fonte,
            cor_letra=cor_letra,
            cor_fundo=cor_fundo,
            opacidade=opacidade,
            dub_to=dub_to,
            tts_voice=tts_voice,
            progress=progress,
            source_by_path=expanded_sources,
            manual_start=manual_start,
            manual_end=manual_end,
            hook_text=hook_text,
            outro_text=outro_text,
        )
    finally:
        if chunk_root is not None:
            cleanup_split_directory(chunk_root)
