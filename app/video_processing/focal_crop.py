"""
Calcula o offset (x, y) do crop 9:16 após scale=increase, priorizando rostos
(foco principal em vlogs, podcasts, cursos). Detecção via MediaPipe Tasks
(Face Detector / BlazeFace); sem rosto confiável, tenta centro por movimento
entre quadros. Com 2+ rostos no quadro, estima quem fala por movimento na região
da boca e pode gerar crop dinâmico (expressões FFmpeg em t) para manter o
falante centrado ao longo do corte.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from app.core.config import (
    OUTPUT_VIDEO_HEIGHT,
    OUTPUT_VIDEO_WIDTH,
    SMART_CROP_FRAME_SAMPLES,
    SMART_CROP_MIN_CHANGE_INTERVAL_SEC,
    SMART_CROP_SPEAKER_FPS,
)

# BlazeFace full-range — melhor para enquadramentos variados (ex.: 9:16).
_BLAZE_FACE_FULL_RANGE_TFLITE = "blaze_face_full_range.tflite"
_BLAZE_FACE_FULL_RANGE_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_full_range/float16/latest/blaze_face_full_range.tflite"
)
_BLAZE_FACE_FULL_RANGE_SHA256 = os.getenv("BLAZEFACE_TFLITE_SHA256", "").strip().lower()

# Região inferior do bbox (aprox. boca) para energia de movimento entre quadros.
_MOUTH_Y0_FRAC = 0.52
_MOUTH_Y1_FRAC = 0.98
_MOUTH_X0_FRAC = 0.18
_MOUTH_X1_FRAC = 0.82
_MOUTH_ROI_W = 48
_MOUTH_ROI_H = 32

# Histerese: só troca de falante se o vencedor superar o segundo por esta fração do máximo.
_SPEAKER_MARGIN_FRAC = 0.12

_log = logging.getLogger(__name__)


def _opencv_file_capture(path: str):
    """Abre arquivo de vídeo com backend FFmpeg (Mesa/Linux) e cai para o default se falhar."""
    import cv2

    ff = getattr(cv2, "CAP_FFMPEG", 0)
    if ff:
        cap = cv2.VideoCapture(path, ff)
        if cap.isOpened():
            return cap
        try:
            cap.release()
        except Exception:
            pass
    return cv2.VideoCapture(path)


class _MediaPipeFaceDetectorBroken(Exception):
    """Primeiro detect() falhou; o grafo do MediaPipe costuma ficar inválido — abortar smart crop."""


def _linux_mediapipe_gpu_delegate_unsupported() -> bool:
    """True quando GPU delegate do Tasks no Linux tende a falhar (ex.: AMD+Mesa sem NVIDIA)."""
    if sys.platform != "linux":
        return False
    return not Path("/proc/driver/nvidia/version").is_file()


def _mediapipe_face_delegate() -> Any:
    """
    Delegate do BlazeFace. GPU no Linux (EGL) costuma quebrar em alguns drivers AMD+Mesa
    (RET_CHECK em TensorsToDetectionsCalculator), travando o pipeline no crop inteligente.
    Use SMART_CROP_MEDIAPIPE_GPU=1 para tentar GPU (ex.: NVIDIA no Linux).
    Com GPU pedida em Linux sem driver NVIDIA, usa CPU salvo SMART_CROP_MEDIAPIPE_GPU_FORCE=1.
    """
    import mediapipe as mp

    raw = (os.getenv("SMART_CROP_MEDIAPIPE_GPU") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        wants_gpu = False
    elif raw in ("1", "true", "yes", "on"):
        wants_gpu = True
    else:
        wants_gpu = not _linux_mediapipe_gpu_delegate_unsupported()
    force_gpu = (os.getenv("SMART_CROP_MEDIAPIPE_GPU_FORCE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if wants_gpu and _linux_mediapipe_gpu_delegate_unsupported() and not force_gpu:
        _log.info(
            "SMART_CROP_MEDIAPIPE_GPU ignorado no Linux sem /proc/driver/nvidia/version "
            "(delegate GPU do MediaPipe costuma falhar em AMD+Mesa). Usando CPU. "
            "Para insistir na GPU: SMART_CROP_MEDIAPIPE_GPU_FORCE=1."
        )
        return mp.tasks.BaseOptions.Delegate.CPU
    if wants_gpu:
        return mp.tasks.BaseOptions.Delegate.GPU
    return mp.tasks.BaseOptions.Delegate.CPU


def _create_face_detector(model_path: str, delegate: Any) -> Any:
    import mediapipe as mp

    BaseOptions = mp.tasks.BaseOptions
    FaceDetector = mp.tasks.vision.FaceDetector
    FaceDetectorOptions = mp.tasks.vision.FaceDetectorOptions
    VisionRunningMode = mp.tasks.vision.RunningMode
    return FaceDetector.create_from_options(
        FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=model_path, delegate=delegate),
            running_mode=VisionRunningMode.IMAGE,
            min_detection_confidence=0.5,
        )
    )


def _mediapipe_face_detector_smoke_test(fd: Any) -> None:
    """Um detect() mínimo; falhas de driver/shape aparecem aqui antes de processar o vídeo."""
    import numpy as np
    import mediapipe as mp

    rgb = np.zeros((128, 128, 3), dtype=np.uint8)
    rgb[:, :] = (48, 48, 48)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    fd.detect(mp_image)


def _open_face_detector_with_cpu_fallback(model_path: str) -> Any | None:
    """
    Abre FaceDetector com o delegate preferido e, se quebrar (GPU/driver), tenta CPU.
    """
    import mediapipe as mp

    primary = _mediapipe_face_delegate()
    cpu = mp.tasks.BaseOptions.Delegate.CPU
    delegates: list[Any] = []
    for d in (primary, cpu):
        if d not in delegates:
            delegates.append(d)

    last_err: BaseException | None = None
    for del_ in delegates:
        try:
            fd = _create_face_detector(model_path, del_)
        except Exception as e:
            last_err = e
            _log.debug("FaceDetector.create falhou (delegate=%s): %s", del_, e)
            continue
        try:
            _mediapipe_face_detector_smoke_test(fd)
        except Exception as e:
            last_err = e
            _log.warning(
                "FaceDetector MediaPipe falhou no teste inicial (delegate=%s): %s",
                del_,
                e,
            )
            try:
                fd.close()
            except Exception:
                pass
            continue
        if del_ != primary:
            _log.warning(
                "FaceDetector MediaPipe em CPU após falha com o delegate inicial (smart crop)."
            )
        return fd

    if last_err is not None:
        _log.warning(
            "Não foi possível abrir FaceDetector MediaPipe (%s); smart crop desativado.",
            last_err,
        )
    return None


def _model_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "meu_saas_cortes" / "mediapipe_models"


def _ensure_blaze_face_model_path() -> Optional[str]:
    """Garante o .tflite no disco; retorna caminho absoluto ou None se falhar."""
    dest = _model_cache_dir() / _BLAZE_FACE_FULL_RANGE_TFLITE
    if dest.is_file() and dest.stat().st_size > 0:
        if _BLAZE_FACE_FULL_RANGE_SHA256:
            try:
                if _sha256_file(dest) != _BLAZE_FACE_FULL_RANGE_SHA256:
                    try:
                        dest.unlink()
                    except OSError:
                        return None
                else:
                    return str(dest.resolve())
            except OSError:
                return None
        else:
            return str(dest.resolve())
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".download")
        urllib.request.urlretrieve(_BLAZE_FACE_FULL_RANGE_URL, tmp)  # noqa: S310 — URL fixa oficial
        if _BLAZE_FACE_FULL_RANGE_SHA256:
            if _sha256_file(tmp) != _BLAZE_FACE_FULL_RANGE_SHA256:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return None
        tmp.replace(dest)
    except (OSError, urllib.error.URLError):
        return None
    return str(dest.resolve()) if dest.is_file() else None


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _scale_cover_dims(src_w: int, src_h: int, out_w: int, out_h: int) -> tuple[float, float, float]:
    """Mesma lógica que scale=W:H:force_original_aspect_ratio=increase (área coberta)."""
    s = max(out_w / src_w, out_h / src_h)
    return src_w * s, src_h * s, s


def _clamp_crop_xy(
    cx_scaled: float,
    cy_scaled: float,
    iw: float,
    ih: float,
    out_w: int,
    out_h: int,
) -> tuple[int, int]:
    x = int(round(cx_scaled - out_w / 2))
    y = int(round(cy_scaled - out_h / 2))
    x = max(0, min(x, int(math.floor(iw)) - out_w))
    y = max(0, min(y, int(math.floor(ih)) - out_h))
    return x, y


def _all_faces_sorted_by_x(
    bgr_frame,
    face_detector,
) -> list[tuple[float, float, float, float, float, float]]:
    """Lista (cx, cy, ox, oy, w, h) ordenada da esquerda para a direita."""
    import cv2
    import mediapipe as mp

    h, w = bgr_frame.shape[:2]
    if w <= 0 or h <= 0:
        return []
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    try:
        result = face_detector.detect(mp_image)
    except Exception as e:
        # Um falha costuma “envenenar” o grafo; continuar amostrando só repete erro e atrasa o pipeline.
        _log.warning(
            "MediaPipe FaceDetector.detect falhou; smart crop será ignorado neste trecho. Erro: %s",
            e,
        )
        raise _MediaPipeFaceDetectorBroken() from e
    if not result.detections:
        return []
    out: list[tuple[float, float, float, float, float, float]] = []
    for det in result.detections:
        bb = det.bounding_box
        ox, oy, bw, bh = float(bb.origin_x), float(bb.origin_y), float(bb.width), float(bb.height)
        cx = ox + bw / 2.0
        cy = oy + bh / 2.0
        out.append((cx, cy, ox, oy, bw, bh))
    out.sort(key=lambda t: t[0])
    return out


def _largest_face_center_pixels(
    bgr_frame,
    face_detector,
) -> Optional[tuple[float, float]]:
    """Centro do maior rosto (área do bbox) em pixels, ou None."""
    faces = _all_faces_sorted_by_x(bgr_frame, face_detector)
    if not faces:
        return None
    best = max(
        faces,
        key=lambda f: f[4] * f[5],
    )
    return (best[0], best[1])


def _mouth_roi_gray(gray, ox: float, oy: float, bw: float, bh: float):
    """ROI da boca em escala fixa para comparar quadros consecutivos."""
    import cv2

    h, w = gray.shape[:2]
    if bw <= 1 or bh <= 1:
        return None
    x0 = int(max(0, min(w - 1, ox + bw * _MOUTH_X0_FRAC)))
    x1 = int(max(0, min(w, ox + bw * _MOUTH_X1_FRAC)))
    y0 = int(max(0, min(h - 1, oy + bh * _MOUTH_Y0_FRAC)))
    y1 = int(max(0, min(h, oy + bh * _MOUTH_Y1_FRAC)))
    if x1 <= x0 or y1 <= y0:
        return None
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    return cv2.resize(roi, (_MOUTH_ROI_W, _MOUTH_ROI_H), interpolation=cv2.INTER_AREA)


def _motion_center_pixels(prev_gray, gray) -> Optional[tuple[float, float]]:
    """Centro aproximado da área com mais movimento entre dois quadros (ação sem rosto)."""
    import cv2

    diff = cv2.absdiff(prev_gray, gray)
    diff = cv2.GaussianBlur(diff, (31, 31), 0)
    _, th = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
    th = cv2.dilate(th, None, iterations=2)
    m = cv2.moments(th)
    if m["m00"] < (prev_gray.shape[0] * prev_gray.shape[1] * 0.002):
        return None
    return (m["m10"] / m["m00"], m["m01"] / m["m00"])


def _match_faces_greedy(
    prev: list[tuple[float, float, float, float, float, float]],
    curr: list[tuple[float, float, float, float, float, float]],
) -> list[Optional[int]]:
    """Para cada índice em curr, índice correspondente em prev ou None."""
    if not curr:
        return []
    if not prev:
        return [None] * len(curr)
    used_prev: set[int] = set()
    pairs: list[tuple[float, int, int]] = []
    for ic, fc in enumerate(curr):
        cxc = fc[0]
        for ip, fp in enumerate(prev):
            if ip in used_prev:
                continue
            d = abs(cxc - fp[0])
            pairs.append((d, ic, ip))
    pairs.sort(key=lambda x: x[0])
    curr_to_prev: dict[int, int] = {}
    for d, ic, ip in pairs:
        if ic in curr_to_prev or ip in used_prev:
            continue
        max_w = max(curr[ic][4], prev[ip][4])
        if d > max_w * 1.2:
            continue
        curr_to_prev[ic] = ip
        used_prev.add(ip)
    return [curr_to_prev.get(i) for i in range(len(curr))]


def _stabilize_speaker_changes_min_interval(
    samples: list[tuple[float, float, float, int]],
    min_interval_sec: float,
) -> list[tuple[float, float, float, int]]:
    """
    Só aceita troca de falante (e crop correspondente) se passaram pelo menos
    `min_interval_sec` desde a última mudança — reduz alternância frenética.
    Com o mesmo falante, segue o centro (rx, ry) do detector.
    """
    if not samples or min_interval_sec <= 0:
        return samples
    out: list[tuple[float, float, float, int]] = []
    t0, cx, cy, spk = samples[0]
    last_change_t = t0
    out.append((t0, cx, cy, spk))
    for i in range(1, len(samples)):
        t, rx, ry, rspk = samples[i]
        if rspk != spk:
            if t - last_change_t >= min_interval_sec:
                spk = rspk
                cx, cy = rx, ry
                last_change_t = t
            # senão mantém cx, cy, spk anteriores
        else:
            cx, cy = rx, ry
        out.append((t, cx, cy, spk))
    return out


def _piecewise_t_expr(values: list[int], boundaries: list[float]) -> str:
    """
    Expressão FFmpeg em t: constante por partes.
    values[i] em [boundaries[i-1], boundaries[i]), boundaries[-1] = +inf implícito.
    len(values) == len(boundaries) + 1.
    """
    assert len(values) == len(boundaries) + 1
    e = str(values[-1])
    for i in range(len(boundaries) - 1, -1, -1):
        t = boundaries[i]
        e = f"if(lt(t\\,{t:.6f}\\)\\,{values[i]}\\,{e})"
    return e


def _max_face_count_sampled(
    cap,
    face_detector,
    indices: list[int],
    nframes: int,
) -> int:
    """Maior número de rostos visto em uma amostra de quadros."""
    import cv2

    m = 0
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(idx, max(0, nframes - 1)))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        faces = _all_faces_sorted_by_x(frame, face_detector)
        m = max(m, len(faces))
    return m


def _max_face_count_at_times(
    cap,
    face_detector,
    times_sec: list[float],
) -> int:
    """Amostra em tempos absolutos (s) no arquivo (seek por MSEC + read)."""
    import cv2

    m = 0
    for ts in times_sec:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(ts)) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        faces = _all_faces_sorted_by_x(frame, face_detector)
        m = max(m, len(faces))
    return m


def _compute_static_median_crop_cap(
    cap: object,
    face_detector: object | None,
    out_w: int,
    out_h: int,
    *,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> Optional[tuple[int, int]]:
    """Amostra quadros no capture já aberto; mediana do centro (maior rosto / movimento)."""
    import cv2

    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
    if src_w <= 0 or src_h <= 0:
        return None

    vfps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
    msec_times: list[float] | None = None
    indices: list[int]
    if clip_start is not None and clip_end is not None:
        clip_len = max(0.04, float(clip_end) - float(clip_start))
        n = max(3, min(SMART_CROP_FRAME_SAMPLES, max(1, int(clip_len * vfps))))
        t0 = float(clip_start)
        msec_times = [t0 + (i + 0.5) / n * clip_len for i in range(n)]
        indices = []
    else:
        n = max(3, min(SMART_CROP_FRAME_SAMPLES, max(1, nframes)))
        if nframes > 0:
            indices = [min(nframes - 1, int((i + 0.5) * nframes / n)) for i in range(n)]
        else:
            indices = [0]

    centers_x: list[float] = []
    centers_y: list[float] = []

    def sample_frames(fd: object | None, *, msec_times: list[float] | None = None) -> None:
        prev_gray: object | None = None
        if msec_times is not None:
            for ts in msec_times:
                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, ts) * 1000.0)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                c = None
                if fd is not None:
                    c = _largest_face_center_pixels(frame, fd)
                if c is not None:
                    centers_x.append(c[0])
                    centers_y.append(c[1])
                elif prev_gray is not None:
                    c2 = _motion_center_pixels(prev_gray, gray)
                    if c2 is not None:
                        centers_x.append(c2[0])
                        centers_y.append(c2[1])
                prev_gray = gray
            return
        prev_gray = None
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            c = None
            if fd is not None:
                c = _largest_face_center_pixels(frame, fd)
            if c is not None:
                centers_x.append(c[0])
                centers_y.append(c[1])
            elif prev_gray is not None:
                c2 = _motion_center_pixels(prev_gray, gray)
                if c2 is not None:
                    centers_x.append(c2[0])
                    centers_y.append(c2[1])
            prev_gray = gray

    sample_frames(face_detector, msec_times=msec_times)

    if not centers_x:
        return None

    cx = statistics.median(centers_x)
    cy = statistics.median(centers_y)

    iw, ih, s = _scale_cover_dims(src_w, src_h, out_w, out_h)
    cx_scaled = cx * s
    cy_scaled = cy * s

    return _clamp_crop_xy(cx_scaled, cy_scaled, iw, ih, out_w, out_h)


def _speaker_timeline_crop_segments(
    cap: object,
    face_detector,
    out_w: int,
    out_h: int,
    fps_sample: float,
    *,
    time_offset_sec: float = 0.0,
    clip_duration_sec: float | None = None,
) -> Optional[list[tuple[float, float, int, int]]]:
    """
    Segmentos [t0,t1) com (crop_x, crop_y) para manter o rosto do falante no centro.
    Usa um VideoCapture já aberto; não chama release().
    """
    import cv2

    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
    vfps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
    if clip_duration_sec is not None:
        duration = max(0.0, float(clip_duration_sec))
    else:
        duration = nframes / vfps if vfps > 0 and nframes > 0 else 0.0
    t_off = float(time_offset_sec)
    if src_w <= 0 or src_h <= 0 or duration <= 0:
        return None

    iw, ih, s = _scale_cover_dims(src_w, src_h, out_w, out_h)

    dt = 1.0 / max(0.5, min(12.0, fps_sample))
    times_t: list[float] = []
    t = 0.0
    while t < duration - 1e-6:
        times_t.append(t)
        t += dt

    prev_gray: object | None = None
    prev_faces: list[tuple[float, float, float, float, float, float]] = []
    prev_mouths: list[object | None] = []

    samples: list[tuple[float, float, float, int]] = []
    last_speaker_i = 0
    last_cx_s: Optional[float] = None
    last_cy_s: Optional[float] = None

    for t in times_t:
        if clip_duration_sec is not None:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_off + t) * 1000.0)
        else:
            fi = min(nframes - 1, int(t * vfps + 0.5))
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _all_faces_sorted_by_x(frame, face_detector)

        if len(faces) >= 2 and prev_gray is not None and len(prev_faces) >= 1:
            match = _match_faces_greedy(prev_faces, faces)
            motions: list[float] = []
            for j, f in enumerate(faces):
                ox, oy, bw, bh = f[2], f[3], f[4], f[5]
                roi = _mouth_roi_gray(gray, ox, oy, bw, bh)
                mp = match[j] if j < len(match) else None
                if (
                    roi is not None
                    and mp is not None
                    and 0 <= mp < len(prev_mouths)
                    and prev_mouths[mp] is not None
                ):
                    diff = cv2.absdiff(roi, prev_mouths[mp])
                    m = float(cv2.mean(diff)[0])
                else:
                    m = 0.0
                motions.append(m)

            if motions:
                ranked = sorted(range(len(motions)), key=lambda i: motions[i], reverse=True)
                top = ranked[0]
                second = motions[ranked[1]] if len(ranked) > 1 else 0.0
                margin = (motions[top] - second) if len(motions) > 1 else motions[top]
                if motions[top] <= 1e-6:
                    spk = last_speaker_i if last_speaker_i < len(faces) else top
                elif margin < _SPEAKER_MARGIN_FRAC * max(motions[top], 1.0):
                    spk = last_speaker_i if last_speaker_i < len(faces) else top
                else:
                    spk = top
            else:
                spk = 0
        elif len(faces) == 1:
            spk = 0
        elif len(faces) == 0:
            if last_cx_s is not None and last_cy_s is not None:
                samples.append((t, last_cx_s, last_cy_s, last_speaker_i))
            prev_gray = gray
            prev_faces = faces
            prev_mouths = []
            continue
        else:
            spk = 0

        cx, cy = faces[spk][0], faces[spk][1]
        last_speaker_i = spk
        last_cx_s = cx * s
        last_cy_s = cy * s
        samples.append((t, last_cx_s, last_cy_s, spk))

        mouths_next: list[object | None] = []
        for f in faces:
            ox, oy, bw, bh = f[2], f[3], f[4], f[5]
            mouths_next.append(_mouth_roi_gray(gray, ox, oy, bw, bh))
        prev_mouths = mouths_next
        prev_faces = faces
        prev_gray = gray

    if len(samples) < 2:
        return None

    samples = _stabilize_speaker_changes_min_interval(
        samples, SMART_CROP_MIN_CHANGE_INTERVAL_SEC
    )

    merged: list[tuple[float, float, int, int]] = []
    seg_start = samples[0][0]
    seg_spk = samples[0][3]
    xs = [samples[0][1]]
    ys = [samples[0][2]]

    def flush(end_t: float) -> None:
        nonlocal seg_start, seg_spk, xs, ys
        if end_t <= seg_start + 1e-9:
            xs = []
            ys = []
            return
        cx = statistics.median(xs)
        cy = statistics.median(ys)
        x, y = _clamp_crop_xy(cx, cy, iw, ih, out_w, out_h)
        merged.append((seg_start, end_t, x, y))
        xs = []
        ys = []

    for i in range(1, len(samples)):
        t, x, y, spk = samples[i]
        if spk != seg_spk and spk >= 0:
            flush(t)
            seg_start = t
            seg_spk = spk
        xs.append(x)
        ys.append(y)

    flush(duration)

    out: list[tuple[float, float, int, int]] = []
    for a, b, x, y in merged:
        if b - a < 0.08:
            continue
        out.append((a, b, x, y))

    if not out:
        return None

    # Funde segmentos vizinhos com o mesmo crop (inteiro).
    compact: list[tuple[float, float, int, int]] = [out[0]]
    for a, b, x, y in out[1:]:
        pa, pb, px, py = compact[-1]
        if px == x and py == y:
            compact[-1] = (pa, b, x, y)
        else:
            compact.append((a, b, x, y))

    return compact


def compute_crop_plan(
    video_path: str,
    out_w: int | None = None,
    out_h: int | None = None,
    *,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> Optional[dict[str, Any]]:
    """
    Plano de crop para um arquivo de vídeo ou trecho [clip_start, clip_end) no mesmo arquivo.

    Retorna:
      {"mode": "static", "x": int, "y": int}
      {"mode": "dynamic", "x_expr": str, "y_expr": str, "fallback_x": int, "fallback_y": int}
    ou None.
    """
    out_w = out_w if out_w is not None else OUTPUT_VIDEO_WIDTH
    out_h = out_h if out_h is not None else OUTPUT_VIDEO_HEIGHT

    try:
        import cv2
    except ImportError:
        return None

    model_path = _ensure_blaze_face_model_path()
    if not model_path:
        return None

    fd = _open_face_detector_with_cpu_fallback(model_path)
    if fd is None:
        return None

    cap: object | None = None
    try:
        cap = _opencv_file_capture(video_path)
        if not cap.isOpened():
            return None
        try:
            use_clip = clip_start is not None and clip_end is not None
            clip_len = 0.0
            if use_clip:
                clip_len = max(0.04, float(clip_end) - float(clip_start))
                vfps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
                nquick = max(3, min(SMART_CROP_FRAME_SAMPLES, max(1, int(clip_len * vfps))))
                t0 = float(clip_start)
                qtimes = [t0 + (i + 0.5) / nquick * clip_len for i in range(nquick)]
                max_faces = _max_face_count_at_times(cap, fd, qtimes)
                static = _compute_static_median_crop_cap(
                    cap, fd, out_w, out_h, clip_start=float(clip_start), clip_end=float(clip_end)
                )
            else:
                nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
                nquick = max(3, min(SMART_CROP_FRAME_SAMPLES, max(1, nframes)))
                if nframes > 0:
                    qidx = [
                        min(nframes - 1, int((i + 0.5) * nframes / nquick)) for i in range(nquick)
                    ]
                else:
                    qidx = [0]
                max_faces = _max_face_count_sampled(cap, fd, qidx, nframes)
                static = _compute_static_median_crop_cap(cap, fd, out_w, out_h)

            if max_faces < 2:
                if static is None:
                    return None
                return {"mode": "static", "x": static[0], "y": static[1]}

            segs = _speaker_timeline_crop_segments(
                cap,
                fd,
                out_w,
                out_h,
                SMART_CROP_SPEAKER_FPS,
                time_offset_sec=float(clip_start) if use_clip else 0.0,
                clip_duration_sec=clip_len if use_clip else None,
            )
            if not segs or len(segs) == 1:
                if static is None:
                    return None
                return {"mode": "static", "x": static[0], "y": static[1]}

            duration = segs[-1][1]
            xs = [s[2] for s in segs]
            ys = [s[3] for s in segs]
            boundaries = [s[1] for s in segs[:-1]]
            x_expr = _piecewise_t_expr(xs, boundaries)
            y_expr = _piecewise_t_expr(ys, boundaries)

            total = max(duration, 1e-6)
            fb_x = int(round(sum(s[2] * (s[1] - s[0]) for s in segs) / total))
            fb_y = int(round(sum(s[3] * (s[1] - s[0]) for s in segs) / total))

            return {
                "mode": "dynamic",
                "x_expr": x_expr,
                "y_expr": y_expr,
                "fallback_x": fb_x,
                "fallback_y": fb_y,
            }
        except _MediaPipeFaceDetectorBroken:
            _log.warning(
                "Smart crop desativado (detector de rosto indisponível neste driver/GPU) para: %s",
                video_path,
            )
            return None
        except Exception as e:
            _log.warning(
                "Smart crop (MediaPipe) falhou para %s: %s — usando crop padrão.",
                video_path,
                e,
            )
            return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        try:
            fd.close()
        except Exception:
            pass
