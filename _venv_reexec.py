"""Reexecuta o script atual com o Python do .venv, se existir e não for o ativo."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def ensure_venv(script_path: str | Path) -> None:
    root = Path(script_path).resolve().parent
    venv_home = root / ".venv"
    if sys.platform == "win32":
        venv_py = venv_home / "Scripts" / "python.exe"
    else:
        venv_py = venv_home / "bin" / "python"
    if not venv_py.is_file():
        return
    try:
        # .venv/bin/python costuma ser symlink para /usr/bin/python3; o que importa é sys.prefix.
        if Path(sys.prefix).resolve() == venv_home.resolve():
            return
    except OSError:
        return
    script = Path(script_path).resolve()
    raise SystemExit(subprocess.call([str(venv_py), str(script), *sys.argv[1:]]))
