import os
import platform
import subprocess
from pathlib import Path

from app.core.cache import fingerprint_file
from app.core.cache_pipeline import crop_plan_cache_opts, load_cached_crop_plan, save_cached_crop_plan
from app.core.config import (
    CLIP_SPEED_UP_PERCENT,
    FFMPEG_PATH,
    OUTPUT_VIDEO_HEIGHT,
    OUTPUT_VIDEO_WIDTH,
    SMART_CROP_ENABLED,
    TIKTOK_SUBTITLE_FONT_SIZE,
    TIKTOK_SUBTITLE_MARGIN_LR,
    TIKTOK_SUBTITLE_MARGIN_V,
    clip_gpu_uses_vaapi,
    clip_ffmpeg_threads_args,
    ffmpeg_vaapi_hwdevice_args,
    ffmpeg_vaapi_vf_hwupload_suffix,
    gpu_clip_encoder_ffmpeg_args,
)
from app.core.subprocess_utils import run_cancelable
from app.subtitle.ass_builder import write_tiktok_ass_from_srt, write_tiktok_ass_karaoke_from_srt
from app.video_processing.focal_crop import compute_crop_plan

# Gancho visual: visível só enquanto t < este valor (some exatamente em t = 3s).
HOOK_VISIBLE_UNTIL_SEC = 3.0
# Margem extra no rodapé: evita sobrepor UI do TikTok (nome do perfil, legenda do post, botões).
SUBTITLE_BOTTOM_MARGIN_MULTIPLIER = 2.0
SUBTITLE_RAISE_FACTOR = 1.55
HOOK_TOP_Y_MULTIPLIER = 2.0

# CTA "siga o perfil": topo do texto ~20% abaixo do topo; visível de 13s a 15s no clipe.
FOLLOW_CTA_START_SEC = 13.0
FOLLOW_CTA_END_SEC = 15.0
FOLLOW_CTA_TOP_FRACTION = 0.2
FOLLOW_CTA_FONT_SCALE = 0.88  # em relação à legenda TikTok (hook visual, não domina o quadro)


def _follow_profile_cta_text(target_language: str) -> str:
    lang = (target_language or "pt").strip().lower()
    if lang == "en":
        return "Follow our profile for more videos like this"
    return "Siga nosso perfil para mais vídeos como esse"


