"""Helpers para expor saídas locais com segurança na interface web."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from app.core.config import OUTPUT_DIR


def output_media_url(path: str | Path) -> str | None:
    """Converte uma saída dentro de ``resultados/`` para a URL local correspondente."""
    output_root = OUTPUT_DIR.resolve()
    candidate = Path(path).expanduser().resolve()
    try:
        relative = candidate.relative_to(output_root)
    except ValueError:
        return None
    return "/media/" + quote(relative.as_posix(), safe="/")


def output_media_urls(paths: list[str] | tuple[str, ...]) -> list[str | None]:
    return [output_media_url(path) for path in paths]
