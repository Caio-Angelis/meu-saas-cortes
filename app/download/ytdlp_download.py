"""Download de vídeo via yt-dlp (URLs) para uso no pipeline / GUI."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.source_history import canonical_source_key, get_source_history

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


@dataclass(frozen=True)
class ThemeSearchHit:
    """Resultado da busca por tema, incluindo a razão do ranking local."""

    url: str
    title: str
    view_count: int
    duration_sec: float
    channel: str | None = None
    source_score: float = 0.0
    relevance_score: float = 0.0
    format_score: float = 0.0
    ranking_reason: str = ""


def _theme_min_duration_sec(override: float | None = None) -> float:
    if override is not None:
        return float(override)
    raw = (os.getenv("YT_THEME_MIN_DURATION_SEC") or "600").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 600.0


def _theme_search_n(override: int | None = None) -> int:
    if override is not None:
        n = int(override)
    else:
        raw = (os.getenv("YT_THEME_SEARCH_N") or "20").strip()
        try:
            n = int(raw)
        except ValueError:
            n = 20
    return max(1, min(50, n))


def _normalize_search_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _query_tokens(query: str) -> set[str]:
    stop = {"a", "o", "de", "da", "do", "e", "em", "para", "com", "the", "and", "of"}
    return {token for token in _normalize_search_text(query).split() if len(token) >= 3 and token not in stop}


def _view_score(view_count: int, view_values: list[int]) -> float:
    if not view_values or max(view_values) <= 0:
        return 4.0
    positive = [max(0, value) for value in view_values]
    low = min(positive)
    high = max(positive)
    if high == low:
        return 5.0
    log_value = math.log1p(max(0, view_count))
    log_low = math.log1p(low)
    log_high = math.log1p(high)
    return max(0.0, min(10.0, 10.0 * (log_value - log_low) / (log_high - log_low)))


def _source_scores(data: dict, query: str, *, view_values: list[int]) -> dict[str, object]:
    title = str(data.get("title") or "").strip()
    channel = str(data.get("channel") or data.get("uploader") or "").strip()
    searchable = _normalize_search_text(f"{title} {channel}")
    query_norm = _normalize_search_text(query)
    query_tokens = _query_tokens(query)
    title_tokens = set(searchable.split())
    overlap = len(query_tokens & title_tokens) / len(query_tokens) if query_tokens else 0.5
    phrase_bonus = 1.5 if query_norm and query_norm in searchable else 0.0
    relevance = max(0.0, min(10.0, overlap * 8.5 + phrase_bonus)) if query_tokens else 5.0

    positive_format_terms = (
        "entrevista",
        "podcast",
        "conversa",
        "papo",
        "debate",
        "fala sobre",
        "explica",
        "historia",
        "bastidores",
        "corte",
        "cortes",
        "perguntas",
        "talk",
    )
    negative_format_terms = (
        "musica completa",
        "full album",
        "album completo",
        "official audio",
        "clipe oficial",
        "karaoke",
        "instrumental",
        "lyrics",
        "playthrough",
        "cover completo",
        "1 hour loop",
    )
    positive_hits = sum(term in searchable for term in positive_format_terms)
    negative_hits = sum(term in searchable for term in negative_format_terms)
    format_score = max(0.0, min(10.0, 5.0 + positive_hits * 1.35 - negative_hits * 2.4))

    duration_raw = data.get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        duration_score = 4.0
    elif duration < 600:
        duration_score = 2.0
    elif duration <= 5400:
        duration_score = 9.0
    elif duration <= 10_800:
        duration_score = 6.5
    else:
        duration_score = 3.5

    known_entity_terms = (
        "kiko loureiro",
        "slash",
        "john mayer",
        "steve vai",
        "joe satriani",
        "jimi hendrix",
        "metallica",
        "guns n roses",
        "megadeth",
        "pink floyd",
        "guitarra",
        "violao",
        "blues",
        "rock",
        "pentatonica",
        "improvisacao",
    )
    entity_score = 8.0 if any(term in searchable for term in known_entity_terms) else 4.0
    try:
        view_count = int(data.get("view_count") or 0)
    except (TypeError, ValueError):
        view_count = 0
    source_score = (
        relevance * 0.35
        + format_score * 0.25
        + duration_score * 0.10
        + entity_score * 0.10
        + _view_score(view_count, view_values) * 0.20
    )
    reason_parts = [f"relevância {relevance:.1f}", f"formato falado {format_score:.1f}"]
    if view_count:
        reason_parts.append(f"views {view_count:,}")
    return {
        "source_score": round(source_score, 2),
        "relevance_score": round(relevance, 2),
        "format_score": round(format_score, 2),
        "duration_score": round(duration_score, 2),
        "duration_sec": duration,
        "view_count": view_count,
        "ranking_reason": "; ".join(reason_parts),
    }


def rank_theme_sources(
    entries: list[dict],
    *,
    query: str = "",
    min_duration_sec: float = 600.0,
    exclude_source_keys: set[str] | None = None,
) -> list[ThemeSearchHit]:
    """
    Ranqueia fontes por relevância + formato falado + duração + views logarítmicas.
    `query` é passado como argumento opcional para manter compatibilidade com chamadas antigas.
    """
    return _rank_theme_sources(
        entries,
        query=query,
        min_duration_sec=min_duration_sec,
        exclude_source_keys=exclude_source_keys,
    )


def _rank_theme_sources(
    entries: list[dict],
    *,
    query: str,
    min_duration_sec: float,
    exclude_source_keys: set[str] | None,
) -> list[ThemeSearchHit]:
    usable: list[tuple[dict, str, float]] = []
    known_duration_count = 0
    for data in entries:
        if not isinstance(data, dict):
            continue
        duration_raw = data.get("duration")
        try:
            duration_sec = float(duration_raw) if duration_raw is not None else 0.0
        except (TypeError, ValueError):
            duration_sec = 0.0
        if duration_sec > 0:
            known_duration_count += 1
        if duration_sec > 0 and duration_sec < min_duration_sec:
            continue
        url = (data.get("webpage_url") or data.get("url") or "").strip()
        vid = (data.get("id") or "").strip()
        if (not url or not url.startswith(("http://", "https://"))) and vid and not vid.startswith(
            "http"
        ):
            url = f"https://www.youtube.com/watch?v={vid}"
        if not url or url.startswith("ytsearch"):
            continue
        try:
            if exclude_source_keys and canonical_source_key(url) in exclude_source_keys:
                continue
        except (ValueError, UnicodeError):
            pass
        usable.append((data, url, duration_sec))

    # Com duração conhecida, não arriscamos escolher um resultado curto/indefinido. Se todos
    # vierem sem duração, ainda assim a busca funciona com uma penalidade neutra.
    if known_duration_count:
        usable = [item for item in usable if item[2] <= 0 or item[2] >= min_duration_sec]
        usable = [item for item in usable if item[2] > 0]
    if not usable:
        return []
    view_values: list[int] = []
    for data, _url, _duration in usable:
        try:
            view_values.append(max(0, int(data.get("view_count") or 0)))
        except (TypeError, ValueError):
            view_values.append(0)

    hits: list[ThemeSearchHit] = []
    for data, url, duration_sec in usable:
        score = _source_scores(data, query, view_values=view_values)
        title = (data.get("title") or "sem título").strip() or "sem título"
        channel = (data.get("channel") or data.get("uploader") or "").strip() or None
        hits.append(
            ThemeSearchHit(
                url=url,
                title=title,
                view_count=int(score["view_count"]),
                duration_sec=duration_sec,
                channel=channel,
                source_score=float(score["source_score"]),
                relevance_score=float(score["relevance_score"]),
                format_score=float(score["format_score"]),
                ranking_reason=str(score["ranking_reason"]),
            )
        )
    return sorted(hits, key=lambda hit: (hit.source_score, hit.view_count), reverse=True)


def pick_top_viewed_among_long(
    entries: list[dict],
    *,
    min_duration_sec: float = 600.0,
    exclude_source_keys: set[str] | None = None,
    query: str = "",
) -> ThemeSearchHit | None:
    """Compatibilidade histórica: agora devolve o primeiro do source_score."""
    ranked = _rank_theme_sources(
        entries,
        query=query,
        min_duration_sec=min_duration_sec,
        exclude_source_keys=exclude_source_keys,
    )
    return ranked[0] if ranked else None


def search_youtube_top_by_views(
    query: str,
    *,
    min_duration_sec: float | None = None,
    search_n: int | None = None,
) -> ThemeSearchHit:
    """
    Busca no YouTube via yt-dlp (`ytsearchN:query`), filtra vídeos longos e
    devolve o primeiro pelo `source_score`. Não baixa o vídeo.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("Tema de busca vazio.")

    ytdlp_cmd = resolve_ytdlp_cmd()
    if not ytdlp_cmd:
        raise FileNotFoundError(
            "yt-dlp não encontrado. Instale: pip install -U 'yt-dlp[default]' "
            "(ou defina YTDLP_PATH no ambiente)."
        )

    min_dur = _theme_min_duration_sec(min_duration_sec)
    n = _theme_search_n(search_n)
    search_target = f"ytsearch{n}:{q}"
    cookies = _cookie_argv()
    child_env = _ytdlp_subprocess_env()
    cmd: list[str] = [
        *ytdlp_cmd,
        "--flat-playlist",
        "--dump-json",
        "--skip-download",
        "--no-warnings",
        *cookies,
        search_target,
    ]
    _log.info("Busca YouTube por tema: %s (N=%s, min_dur=%ss)", q, n, int(min_dur))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=child_env,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Busca YouTube expirou para tema «{q}».") from e

    entries: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            entries.append(data)

    if proc.returncode != 0 and not entries:
        err = (proc.stderr or "").strip() or f"código {proc.returncode}"
        raise RuntimeError(f"yt-dlp falhou na busca por tema «{q}»: {err[:300]}")

    used_source_keys = get_source_history().used_keys()
    hit = pick_top_viewed_among_long(
        entries,
        min_duration_sec=min_dur,
        exclude_source_keys=used_source_keys,
        query=q,
    )
    if hit is None:
        mins = max(1, int(round(min_dur / 60.0)))
        raise RuntimeError(
            f"Nenhum vídeo novo com ≥ {mins} min encontrado para «{q}» "
            f"(avaliados {len(entries)} resultado(s) da busca)."
        )
    _log.info(
        "Tema «%s» → %s (source_score %.2f, %s views, %.0f s)",
        q,
        hit.title,
        hit.source_score,
        f"{hit.view_count:,}",
        hit.duration_sec,
    )
    return hit


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
        if not u:
            continue
        try:
            key = canonical_source_key(u)
        except (ValueError, UnicodeError):
            key = u
        if key not in seen:
            seen.add(key)
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


def _download_video_untracked(
    url: str,
    dest_dir: str | Path,
    *,
    no_playlist: bool = True,
) -> DownloadResult:
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


def download_video(url: str, dest_dir: str | Path, *, no_playlist: bool = True) -> DownloadResult:
    """Baixa uma fonte nova ou reutiliza o arquivo local já baixado."""

    history = get_source_history()
    existing = history.get_downloaded(url)
    if existing is not None:
        _log.info("Fonte já baixada; reutilizando arquivo local: %s", existing.path)
        attribution = (
            VideoSourceAttribution(channel=existing.channel, source_url=url.strip())
            if existing.channel
            else None
        )
        return DownloadResult(path=existing.path, attribution=attribution)

    source_key = history.claim(url)
    try:
        result = _download_video_untracked(url, dest_dir, no_playlist=no_playlist)
    except Exception as exc:
        history.mark_failed(source_key, str(exc))
        raise

    channel = result.attribution.channel if result.attribution else None
    history.mark_downloaded(
        source_key,
        downloaded_path=result.path,
        channel=channel,
    )
    return result
