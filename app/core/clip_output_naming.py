"""Nomes de arquivos finais em resultados/ (clipe + vídeo original)."""

import re

_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def sanitize_clip_output_stem(name: str, *, max_len: int = 160) -> str:
    """
    Parte do nome do arquivo em resultados/ (sem índice do corte).
    Remove caracteres inválidos em nomes de arquivo e limita o tamanho.
    """
    s = (name or "").strip()
    if not s:
        s = "video"
    s = _FILENAME_UNSAFE.sub("_", s)
    s = re.sub(r"_+", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" ._")
    if not s:
        s = "video"
    if len(s) > max_len:
        s = s[:max_len].rstrip(" ._")
    return s or "video"
