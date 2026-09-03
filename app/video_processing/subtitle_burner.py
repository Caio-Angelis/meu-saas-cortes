import os
import platform
import subprocess
import textwrap
from pathlib import Path

from app.core.cache import fingerprint_file
from app.core.cache_pipeline import (
    crop_plan_cache_opts,
    load_cached_crop_plan,
    save_cached_crop_plan,
)
from app.core.config import (
    CLIP_SPEED_UP_PERCENT,
    FFMPEG_PATH,
    OUTPUT_VIDEO_HEIGHT,
    OUTPUT_VIDEO_WIDTH,
    OUTRO_CARD_DURATION_SEC,
    SMART_CROP_ENABLED,
    TIKTOK_SUBTITLE_FONT_SIZE,
    TIKTOK_SUBTITLE_MARGIN_LR,
    TIKTOK_SUBTITLE_MARGIN_V,
    clip_ffmpeg_threads_args,
    clip_gpu_uses_vaapi,
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


def _follow_profile_cta_text(target_language: str, cta_text: str | None = None) -> str:
    if cta_text is not None:
        clean = " ".join(str(cta_text).replace("\n", " ").replace("\r", " ").split())
        if not clean:
            return ""
        return " ".join(clean.split()[:8])
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


def _wrap_outro_text(text: str, width: int = 34) -> str:
    """Quebra a tela final em linhas legíveis sem alterar o texto editorial."""
    paragraphs = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for paragraph in paragraphs:
        clean = " ".join(paragraph.split())
        if not clean:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.extend(
            textwrap.wrap(
                clean,
                width=width,
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [clean]
        )
    return "\n".join(lines).strip()


def _has_audio_stream(video_path: str) -> bool:
    from app.core.config import FFPROBE_PATH

    try:
        result = subprocess.run(
            [
                FFPROBE_PATH,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return bool((result.stdout or "").strip())


def append_outro_card(
    input_path: str,
    output_path: str,
    outro_text: str,
    *,
    duration_sec: float | None = None,
) -> str:
    """Concatena uma tela final própria ao clipe, mantendo o áudio original."""
    clean = _wrap_outro_text(outro_text)
    if not clean:
        return input_path

    from app.core.config import FONTS_DIR

    duration = max(
        1.5,
        min(8.0, float(duration_sec if duration_sec is not None else OUTRO_CARD_DURATION_SEC)),
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    text_file = output.with_suffix(".outro.txt")
    text_file.write_text(clean, encoding="utf-8")
    text_path = _escape_srt_path(str(text_file))
    font_path = Path(FONTS_DIR) / "Montserrat-Bold.ttf"
    font_clause = (
        f"fontfile='{_escape_srt_path(str(font_path))}'"
        if font_path.is_file()
        else "font='Montserrat'"
    )
    card_input = (
        f"color=c=0x0D1226:s={OUTPUT_VIDEO_WIDTH}x{OUTPUT_VIDEO_HEIGHT}:"
        f"r=30:d={duration:.3f}"
    )
    card_video = (
        f"[1:v]drawbox=x=88:y=250:w={OUTPUT_VIDEO_WIDTH - 176}:h=8:"
        "color=0x22D3EE@0.95:t=fill,"
        f"drawtext=text='BENDIFY':{font_clause}:fontsize=58:fontcolor=0x22D3EE:"
        "x=(w-text_w)/2:y=285:borderw=2:bordercolor=0x07101F@0.8,"
        f"drawtext=textfile='{text_path}':{font_clause}:fontsize=44:"
        "fontcolor=white:line_spacing=16:box=1:boxcolor=black@0.18:boxborderw=30:"
        "x=(w-text_w)/2:y=(h-text_h)/2[vcard]"
    )

    has_audio = _has_audio_stream(input_path)
    if has_audio:
        filter_complex = (
            f"{card_video};"
            "[0:v]setpts=PTS-STARTPTS,setsar=1[vmain];"
            "[0:a]aresample=async=1:first_pts=0[amain];"
            f"[2:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[acard];"
            "[vmain][amain][vcard][acard]concat=n=2:v=1:a=1[v][a]"
        )
        map_args = [
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
        ]
    else:
        filter_complex = (
            f"{card_video};"
            "[0:v]setpts=PTS-STARTPTS,setsar=1[vmain];"
            "[vmain][vcard]concat=n=2:v=1:a=0[v]"
        )
        map_args = ["-map", "[v]"]

    cmd = [
        FFMPEG_PATH,
        "-i",
        input_path,
        "-f",
        "lavfi",
        "-t",
        f"{duration:.3f}",
        "-i",
        card_input,
        "-f",
        "lavfi",
        "-t",
        f"{duration:.3f}",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-filter_complex",
        filter_complex,
        *map_args,
        "-map_metadata",
        "-1",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        output_path,
    ]
    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
    finally:
        try:
            text_file.unlink()
        except OSError:
            pass
    return output_path


def _ffprobe_frame_size(video_path: str) -> tuple[int, int] | None:
    """Largura×altura do primeiro stream de vídeo via ffprobe, ou None."""
    from app.core.config import FFPROBE_PATH

    try:
        r = subprocess.run(
            [
                FFPROBE_PATH,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        parts = (r.stdout or "").strip().split("x")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return None


def _band_crop_xy(
    cx: float,
    cy: float,
    src_w: int,
    src_h: int,
    crop_w: int,
    crop_h: int,
) -> tuple[int, int, int, int]:
    """Janela (cw, ch, x, y) centrada em (cx, cy), clampada ao quadro fonte."""
    cw = min(int(crop_w), int(src_w))
    ch = min(int(crop_h), int(src_h))
    x = int(round(cx - cw / 2))
    y = int(round(cy - ch / 2))
    x = max(0, min(x, src_w - cw))
    y = max(0, min(y, src_h - ch))
    return cw, ch, x, y


def _build_split_vstack_graph(
    left: tuple[float, float],
    right: tuple[float, float],
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    *,
    input_label: str = "0:v",
) -> str:
    """
    Filtergraph: left → faixa superior, right → inferior, vstack → [vsplit].
    Centros em pixels da fonte; crop clampado aos bounds da fonte.
    """
    half = out_h // 2
    lx, ly = float(left[0]), float(left[1])
    rx, ry = float(right[0]), float(right[1])
    cw_t, ch_t, tx, ty = _band_crop_xy(lx, ly, src_w, src_h, out_w, half)
    cw_b, ch_b, bx, by = _band_crop_xy(rx, ry, src_w, src_h, out_w, half)
    return (
        f"[{input_label}]crop={cw_t}:{ch_t}:{tx}:{ty},"
        f"scale={out_w}:{half}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{half},setsar=1[top];"
        f"[{input_label}]crop={cw_b}:{ch_b}:{bx}:{by},"
        f"scale={out_w}:{half}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{half},setsar=1[bot];"
        f"[top][bot]vstack=inputs=2[vsplit]"
    )


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
    cta_text: str | None = None,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> tuple[str, str, Path | None, Path, bool]:
    """
    Monta filter chain (scale/crop/ASS + drawtext). Gera .ass e arquivos auxiliares no disco.

    Retorna (vf_or_fc, ass_path, hook_file, cta_file, is_filter_complex).
    Se is_filter_complex, a string é um -filter_complex que termina em [vout].
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
        hook_file.write_text(
            _wrap_outro_text(hook_clean.replace("\n", " ").replace("\r", " "), width=28),
            encoding="utf-8",
        )

    from app.core.config import SUBTITLE_KARAOKE, SUBTITLE_KARAOKE_HIGHLIGHT
    ass_path = str(Path(srt_path).with_suffix(".ass"))
    if SUBTITLE_KARAOKE:
        write_tiktok_ass_karaoke_from_srt(
            srt_path,
            ass_path,
            play_res_x=w,
            play_res_y=h,
            font_name=fonte,
            font_size=fs,
            highlight_hex=SUBTITLE_KARAOKE_HIGHLIGHT,
            margin_l=mlr,
            margin_r=mlr,
            margin_v=subtitle_margin_v,
            alignment=alignment,
        )
    else:
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

    split_plan: dict | None = None
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
            elif plan["mode"] == "split":
                split_plan = plan
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
    resolved_cta_text = _follow_profile_cta_text(target_language, cta_text)
    cta_file = Path(srt_path).with_suffix(".follow_cta.txt")
    cta_file.write_text(resolved_cta_text, encoding="utf-8")
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
    cta_vf = ""
    if resolved_cta_text:
        cta_vf = (
            f",drawtext=textfile='{cta_path_esc}':{cta_font_clause}:fontsize={cta_fs}:"
            f"fontcolor={cta_fg}:box=1:boxcolor=black@{box_a:.3f}:boxborderw=10:"
            f"x=(w-text_w)/2:y={cta_y}:borderw=2:bordercolor=black@0.55:"
            f"enable='{cta_enable}'"
        )

    from app.core.config import FONTS_DIR
    fonts_clause = f":fontsdir='{_escape_srt_path(FONTS_DIR)}'"
    overlay_tail = f"subtitles='{escaped}'{fonts_clause}{hook_vf}{cta_vf}"

    if split_plan is not None:
        src_w = int(split_plan.get("src_w") or 0)
        src_h = int(split_plan.get("src_h") or 0)
        if src_w <= 0 or src_h <= 0:
            probed = _ffprobe_frame_size(video_path)
            if probed is not None:
                src_w, src_h = probed
        if src_w <= 0 or src_h <= 0:
            # Sem tamanho da fonte: cai no cover estático (evita KeyError / crop inválido).
            vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},{overlay_tail}"
            return vf, ass_path, hook_file, cta_file, False
        left = tuple(split_plan["left"])
        right = tuple(split_plan["right"])
        split_graph = _build_split_vstack_graph(
            (float(left[0]), float(left[1])),
            (float(right[0]), float(right[1])),
            src_w,
            src_h,
            w,
            h,
        )
        fc = f"{split_graph};[vsplit]{overlay_tail}[vout]"
        return fc, ass_path, hook_file, cta_file, True

    vf = f"{scale_crop},{overlay_tail}"
    return vf, ass_path, hook_file, cta_file, False


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
    cta_text: str | None = None,
    outro_text: str | None = None,
    use_gpu_encoder: bool = False,
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    vf, ass_path, hook_file, cta_file, is_fc = _prepare_scale_crop_overlay_vf(
        video_path,
        srt_path,
        posicao,
        fonte,
        cor_letra,
        cor_fundo,
        opacidade,
        hook_phrase,
        target_language,
        cta_text=cta_text,
    )

    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    # NVENC aceita os frames produzidos pelo filter_complex; VA-API ainda precisa
    # do hwupload que a variante split não encadeia.
    use_gpu = bool(use_gpu_encoder) and not (is_fc and clip_gpu_uses_vaapi())
    venc: list[str] = gpu_clip_encoder_ffmpeg_args() if use_gpu else cpu_venc
    th = clip_ffmpeg_threads_args(use_gpu_encoder=use_gpu)
    va_pre = ffmpeg_vaapi_hwdevice_args() if (use_gpu and clip_gpu_uses_vaapi()) else []

    if is_fc:
        cmd = [
            FFMPEG_PATH,
            *th,
            "-i",
            video_path,
            "-filter_complex",
            vf,
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            *venc,
            "-c:a",
            "copy",
            output_path,
            "-y",
        ]
    else:
        vf_full = vf + ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu)
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
        if use_gpu:
            cpu_filter = ["-filter_complex", vf, "-map", "[vout]", "-map", "0:a?"] if is_fc else ["-vf", vf]
            cmd_cpu = [
                FFMPEG_PATH,
                *clip_ffmpeg_threads_args(use_gpu_encoder=False),
                "-i",
                video_path,
                *cpu_filter,
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
    cta_text: str | None = None,
    outro_text: str | None = None,
    use_gpu_encoder: bool = False,
) -> str:
    """Único passe FFmpeg: corte + filtros de velocidade + escala/crop + legendas/CTA no vídeo fonte."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    duration = float(clip_end) - float(clip_start)
    tempo = 1.0 + CLIP_SPEED_UP_PERCENT / 100.0
    vf_cut = f"noise=alls=1:allf=t+u,eq=brightness=0.01,setpts=PTS/{tempo}"
    outro_clean = (outro_text or "").strip()
    render_path = output_path
    if outro_clean:
        out = Path(output_path)
        render_path = str(out.with_name(f"{out.stem}__main{out.suffix}"))
        try:
            Path(render_path).unlink()
        except OSError:
            pass

    vf_overlay, ass_path, hook_file, cta_file, is_fc = _prepare_scale_crop_overlay_vf(
        source_video_path,
        srt_path,
        posicao,
        fonte,
        cor_letra,
        cor_fundo,
        opacidade,
        hook_phrase,
        target_language,
        cta_text=cta_text,
        clip_start=clip_start,
        clip_end=clip_end,
    )
    from app.core.config import (
        VISUAL_GRADE,
        VISUAL_PROGRESS_BAR,
        VISUAL_PROGRESS_COLOR,
        VISUAL_WATERMARK_TEXT,
    )
    extra = ""
    if VISUAL_GRADE:
        extra += ",eq=contrast=1.06:saturation=1.12:brightness=0.01,vignette=PI/6"
    if VISUAL_PROGRESS_BAR:
        dur_pb = max(0.1, (float(clip_end) - float(clip_start)) / (1.0 + CLIP_SPEED_UP_PERCENT / 100.0))
        extra += f",drawbox=x=0:y=0:w='iw*t/{dur_pb:.3f}':h=8:color={VISUAL_PROGRESS_COLOR}@0.9:thickness=fill"
    if VISUAL_WATERMARK_TEXT:
        wm = _escape_filter_single_quoted(VISUAL_WATERMARK_TEXT)
        extra += (
            f",drawtext=text='{wm}':font='Arial':fontsize=34:fontcolor=white@0.75:"
            f"x=w-text_w-40:y=h-text_h-40:borderw=2:bordercolor=black@0.6"
        )
    af = f"atempo={tempo},loudnorm=I=-14:TP=-1.0:LRA=11"

    cpu_venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    # NVENC aceita filter_complex em software; somente VA-API exige hwupload extra.
    use_gpu = bool(use_gpu_encoder) and not (is_fc and clip_gpu_uses_vaapi())
    venc: list[str] = gpu_clip_encoder_ffmpeg_args() if use_gpu else cpu_venc
    th = clip_ffmpeg_threads_args(use_gpu_encoder=use_gpu)
    va_pre = ffmpeg_vaapi_hwdevice_args() if (use_gpu and clip_gpu_uses_vaapi()) else []

    if is_fc:
        body = vf_overlay.replace("[0:v]", "[vpre]")
        if extra and body.endswith("[vout]"):
            body = body[: -len("[vout]")] + f"{extra}[vout]"
        fc = f"[0:v]{vf_cut}[vpre];{body}"
        cmd = [
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
            "-filter_complex",
            fc,
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            "-af",
            af,
            *venc,
            "-c:a",
            "aac",
            "-avoid_negative_ts",
            "1",
            "-y",
            render_path,
        ]
    else:
        vf = f"{vf_cut},{vf_overlay}{extra}"
        vf_full = vf + ffmpeg_vaapi_vf_hwupload_suffix(use_gpu_encoder=use_gpu)
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
            render_path,
        ]
        cmd = [*cmd_base[: cmd_base.index("-c:a")], *venc, *cmd_base[cmd_base.index("-c:a") :]]

    render_succeeded = False
    try:
        run_cancelable(cmd, capture_output=True, text=True, check=True)
        render_succeeded = True
    except subprocess.CalledProcessError as e:
        if use_gpu:
            cpu_filter = (
                ["-filter_complex", fc, "-map", "[vout]", "-map", "0:a?"]
                if is_fc
                else ["-vf", f"{vf_cut},{vf_overlay}{extra}"]
            )
            cmd_cpu = [
                FFMPEG_PATH,
                "-fflags",
                "+bitexact",
                *clip_ffmpeg_threads_args(use_gpu_encoder=False),
                "-ss",
                str(clip_start),
                "-i",
                source_video_path,
                "-t",
                str(duration),
                "-map_metadata",
                "-1",
                *cpu_filter,
                "-af",
                af,
                *cpu_venc,
                "-c:a",
                "aac",
                "-avoid_negative_ts",
                "1",
                "-y",
                render_path,
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
        if outro_clean and not render_succeeded:
            try:
                Path(render_path).unlink()
            except OSError:
                pass
    if outro_clean:
        try:
            append_outro_card(render_path, output_path, outro_clean)
        finally:
            try:
                Path(render_path).unlink()
            except OSError:
                pass
    return output_path
