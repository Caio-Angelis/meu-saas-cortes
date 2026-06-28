"""Cliente ComfyUI — geração de vídeo via workflow_historia.json (stdlib only)."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COMFYUI_BASE_URL = "http://127.0.0.1:8188"
POLL_INTERVAL_SEC = 1.75
POLL_TIMEOUT_SEC = 900
LATENT_NODE_ID = "1"
OUTPUT_NODE_ID = "8"
PROMPT_NODE_ID = "4"
SEED_NODE_ID = "6"
FACE_DETAILER_SEED_NODE_ID = "13"

# Geração ComfyUI: 9:16 nativo SD 1.5 (512×896, múltiplos de 64) @ 3 fps
HISTORIA_FRAME_RATE = 3
HISTORIA_LATENT_WIDTH = 512
HISTORIA_LATENT_HEIGHT = 896


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_workflow() -> dict:
    workflow_path = _repo_root() / "workflow_historia.json"
    if not workflow_path.is_file():
        raise FileNotFoundError(f"Workflow não encontrado: {workflow_path}")
    with workflow_path.open(encoding="utf-8") as handle:
        workflow = json.load(handle)
    if not isinstance(workflow, dict):
        raise ValueError("workflow_historia.json deve ser um objeto JSON")
    return workflow


def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace").strip()
        hint = detail[:1200] if detail else e.reason
        raise RuntimeError(
            f"ComfyUI rejeitou o workflow (HTTP {e.code}): {hint}"
        ) from e
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Resposta inválida do ComfyUI em POST /prompt")
    return data


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"Resposta inválida do ComfyUI em GET {url}")
    return data


def _download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def _inject_workflow_params(workflow: dict, prompt_visual: str) -> dict:
    if PROMPT_NODE_ID not in workflow or SEED_NODE_ID not in workflow:
        raise ValueError("Workflow deve conter os nós 4 (prompt) e 6 (seed)")

    prompt_node = workflow[PROMPT_NODE_ID]
    seed_node = workflow[SEED_NODE_ID]
    if not isinstance(prompt_node, dict) or not isinstance(seed_node, dict):
        raise ValueError("Nós 4 e 6 devem ser objetos JSON")

    prompt_inputs = prompt_node.setdefault("inputs", {})
    seed_inputs = seed_node.setdefault("inputs", {})
    if not isinstance(prompt_inputs, dict) or not isinstance(seed_inputs, dict):
        raise ValueError("inputs dos nós 4 e 6 devem ser objetos JSON")

    prompt_inputs["text"] = (prompt_visual or "").strip()
    seed_value = random.randint(0, 2**32 - 1)
    seed_inputs["seed"] = seed_value

    if FACE_DETAILER_SEED_NODE_ID in workflow:
        face_seed_node = workflow[FACE_DETAILER_SEED_NODE_ID]
        if isinstance(face_seed_node, dict):
            face_seed_inputs = face_seed_node.setdefault("inputs", {})
            if isinstance(face_seed_inputs, dict):
                face_seed_inputs["seed"] = seed_value

    if LATENT_NODE_ID in workflow:
        latent_node = workflow[LATENT_NODE_ID]
        if isinstance(latent_node, dict):
            latent_inputs = latent_node.setdefault("inputs", {})
            if isinstance(latent_inputs, dict):
                latent_inputs["width"] = HISTORIA_LATENT_WIDTH
                latent_inputs["height"] = HISTORIA_LATENT_HEIGHT

    if OUTPUT_NODE_ID in workflow:
        video_node = workflow[OUTPUT_NODE_ID]
        if isinstance(video_node, dict):
            video_inputs = video_node.setdefault("inputs", {})
            if isinstance(video_inputs, dict):
                video_inputs["frame_rate"] = HISTORIA_FRAME_RATE

    return workflow


def _wait_for_history(prompt_id: str) -> dict:
    history_url = f"{COMFYUI_BASE_URL}/history/{prompt_id}"
    deadline = time.monotonic() + POLL_TIMEOUT_SEC

    while time.monotonic() < deadline:
        history = _get_json(history_url)
        if prompt_id in history:
            entry = history[prompt_id]
            if isinstance(entry, dict):
                return entry
            raise RuntimeError(f"Entrada de histórico inválida para prompt_id={prompt_id!r}")

        time.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError(
        f"ComfyUI não concluiu o job {prompt_id!r} em {POLL_TIMEOUT_SEC}s"
    )


def _extract_output_file(history_entry: dict) -> tuple[str, str]:
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        raise RuntimeError("Histórico do ComfyUI não contém 'outputs'")

    node_output = outputs.get(OUTPUT_NODE_ID)
    if not isinstance(node_output, dict):
        raise RuntimeError(f"Nó {OUTPUT_NODE_ID} não possui saída no histórico")

    for media_key in ("gifs", "images"):
        items = node_output.get(media_key)
        if not isinstance(items, list) or not items:
            continue
        first = items[0]
        if not isinstance(first, dict):
            continue
        filename = first.get("filename")
        if not filename:
            continue
        subfolder = first.get("subfolder", "")
        return str(filename), str(subfolder or "")

    raise RuntimeError(
        f"Nó {OUTPUT_NODE_ID} não retornou arquivo em 'gifs' nem 'images'"
    )


def gerar_video_comfyui(prompt_visual: str, output_path: Path) -> None:
    """Envia workflow ao ComfyUI, aguarda conclusão e grava o MP4 em ``output_path``."""
    workflow = _load_workflow()
    _inject_workflow_params(workflow, prompt_visual)

    prompt_response = _post_json(
        f"{COMFYUI_BASE_URL}/prompt",
        {"prompt": workflow},
    )
    prompt_id = prompt_response.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI não retornou prompt_id após POST /prompt")

    history_entry = _wait_for_history(str(prompt_id))
    filename, subfolder = _extract_output_file(history_entry)

    query = urllib.parse.urlencode(
        {
            "filename": filename,
            "subfolder": subfolder,
            "type": "output",
        }
    )
    download_url = f"{COMFYUI_BASE_URL}/view?{query}"
    video_bytes = _download_bytes(download_url)
    if not video_bytes:
        raise RuntimeError(f"Download vazio para {filename!r}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(video_bytes)
