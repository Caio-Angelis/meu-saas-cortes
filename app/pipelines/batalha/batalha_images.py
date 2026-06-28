"""
Utilitários de imagem para Batalha 1v1: busca na web, recorte circular e fallback.
"""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_log = logging.getLogger("batalha_images")

_DEFAULT_AVATAR_SIZE = 256
_DOWNLOAD_TIMEOUT_SEC = 18.0
_USER_AGENT = "meu_saas_cortes/1.0 (Batalha1v1 local pipeline; +https://www.mediawiki.org/)"
_WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
_WIKIPEDIA_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_PREFERRED_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


def normalize_hex_color(value: str, *, default: str = "#4A90D9") -> str:
    """Normaliza cor para #RRGGBB; aceita RRGGBB sem #."""
    raw = (value or "").strip()
    if not raw:
        return default.upper()
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
        return raw.upper()
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", raw):
        r, g, b = raw[1], raw[2], raw[3]
        return f"#{r}{r}{g}{g}{b}{b}".upper()
    return default.upper()


def hex_to_rgb(hex_color: str, *, default: tuple[int, int, int] = (74, 144, 217)) -> tuple[int, int, int]:
    normalized = normalize_hex_color(hex_color, default=f"#{default[0]:02X}{default[1]:02X}{default[2]:02X}")
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
    )


def _opponent_initial(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "?").strip())
    if not cleaned:
        return "?"
    for ch in cleaned:
        if ch.isalnum():
            return ch.upper()
    return "?"


def apply_circular_mask(img: Image.Image, *, size: int | None = None) -> Image.Image:
    """Redimensiona para quadrado e aplica máscara circular (RGBA)."""
    target = max(32, int(size or _DEFAULT_AVATAR_SIZE))
    src = img.convert("RGBA")
    w, h = src.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = src.crop((left, top, left + side, top + side))
    square = cropped.resize((target, target), Image.Resampling.LANCZOS)

    mask = Image.new("L", (target, target), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, target - 1, target - 1), fill=255)

    out = Image.new("RGBA", (target, target), (0, 0, 0, 0))
    out.paste(square, (0, 0), mask)
    return out


