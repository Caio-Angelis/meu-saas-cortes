#!/usr/bin/env bash
# Instala TTS local (Kokoro) com suporte GPU NVIDIA, incluindo RTX 50xx (Blackwell / sm_120).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
PIP="${ROOT}/.venv/bin/pip"

if [[ ! -x "$PY" ]]; then
  echo "Crie o venv primeiro: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

echo "→ PyTorch CUDA 12.8 (RTX 50xx / Blackwell)…"
"$PIP" install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

echo "→ Kokoro TTS + soundfile…"
"$PIP" install -r "${ROOT}/requirements-local-tts.txt"

echo "→ Verificando GPU…"
"$PY" - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda:", torch.cuda.is_available(), end="")
if torch.cuda.is_available():
    print(" —", torch.cuda.get_device_name(0), torch.cuda.get_device_capability())
else:
    print()
PY

echo "→ Teste rápido de síntese…"
"$PY" - <<'PY'
from pathlib import Path
from app.local_tts import local_tts_save_to_path

out = Path("/tmp/kokoro_install_test.mp3")
local_tts_save_to_path("Teste de voz local em português.", out, voice="pf_dora")
print("OK:", out, out.stat().st_size, "bytes")
PY

echo "Pronto. Reinicie a GUI e escolha uma voz «Kokoro local» no combobox."