def _hex_to_ass_primary(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _hex_to_ass_back(hex_color: str, opacidade: int) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    alpha = int((1.0 - opacidade / 100.0) * 255)
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _escape_srt_path(path: str) -> str:
    abs_path = os.path.abspath(path).replace("\\", "/")
    if platform.system() == "Windows" and len(abs_path) >= 2 and abs_path[1] == ":":
        abs_path = abs_path[0] + "\\:" + abs_path[2:]
    return abs_path


def _hex_to_drawtext_color(hex_color: str) -> str:
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return "white"
    return f"0x{h.upper()}"


def _drawtext_box_alpha(opacidade: int) -> float:
    return max(0.0, min(1.0, opacidade / 100.0))


def _resolve_drawtext_fontfile(fonte: str) -> str | None:
    if platform.system() != "Windows":
        return None
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if not fonts_dir.is_dir():
        return None
    stem = fonte.strip()
    compact = stem.replace(" ", "")
    for p in fonts_dir.iterdir():
        if p.suffix.lower() not in (".ttf", ".otf"):
            continue
        n = p.stem.lower()
        if n == stem.lower() or n == compact.lower():
            return str(p.resolve())
    return None


def _escape_filter_single_quoted(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", r"\'")


def _prepare_scale_crop_overlay_vf(
    video_path: str,
    srt_path: str,
    posicao: str,
    fonte: str,
    cor_letra: str,
    cor_fundo: str,
    opacidade: int,
    hook_phrase: str | None,
    target_language: str,
    *,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> tuple[str, str, Path | None, Path]:
    """
    Monta filter chain (scale/crop/ASS + drawtext). Gera .ass e arquivos auxiliares no disco.
    """
    from app.core.config import TIKTOK_SUBTITLE_FONT
    if fonte in (None, "", "Arial"):
        fonte = TIKTOK_SUBTITLE_FONT
    alignment = 2 if posicao == "bottom" else 8
    primary = _hex_to_ass_primary(cor_letra)
    back = _hex_to_ass_back(cor_fundo, opacidade)

    w, h = OUTPUT_VIDEO_WIDTH, OUTPUT_VIDEO_HEIGHT
    fs = TIKTOK_SUBTITLE_FONT_SIZE
    mv = TIKTOK_SUBTITLE_MARGIN_V
    mlr = TIKTOK_SUBTITLE_MARGIN_LR
    subtitle_margin_v = (
        int(round(mv * SUBTITLE_BOTTOM_MARGIN_MULTIPLIER)) if alignment == 2 else mv
    )
    subtitle_margin_v = int(round(subtitle_margin_v * SUBTITLE_RAISE_FACTOR))

    hook_file: Path | None = None
    hook_clean = (hook_phrase or "").strip()
    if hook_clean:
        hook_file = Path(srt_path).with_suffix(".hook.txt")
        hook_file.write_text(hook_clean.replace("\n", " ").replace("\r", " "), encoding="utf-8")

    ass_path = str(Path(srt_path).with_suffix(".ass"))
    write_tiktok_ass_from_srt(
        srt_path,
        ass_path,
        play_res_x=w,
        play_res_y=h,
        font_name=fonte,
        font_size=fs,
        primary_ass=primary,
        back_ass=back,
        margin_l=mlr,
        margin_r=mlr,
        margin_v=subtitle_margin_v,
        alignment=alignment,
    )
    escaped = _escape_srt_path(ass_path)

    scale_crop = f"scale={w}:{h}:force_original_aspect_ratio=increase"
    if SMART_CROP_ENABLED:
        vf_fp = fingerprint_file(video_path)
        crop_opts: dict = {**crop_plan_cache_opts()}
        if clip_start is not None and clip_end is not None:
            crop_opts["clip_start"] = round(float(clip_start), 3)
            crop_opts["clip_end"] = round(float(clip_end), 3)
        plan = load_cached_crop_plan(video_fp=vf_fp, opts=crop_opts)
        if plan is None:
            if clip_start is not None and clip_end is not None:
                plan = compute_crop_plan(
                    video_path, w, h, clip_start=float(clip_start), clip_end=float(clip_end)
                )
            else:
                plan = compute_crop_plan(video_path, w, h)
            if plan is not None:
                save_cached_crop_plan(video_fp=vf_fp, opts=crop_opts, plan=plan)
        if plan is not None:
            if plan["mode"] == "static":
                scale_crop += f",crop={w}:{h}:{plan['x']}:{plan['y']}"
            else:
                scale_crop += f",crop={w}:{h}:{plan['x_expr']}:{plan['y_expr']}"
        else:
            scale_crop += f",crop={w}:{h}"
    else:
        scale_crop += f",crop={w}:{h}"

    box_a = _drawtext_box_alpha(opacidade)
    hook_vf = ""
    if hook_file is not None:
        hook_path_esc = _escape_srt_path(str(hook_file))
        hook_fs = min(int(round(fs * 1.35)), 68)
        fg = _hex_to_drawtext_color(cor_letra)
        fontfile = _resolve_drawtext_fontfile(fonte)
        font_clause = (
            f"fontfile='{_escape_srt_path(fontfile)}'"
            if fontfile
            else f"font='{_escape_filter_single_quoted(fonte)}'"
        )
        enable_expr = f"gte(t\\,0)*lt(t\\,{HOOK_VISIBLE_UNTIL_SEC})"
        hook_y = int(round(56 * HOOK_TOP_Y_MULTIPLIER))
        hook_vf = (
            f",drawtext=textfile='{hook_path_esc}':{font_clause}:fontsize={hook_fs}:"
            f"fontcolor={fg}:box=1:boxcolor=black@{box_a:.3f}:boxborderw=14:"
            f"x=(w-text_w)/2:y={hook_y}:borderw=2:bordercolor=black@0.6:"
            f"enable='{enable_expr}'"
        )

    cta_end = FOLLOW_CTA_END_SEC
    cta_enable = f"gte(t\\,{FOLLOW_CTA_START_SEC})*lt(t\\,{cta_end})"
    cta_text = _follow_profile_cta_text(target_language)
    cta_file = Path(srt_path).with_suffix(".follow_cta.txt")
    cta_file.write_text(cta_text.replace("\n", " ").replace("\r", " "), encoding="utf-8")
    cta_path_esc = _escape_srt_path(str(cta_file))
    cta_fs = max(
        26,
        min(
            int(round(fs * FOLLOW_CTA_FONT_SCALE)),
            44,
        ),
    )
    cta_y = int(round(h * FOLLOW_CTA_TOP_FRACTION + cta_fs * 0.78))
    cta_fg = _hex_to_drawtext_color(cor_letra)
    cta_fontfile = _resolve_drawtext_fontfile(fonte)
    cta_font_clause = (
        f"fontfile='{_escape_srt_path(cta_fontfile)}'"
        if cta_fontfile
        else f"font='{_escape_filter_single_quoted(fonte)}'"
    )
    cta_vf = (
        f",drawtext=textfile='{cta_path_esc}':{cta_font_clause}:fontsize={cta_fs}:"
        f"fontcolor={cta_fg}:box=1:boxcolor=black@{box_a:.3f}:boxborderw=10:"
        f"x=(w-text_w)/2:y={cta_y}:borderw=2:bordercolor=black@0.55:"
        f"enable='{cta_enable}'"
    )

    from app.core.config import FONTS_DIR
    fonts_clause = f":fontsdir='{_escape_srt_path(FONTS_DIR)}'"
    vf = f"{scale_crop},subtitles='{escaped}'{fonts_clause}{hook_vf}{cta_vf}"
    return vf, ass_path, hook_file, cta_file


def _unlink_burn_sidecars(ass_path: str, hook_file: Path | None, cta_file: Path) -> None:
    p = Path(ass_path)
    if p.exists():
        p.unlink()
    if hook_file is not None and hook_file.exists():
        hook_file.unlink()
    if cta_file.exists():
        cta_file.unlink()


def burn_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str,
    posicao: str = "bottom",
    fonte: str = "Arial",
    cor_letra: str = "#FFFF00",
    cor_fundo: str = "#000000",
    opacidade: int = 75,
    hook_phrase: str | None = None,
    target_language: str = "pt",
    *,
    use_gpu_encoder: bool = False,
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    vf, ass_path, hook_file, cta_file = _prepare_scale_crop_overlay_vf(
        video_path,
        srt_path,
        posicao,
        fonte,
        cor_letra,
        cor_fundo,
        opacidade,
        hook_phrase,
        target_language,
    )

    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    venc: list[str] = gpu_clip_encoder_ffmpeg_args() if use_gpu_encoder else cpu_venc
    th = clip_ffmpeg_threads_args(use_gpu_encoder=use_gpu_encoder)
    va_pre = ffmpeg_vaapi_hwdevice_args() if (use_gpu_encoder and clip_gpu_uses_vaapi()) else []
    vf_full = vf + ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu_encoder)

    cmd = [
        FFMPEG_PATH,
        *va_pre,
        *th,
        "-i",
        video_path,
        "-vf",
        vf_full,
        *venc,
        "-c:a",
        "copy",
        output_path,
        "-y",
    ]

    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        if use_gpu_encoder:
            cmd_cpu = [
                FFMPEG_PATH,
                *th,
                "-i",
                video_path,
                "-vf",
                vf,
                *cpu_venc,
                "-c:a",
                "copy",
                output_path,
                "-y",
            ]
            try:
                run_cancelable(cmd_cpu, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e2:
                detail2 = (e2.stderr or e2.stdout or "").strip()
                if detail2:
                    raise RuntimeError(
                        f"FFmpeg falhou ao queimar legendas (fallback CPU): {detail2}"
                    ) from e2
                raise
        else:
            detail = (e.stderr or e.stdout or "").strip()
            if detail:
                raise RuntimeError(f"FFmpeg falhou ao queimar legendas: {detail}") from e
            raise
    finally:
        _unlink_burn_sidecars(ass_path, hook_file, cta_file)
    return output_path


def cut_and_burn_subtitles(
    source_video_path: str,
    clip_start: float,
    clip_end: float,
    srt_path: str,
    output_path: str,
    posicao: str = "bottom",
    fonte: str = "Arial",
    cor_letra: str = "#FFFF00",
    cor_fundo: str = "#000000",
    opacidade: int = 75,
    hook_phrase: str | None = None,
    target_language: str = "pt",
    *,
    use_gpu_encoder: bool = False,
) -> str:
    """Único passe FFmpeg: corte + filtros de velocidade + escala/crop + legendas/CTA no vídeo fonte."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    duration = float(clip_end) - float(clip_start)
    tempo = 1.0 + CLIP_SPEED_UP_PERCENT / 100.0
    vf_cut = f"noise=alls=1:allf=t+u,eq=brightness=0.01,setpts=PTS/{tempo}"

    vf_overlay, ass_path, hook_file, cta_file = _prepare_scale_crop_overlay_vf(
        source_video_path,
        srt_path,
        posicao,
        fonte,
        cor_letra,
        cor_fundo,
        opacidade,
        hook_phrase,
        target_language,
        clip_start=clip_start,
        clip_end=clip_end,
    )
    vf = f"{vf_cut},{vf_overlay}"
    af = f"atempo={tempo}"

    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    venc: list[str] = gpu_clip_encoder_ffmpeg_args() if use_gpu_encoder else cpu_venc
    th = clip_ffmpeg_threads_args(use_gpu_encoder=use_gpu_encoder)
    va_pre = ffmpeg_vaapi_hwdevice_args() if (use_gpu_encoder and clip_gpu_uses_vaapi()) else []
    vf_full = vf + ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu_encoder)

    cmd_base = [
        FFMPEG_PATH,
        *va_pre,
        "-fflags",
        "+bitexact",
        *th,
        "-ss",
        str(clip_start),
        "-i",
        source_video_path,
        "-t",
        str(duration),
        "-map_metadata",
        "-1",
        "-vf",
        vf_full,
        "-af",
        af,
        "-c:a",
        "aac",
        "-avoid_negative_ts",
        "1",
        "-y",
        output_path,
    ]
    cmd = [*cmd_base[: cmd_base.index("-c:a")], *venc, *cmd_base[cmd_base.index("-c:a") :]]

    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        if use_gpu_encoder:
            cmd_cpu = [
                FFMPEG_PATH,
                "-fflags",
                "+bitexact",
                *th,
                "-ss",
                str(clip_start),
                "-i",
                source_video_path,
                "-t",
                str(duration),
                "-map_metadata",
                "-1",
                "-vf",
                vf,
                "-af",
                af,
                *cpu_venc,
                "-c:a",
                "aac",
                "-avoid_negative_ts",
                "1",
                "-y",
                output_path,
            ]
            try:
                run_cancelable(cmd_cpu, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e2:
                detail = (e2.stderr or e2.stdout or "").strip()
                if detail:
                    raise RuntimeError(
                        f"FFmpeg falhou ao cortar/queimar (fallback CPU): {detail}"
                    ) from e2
                raise
        else:
            detail = (e.stderr or e.stdout or "").strip()
            if detail:
                raise RuntimeError(f"FFmpeg falhou ao cortar/queimar legendas: {detail}") from e
            raise
    finally:
        _unlink_burn_sidecars(ass_path, hook_file, cta_file)
    return output_path