def make_initial_fallback_avatar(
    name: str,
    color_hex: str,
    *,
    size: int = _DEFAULT_AVATAR_SIZE,
) -> Image.Image:
    """Plano B: círculo sólido com a inicial do oponente."""
    target = max(32, int(size))
    rgb = hex_to_rgb(color_hex)
    img = Image.new("RGBA", (target, target), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = max(2, target // 32)
    draw.ellipse((margin, margin, target - margin - 1, target - margin - 1), fill=(*rgb, 255))

    initial = _opponent_initial(name)
    font_size = max(24, target // 2)
    font: ImageFont.ImageFont
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), initial, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (target - tw) // 2 - bbox[0]
    y = (target - th) // 2 - bbox[1]
    draw.text((x, y), initial, fill=(255, 255, 255, 255), font=font)
    return apply_circular_mask(img, size=target)


def download_image_bytes(url: str, *, timeout_sec: float = _DOWNLOAD_TIMEOUT_SEC) -> bytes:
    req = urllib.request.Request(  # noqa: S310 — URL vinda da busca de imagens
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
        return resp.read()


def _http_get_json(url: str, *, timeout_sec: float = _DOWNLOAD_TIMEOUT_SEC) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
        data = json.loads(resp.read())
    return data if isinstance(data, dict) else {}


def _logo_search_queries(display_name: str, search_term: str) -> list[str]:
    name = re.sub(r"\s+", " ", (display_name or "").strip())
    term = re.sub(r"\s+", " ", (search_term or "").strip())
    ordered = [
        term,
        ensure_logo_search_term(name, term),
        f"{name} logo" if name else "",
        name,
        f"{name} symbol" if name else "",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for q in ordered:
        key = q.casefold()
        if not q or key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def _wikimedia_commons_image_urls(search_term: str, *, limit: int = 6) -> list[str]:
    """Busca arquivos no Wikimedia Commons (logos oficiais, PNG/SVG com miniatura)."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": search_term,
        "gsrnamespace": "6",
        "gsrlimit": str(max(3, limit)),
        "prop": "imageinfo",
        "iiprop": "url|mime|thumb",
        "iiurlwidth": "640",
    }
    url = f"{_WIKIMEDIA_API}?{urllib.parse.urlencode(params)}"
    try:
        data = _http_get_json(url)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        _log.warning("Commons API falhou para %r: %s", search_term, e)
        return []

    pages = data.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        return []

    ranked: list[tuple[int, str]] = []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "").casefold()
        for info in page.get("imageinfo") or []:
            if not isinstance(info, dict):
                continue
            mime = str(info.get("mime") or "")
            thumb = (info.get("thumburl") or "").strip()
            direct = (info.get("url") or "").strip()
            pick = thumb or direct
            if not pick.startswith(("http://", "https://")):
                continue
            if mime == "image/svg+xml" and not thumb:
                continue
            score = 0
            if mime in _PREFERRED_IMAGE_MIMES:
                score += 4
            if thumb:
                score += 2
            if "logo" in title or "logo" in pick.casefold():
                score += 5
            if "symbol" in title or "emblem" in title:
                score += 2
            if "poster" in title or "actor" in title or "film still" in title:
                score -= 3
            ranked.append((score, pick))

    ranked.sort(key=lambda t: t[0], reverse=True)
    seen_urls: set[str] = set()
    urls: list[str] = []
    for _score, img_url in ranked:
        if img_url in seen_urls:
            continue
        seen_urls.add(img_url)
        urls.append(img_url)
        if len(urls) >= limit:
            break
    return urls


def _wikipedia_summary_thumbnail(title: str) -> str | None:
    slug = re.sub(r"\s+", "_", (title or "").strip())
    if not slug:
        return None
    url = _WIKIPEDIA_SUMMARY_API + urllib.parse.quote(slug, safe="")
    try:
        data = _http_get_json(url)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        _log.debug("Wikipedia summary falhou para %r: %s", title, e)
        return None
    thumb = data.get("thumbnail") if isinstance(data.get("thumbnail"), dict) else {}
    src = (thumb.get("source") or "").strip()
    if src.startswith(("http://", "https://")):
        return src
    return None


def _search_image_url_duckduckgo(search_term: str) -> str | None:
    term = (search_term or "").strip()
    if not term:
        return None
    try:
        from duckduckgo_search import DDGS
    except ImportError as e:
        _log.warning("duckduckgo-search não instalado: %s", e)
        return None

    queries = [term]
    if "logo" not in term.lower():
        queries.append(ensure_logo_search_term(term, term))

    try:
        with DDGS() as ddgs:
            for query in queries:
                for item in ddgs.images(query, max_results=8, safesearch="moderate"):
                    if not isinstance(item, dict):
                        continue
                    url = (item.get("image") or item.get("thumbnail") or "").strip()
                    if url.startswith(("http://", "https://")):
                        return url
    except Exception as e:
        _log.warning("DuckDuckGo imagens falhou para %r: %s", term, e)
    return None


def collect_logo_image_urls(display_name: str, search_term: str, *, max_urls: int = 12) -> list[str]:
    """Ordena candidatos: Commons (várias consultas) → Wikipedia → DuckDuckGo."""
    seen: set[str] = set()
    urls: list[str] = []

    def add(candidate: str | None) -> None:
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        urls.append(candidate)

    for query in _logo_search_queries(display_name, search_term):
        for img_url in _wikimedia_commons_image_urls(query, limit=4):
            add(img_url)
            if len(urls) >= max_urls:
                return urls

    add(_wikipedia_summary_thumbnail(display_name))

    if not urls:
        add(_search_image_url_duckduckgo(search_term or display_name))

    return urls[:max_urls]


def load_image_from_bytes(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        return im.convert("RGBA")


def ensure_logo_search_term(display_name: str, term: str = "") -> str:
    """Garante consulta de imagem focada em logo (ex.: «Batman» → «Batman logo»)."""
    name = re.sub(r"\s+", " ", (display_name or "").strip())
    query = re.sub(r"\s+", " ", (term or name).strip())
    if not query:
        query = name
    low = query.lower()
    if any(k in low for k in ("logo", "poster", "emblem", "icon", "symbol", "mark", "badge")):
        return query
    if not name:
        return query
    return f"{name} logo"


def prepare_victory_logo(img: Image.Image, *, max_side: int = 520) -> Image.Image:
    """Redimensiona logo/poster para a tela final, preservando proporção (sem máscara circular)."""
    src = img.convert("RGBA")
    w, h = src.size
    if max(w, h) <= max_side:
        return src
    scale = max_side / max(w, h)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    return src.resize((nw, nh), Image.Resampling.LANCZOS)


def cleanup_batalha_downloaded_assets(work_dir: Path) -> None:
    """Remove avatares e logos baixados após o MP4 estar pronto."""
    base = Path(work_dir)
    for name in ("avatar_1.png", "avatar_2.png", "logo_1.png", "logo_2.png"):
        path = base / name
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            _log.warning("Não foi possível apagar %s: %s", path, e)


def _search_image_url(search_term: str, *, display_name: str = "") -> str | None:
    """Primeira URL candidata (compatibilidade com testes antigos)."""
    urls = collect_logo_image_urls(display_name or search_term, search_term, max_urls=1)
    return urls[0] if urls else None


def _download_opponent_image(
    search_term: str,
    display_name: str,
) -> Image.Image | None:
    """Tenta baixar a primeira imagem válida; None se todas falharem."""
    name = (display_name or search_term or "?").strip()
    candidates = collect_logo_image_urls(name, search_term)
    if not candidates:
        return None

    errors: list[str] = []
    for url in candidates:
        try:
            raw = download_image_bytes(url)
            loaded = load_image_from_bytes(raw)
            _log.info("Logo obtido para %r via %s", name, url[:96])
            return loaded
        except (urllib.error.URLError, OSError, ValueError, Image.UnidentifiedImageError) as e:
            errors.append(f"{url[:72]}: {e}")

    _log.warning(
        "Download de logo falhou para %r (%d URLs): %s",
        name,
        len(candidates),
        errors[0] if errors else "desconhecido",
    )
    return None


def fetch_opponent_graphics(
    search_term: str,
    display_name: str,
    fallback_color: str,
    *,
    size: int = _DEFAULT_AVATAR_SIZE,
) -> tuple[Image.Image, Image.Image]:
    """
    Retorna (avatar circular para bolinhas, logo para tela de vitória).

    Se a busca falhar, ambos usam o fallback com inicial.
    """
    name = (display_name or search_term or "?").strip()
    color = normalize_hex_color(fallback_color)
    loaded = _download_opponent_image(search_term, display_name)
    if loaded is not None:
        return apply_circular_mask(loaded, size=size), prepare_victory_logo(loaded)
    _log.info("Avatar fallback (sem URL) para %r — busca: %r", name, search_term)
    fallback = make_initial_fallback_avatar(name, color, size=size)
    return fallback, prepare_victory_logo(fallback)


def fetch_opponent_avatar(
    search_term: str,
    display_name: str,
    fallback_color: str,
    *,
    size: int = _DEFAULT_AVATAR_SIZE,
) -> Image.Image:
    """Busca imagem pelo termo, baixa, aplica máscara circular (bolinhas)."""
    return fetch_opponent_graphics(
        search_term, display_name, fallback_color, size=size
    )[0]


def save_avatar_png(img: Image.Image, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGBA").save(path, format="PNG")
    return path
