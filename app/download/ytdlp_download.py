"""Download de vídeo via yt-dlp (URLs) para uso no pipeline / GUI."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_log = logging.getLogger("ytdlp_download")


@dataclass(frozen=True)
class VideoSourceAttribution:
    """Metadados do canal/uploader para crédito na legenda de postagem."""

    channel: str
    channel_url: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class DownloadResult:
    path: str
    attribution: VideoSourceAttribution | None = None


def attribution_from_ytdlp_info(data: dict) -> VideoSourceAttribution | None:
    """Extrai nome do canal a partir do JSON do yt-dlp (--write-info-json ou -j)."""
    if not isinstance(data, dict):
        return None
    channel = (data.get("channel") or data.get("uploader") or "").strip()
    if not channel:
        return None
    channel_url = (data.get("channel_url") or data.get("uploader_url") or "").strip() or None
    source_url = (data.get("webpage_url") or data.get("original_url") or "").strip() or None
    return VideoSourceAttribution(
        channel=channel,
        channel_url=channel_url,
        source_url=source_url,
    )


def lookup_source_attribution(
    video_path: str,
    source_by_path: dict[str, VideoSourceAttribution] | None,
) -> VideoSourceAttribution | None:
    if not source_by_path:
        return None
    key = str(Path(video_path).resolve())
    return source_by_path.get(key)


def _ytdlp_cmd_runnable(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(
            [*cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            env=_ytdlp_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@lru_cache(maxsize=1)
def resolve_ytdlp_cmd() -> tuple[str, ...] | None:
    """
    Prefixo argv para invocar yt-dlp: script, `python -m yt_dlp` ou binário no PATH.
    Valida com `--version` para ignorar scripts com shebang quebrado (venv movido de pasta).
    Retorna tuple (hashable) para permitir cache com @lru_cache.
    """
    for key in ("YTDLP_PATH", "YT_DLP_PATH"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            p = Path(raw).expanduser()
            if p.is_file():
                cmd = [str(p.resolve())]
                if _ytdlp_cmd_runnable(cmd):
                    return tuple(cmd)
    # Não usar .resolve() no executável: em venv o python costuma ser symlink para /usr/bin,
    # e resolve() faria procurar yt-dlp no sistema (versão antiga) em vez de .venv/bin/yt-dlp.
    base = Path(sys.executable).expanduser().parent
    for name in ("yt-dlp", "yt-dlp.exe"):
        cand = base / name
        if cand.is_file():
            cmd = [str(cand.resolve())]
            if _ytdlp_cmd_runnable(cmd):
                return tuple(cmd)
    mod_cmd = [sys.executable, "-m", "yt_dlp"]
    if _ytdlp_cmd_runnable(mod_cmd):
        return tuple(mod_cmd)
    w = shutil.which("yt-dlp")
    if w:
        cmd = [str(Path(w).resolve())]
        if _ytdlp_cmd_runnable(cmd):
            return tuple(cmd)
    return None


def resolve_ytdlp_executable() -> str | None:
    """Primeiro elemento de `resolve_ytdlp_cmd()` (compatibilidade / logging)."""
    cmd = resolve_ytdlp_cmd()
    if not cmd:
        return None
    if len(cmd) >= 3 and cmd[1:3] == ("-m", "yt_dlp"):
        return f"{cmd[0]} -m yt_dlp"
    return cmd[0]


def normalize_media_url(line: str) -> str | None:
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return None
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("//"):
        return "https:" + s
    low = s.lower()
    if s.startswith("www.") or "youtube.com" in low or "youtu.be" in low:
        return "https://" + s.lstrip("/")
    return None


def collect_urls_from_lines(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in (text or "").splitlines():
        u = normalize_media_url(line)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _shell_split(s: str) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    return shlex.split(s, posix=os.name != "nt")


def _is_youtube(url: str) -> bool:
    low = url.lower()
    return "youtube.com" in low or "youtu.be" in low


def _cookie_argv() -> list[str]:
    out: list[str] = []
    cfb = (os.getenv("YTDLP_COOKIES_FROM_BROWSER") or "").strip()
    if cfb:
        out.extend(["--cookies-from-browser", cfb])
    cf = (os.getenv("YTDLP_COOKIES_FILE") or "").strip()
    if cf:
        p = Path(cf).expanduser()
        if p.is_file():
            out.extend(["--cookies", str(p.resolve())])
    return out


def _youtube_extractor_strategies() -> list[list[str]]:
    """
    Ordem inspirada nos defaults do yt-dlp README: android_vr/mweb tendem a listar mais
    formatos; `web` sozinho cai frequentemente só no 18 progressive com 403 se nsig falhar.
    Evitar `tv` (unsupported em várias builds).
    """
    custom = (os.getenv("YTDLP_YOUTUBE_EXTRACTOR_ARGS") or "").strip()
    if custom:
        return [["--extractor-args", f"youtube:{custom}"]]
    return [
        ["--extractor-args", "youtube:player_client=android_vr"],
        ["--extractor-args", "youtube:player_client=android_vr,web_safari"],
        ["--extractor-args", "youtube:player_client=mweb"],
        ["--extractor-args", "youtube:player_client=web"],
        ["--extractor-args", "youtube:player_client=web_embedded,web"],
        ["--extractor-args", "youtube:player_client=tv_downgraded,web_safari"],
    ]


def _extra_tool_dirs() -> list[Path]:
    """Pastas comuns fora do PATH mínimo da sessão gráfica (GNOME/KDE)."""
    home = Path.home()
    py_bin = Path(sys.executable).expanduser().parent
    out = [
        home / ".local" / "bin",
        home / ".deno" / "bin",
        py_bin,
        Path("/usr/bin"),
        Path("/usr/local/bin"),
    ]
    return [p for p in out if p.is_dir()]


def _find_executable(basename: str) -> str | None:
    for d in _extra_tool_dirs():
        p = d / basename
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
    w = shutil.which(basename)
    return str(Path(w).resolve()) if w else None


def _ytdlp_subprocess_env() -> dict[str, str]:
    """Garante ffmpeg/deno/node no PATH do filho (merge vídeo + nsig)."""
    env = os.environ.copy()
    prefix: list[str] = []
    for d in _extra_tool_dirs():
        s = str(d.resolve())
        if s not in prefix:
            prefix.append(s)
    if prefix:
        env["PATH"] = os.pathsep.join(prefix + [env.get("PATH", "")])
    return env


def _js_runtime_argv() -> list[str]:
    """
    Sem Node/Deno/Bun o YouTube pode falhar nsig → poucos formatos → 403 no CDN.
    Procura também em ~/.local/bin e ~/.deno/bin (GUI costuma não ter no PATH).
    YTDLP_JS_RUNTIMES: vários separados por vírgula (ex.: deno,node ou node:/usr/bin/nodejs).
    """
    raw = (os.getenv("YTDLP_JS_RUNTIMES") or "").strip()
    if raw:
        out: list[str] = []
        for part in raw.split(","):
            p = part.strip()
            if p:
                out.extend(["--js-runtimes", p])
        return out
    for exe, flag in (
        ("deno", "deno"),
        ("node", "node"),
        ("nodejs", "node"),
        ("bun", "bun"),
    ):
        path = _find_executable(exe)
        if path:
            return ["--js-runtimes", f"{flag}:{path}"]
    return []


def _remote_components_argv() -> list[str]:
    """ejs:github traz blobs JS atualizados; 'default'/'full' inclui também ejs:npm."""
    raw = (os.getenv("YTDLP_REMOTE_COMPONENTS") or "").strip().lower()
    if raw in ("0", "no", "off"):
        return []
    if not raw:
        return ["--remote-components", "ejs:github"]
    if raw in ("1", "yes", "on", "default", "full"):
        return ["--remote-components", "ejs:github", "--remote-components", "ejs:npm"]
    pieces = raw.split()
    return [x for chunk in pieces for x in ("--remote-components", chunk)]


def _cleanup_partial(dest: Path, token: str) -> None:
    for p in dest.glob(f"ytdl_{token}*"):
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass


def _read_attribution_from_info_json(dest: Path, token: str) -> VideoSourceAttribution | None:
    for info_path in dest.glob(f"ytdl_{token}.info.json"):
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
            return attribution_from_ytdlp_info(data)
        except (OSError, json.JSONDecodeError, TypeError) as e:
            _log.warning("Não foi possível ler metadados yt-dlp de %s: %s", info_path, e)
        finally:
            try:
                info_path.unlink()
            except OSError:
                pass
    return None


def _run_ytdlp(cmd: list[str], *, env: dict[str, str]) -> int:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _log.info("%s", line.rstrip())
    return int(proc.wait())


def download_video(url: str, dest_dir: str | Path, *, no_playlist: bool = True) -> DownloadResult:
    """
    Baixa um vídeo com yt-dlp para dest_dir.

    Devolve o caminho do ficheiro final e, quando disponível, metadados do canal
    (via --write-info-json) para crédito na legenda de postagem.
    Progresso vai para logging (nível INFO).
    """
    ytdlp_cmd = resolve_ytdlp_cmd()
    if not ytdlp_cmd:
        raise FileNotFoundError(
            "yt-dlp não encontrado. Instale: pip install -U 'yt-dlp[default]' "
            "(ou defina YTDLP_PATH no ambiente)."
        )

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:16]
    out_tmpl = dest / f"ytdl_{token}.%(ext)s"

    user_prefix = _shell_split(os.getenv("YTDLP_EXTRA_ARGS") or "")
    if user_prefix:
        strategies: list[list[str]] = [[]]
    elif _is_youtube(url):
        strategies = _youtube_extractor_strategies()
    else:
        strategies = [[]]

    cookies = _cookie_argv()
    child_env = _ytdlp_subprocess_env()
    js_argv = _js_runtime_argv()
    remote_argv = _remote_components_argv()

    # Preferir H.264 (avc1): OpenCV/FFmpeg em Linux costumam tentar HW AV1, falhar e spammar stderr
    # por frame; VP9/H.264 em software são mais previsíveis. Mantém fallback se só existir AV1.
    _ytdlp_format = (os.getenv("YTDLP_FORMAT") or "").strip()
    _format = _ytdlp_format or (
        "bv*[vcodec^=avc1]+ba/bv*[vcodec!^=av01]+ba/bv*+ba/bestvideo*+bestaudio/best"
    )

    tail = [
        "-o",
        str(out_tmpl),
        "-f",
        _format,
        "--merge-output-format",
        "mp4",
        "--write-info-json",
        "--newline",
        "--concurrent-fragments",
        os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "4"),
        url.strip(),
    ]

    if _is_youtube(url) and not os.getenv("YTDLP_JS_RUNTIMES") and not js_argv:
        _log.warning(
            "\n[yt-dlp] Aviso: nenhum runtime JS (deno/node/bun) no PATH — "
            "YouTube pode falhar (nsig). Instale Node ou Deno ou defina YTDLP_JS_RUNTIMES.\n"
        )

    last_rc = 1
    for attempt, strat in enumerate(strategies):
        if attempt > 0:
            _cleanup_partial(dest, token)
            _log.info(
                "\n[yt-dlp] Retentativa %s/%s (outro player_client do YouTube)…\n",
                attempt + 1,
                len(strategies),
            )
        cmd: list[str] = [*ytdlp_cmd]
        if no_playlist:
            cmd.append("--no-playlist")
        cmd.extend(user_prefix)
        cmd.extend(remote_argv)
        cmd.extend(js_argv)
        cmd.extend(strat)
        cmd.extend(cookies)
        cmd.extend(tail)
        last_rc = _run_ytdlp(cmd, env=child_env)
        if last_rc == 0:
            break

    if last_rc != 0:
        hints = (
            "YouTube com 403: (1) instale Deno ou Node (em ~/.local/bin ou /usr/bin) ou "
            "YTDLP_JS_RUNTIMES=node:/caminho/do/node (2) cookies: "
            "YTDLP_COOKIES_FROM_BROWSER=firefox (feche o browser) ou YTDLP_COOKIES_FILE=… "
            "(3) pip install -U 'yt-dlp[default]' (4) YTDLP_REMOTE_COMPONENTS=full no .env."
        )
        raise RuntimeError(f"yt-dlp falhou (código {last_rc}) para: {url[:80]!r}\n{hints}")

    candidates = [
        p
        for p in dest.glob(f"ytdl_{token}*")
        if p.is_file() and not p.name.endswith(".part") and not p.name.endswith(".ytdl")
    ]
    video_ext = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    preferred = [p for p in candidates if p.suffix.lower() in video_ext]
    pool = preferred if preferred else candidates
    if not pool:
        raise RuntimeError(f"Nenhum ficheiro de vídeo encontrado após download (token {token}).")

    video_path = str(max(pool, key=lambda p: p.stat().st_size).resolve())
    attribution = _read_attribution_from_info_json(dest, token)
    if attribution:
        _log.info(
            "Canal detectado para crédito na legenda: %s",
            attribution.channel,
        )
    return DownloadResult(path=video_path, attribution=attribution)
