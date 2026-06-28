"""Resolução automática de encoder GPU (NVIDIA vs AMD no Linux)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_resolved_encoder_prefers_nvenc_with_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as cfg

    monkeypatch.delenv("CLIP_GPU_ENCODER", raising=False)
    monkeypatch.setattr(cfg, "linux_has_nvidia_driver", lambda: True)
    monkeypatch.setattr(
        cfg,
        "_linux_h264_hw_encoders_in_ffmpeg",
        lambda: frozenset({"h264_nvenc", "h264_vaapi"}),
    )
    assert cfg._resolved_clip_gpu_encoder() == "h264_nvenc"


def test_resolved_encoder_prefers_vaapi_on_amd(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as cfg

    monkeypatch.delenv("CLIP_GPU_ENCODER", raising=False)
    monkeypatch.setattr(cfg, "linux_has_nvidia_driver", lambda: False)
    monkeypatch.setattr(
        cfg,
        "_linux_h264_hw_encoders_in_ffmpeg",
        lambda: frozenset({"h264_nvenc", "h264_vaapi"}),
    )
    assert cfg._resolved_clip_gpu_encoder() == "h264_vaapi"


def test_resolved_encoder_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as cfg

    monkeypatch.setenv("CLIP_GPU_ENCODER", "h264_qsv")
    assert cfg._resolved_clip_gpu_encoder() == "h264_qsv"


def test_default_parallel_gpu_higher_with_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "linux_has_nvidia_driver", lambda: True)
    assert cfg._default_clip_encode_parallel_gpu() == 3


def test_linux_has_nvidia_driver_matches_proc_file() -> None:
    from app.core.config import linux_has_nvidia_driver

    assert linux_has_nvidia_driver() == Path("/proc/driver/nvidia/version").is_file()
