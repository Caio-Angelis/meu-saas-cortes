"""Utilitários para a GUI: duração de vídeo (ffprobe), notificação do SO e pacote .zip."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def ffprobe_duration_seconds(video_path: str) -> float | None:
    """Duração em segundos via ffprobe; None se indisponível."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    p = Path(video_path)
    if not p.is_file():
        return None
    try:
        r = subprocess.run(
            [
                exe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        return float((r.stdout or "").strip())
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def format_duration_hms(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(round(seconds))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def desktop_notify(title: str, body: str, *, urgency: str = "normal") -> None:
    """Notificação leve (Linux notify-send, macOS osascript). Windows: ignorado."""
    title = (title or "Cortes").replace('"', "'")
    body = (body or "").replace('"', "'")[:500]
    try:
        if sys.platform == "linux":
            exe = shutil.which("notify-send")
            if exe:
                cmd = [exe, title, body]
                if urgency == "low":
                    cmd.extend(["-u", "low"])
                subprocess.run(cmd, check=False, timeout=5)
        elif sys.platform == "darwin":
            script = f'display notification "{body}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def export_cortes_zip(output_mp4s: list[str], dest_dir: Path) -> Path | None:
    """
    Cria um .zip com os .mp4, os .txt de legenda TikTok (mesmo nome base) e um LEIA-ME.
    Retorna o caminho do zip ou None se não houver arquivos válidos.
    """
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for raw in output_mp4s:
        mp4 = Path(raw)
        if not mp4.is_file():
            continue
        files.append(mp4)
        cap = mp4.with_suffix(".txt")
        if cap.is_file():
            files.append(cap)

    if not files:
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = dest_dir / f"cortes_export_{ts}.zip"

    readme = (
        "Pacote gerado pelo SaaS de Cortes Virais\n"
        "========================================\n\n"
        "Cada vídeo .mp4 pode ter um arquivo .txt com o mesmo nome: é a legenda/descrição\n"
        "para colar na publicação do TikTok (copiar tudo do .txt e colar no app).\n\n"
        "Ordem sugerida: numere os arquivos pelo prefixo (1_, 2_, …) — costuma ser a ordem\n"
        "de prioridade dos cortes no vídeo original.\n"
    )

    written_names: set[str] = set()

    def arc_for(p: Path) -> str:
        name = p.name
        if name not in written_names:
            written_names.add(name)
            return name
        # colisão improvável: prefixa pasta
        return f"{p.parent.name}__{name}"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("LEIA-ME_POSTAGEM.txt", readme)
        for fp in files:
            zf.write(fp, arcname=arc_for(fp))

    return zip_path
