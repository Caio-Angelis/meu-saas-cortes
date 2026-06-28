"""
Ajustes mínimos de ambiente para desktop Linux.

Chamado no início de gui.py / main.py, antes de importar o pipeline, para reduzir
mensagens nativas no stderr (TensorFlow Lite / absl), alinhar threads de BLAS e,
em setups AMD híbridos, inclinar o Mesa a usar a GPU dedicada (DRI_PRIME).
Não altera a lógica do pipeline — só variáveis de processo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _linux_has_nvidia_driver() -> bool:
    return Path("/proc/driver/nvidia/version").is_file()


def apply_linux_desktop_defaults() -> None:
    if not sys.platform.startswith("linux"):
        return
    # Mesa (OpenGL/EGL): só em AMD híbrido — com NVIDIA dedicada, DRI_PRIME atrapalha.
    if not _linux_has_nvidia_driver():
        os.environ.setdefault("DRI_PRIME", "1")
    # TensorFlow Lite (MediaPipe): menos ruído no terminal antes de absl::InitializeLog
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("GLOG_minloglevel", "2")
    # Ryzen 5600G (6C/12T): evita oversubscription em numpy/OpenBLAS ao rodar FFmpeg em paralelo
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
