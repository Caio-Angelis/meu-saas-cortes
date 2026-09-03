"""Reexecuta o script atual com o ambiente isolado configurado para o projeto."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# Os lançadores do projeto não devem espalhar bytecode pelo diretório do app.
sys.dont_write_bytecode = True


def ensure_venv(script_path: str | Path) -> None:
    root = Path(script_path).resolve().parent
    # Mantém o ambiente fora do projeto quando ele não tiver um .venv local.
    venv_homes = (root / ".venv", Path.home() / ".venvs" / root.name)
    for venv_home in venv_homes:
        if sys.platform == "win32":
            venv_py = venv_home / "Scripts" / "python.exe"
        else:
            venv_py = venv_home / "bin" / "python"
        if not venv_py.is_file():
            continue
        try:
            # O que importa é sys.prefix, inclusive quando o executável é symlink.
            if Path(sys.prefix).resolve() == venv_home.resolve():
                return
        except OSError:
            continue
        script = Path(script_path).resolve()
        child_env = os.environ.copy()
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        raise SystemExit(
            subprocess.call([str(venv_py), str(script), *sys.argv[1:]], env=child_env)
        )
