from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any


def _default_cache_dir() -> Path:
    """
    Cache persistente (não vai para temp/). Segue convenção XDG em Linux/macOS.
    Em Windows usa LOCALAPPDATA quando disponível.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "meu_saas_cortes"


def cache_dir() -> Path:
    raw = (os.getenv("CACHE_DIR") or "").strip()
    return Path(raw).expanduser() if raw else _default_cache_dir()


def _atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_json(path: Path, data: Any) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def fingerprint_file(path: str | Path, *, sample_bytes: int = 1024 * 1024) -> str:
    """
    Fingerprint estável e relativamente rápido:
    - tamanho + mtime_ns
    - sha256 do primeiro e último bloco (1MB por padrão)
    Isso evita ler o arquivo inteiro (vídeos grandes).
    """
    p = Path(path)
    st = p.stat()
    size = int(st.st_size)
    mtime = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))

    h = hashlib.sha256()
    h.update(f"{size}:{mtime}:".encode())

    if size <= sample_bytes * 2:
        with p.open("rb") as f:
            h.update(f.read())
        return h.hexdigest()

    with p.open("rb") as f:
        head = f.read(sample_bytes)
        h.update(head)
        if size > sample_bytes:
            try:
                f.seek(max(0, size - sample_bytes))
            except OSError:
                f.seek(0)
            tail = f.read(sample_bytes)
            h.update(tail)

    return h.hexdigest()


def key_hash(*parts: Any) -> str:
    """
    Hash determinístico para chaves de cache.
    Aceita qualquer coisa serializável (dict/list/str/int/float/bool/None).
    """
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def cache_path(namespace: str, key: str, *, ext: str = ".json") -> Path:
    safe_ns = "".join(c for c in namespace if c.isalnum() or c in ("-", "_"))[:80] or "cache"
    return cache_dir() / safe_ns / f"{key}{ext}"

