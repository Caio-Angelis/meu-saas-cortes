"""Heurística de uso de encoder GPU por índice de clipe."""

from __future__ import annotations

import pytest

from app.pipelines.cortes.pipeline import _clip_uses_gpu_encoder


@pytest.fixture
def gpu_on(monkeypatch: pytest.MonkeyPatch):
    import app.pipelines.cortes.pipeline as pl

    monkeypatch.setattr(pl, "USE_GPU_CLIP_ENCODE", True)
    monkeypatch.setattr(pl, "CLIP_ENCODE_PARALLEL_CPU", 2)
    monkeypatch.setattr(pl, "CLIP_ENCODE_PARALLEL_GPU", 2)


def test_gpu_off_never(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.pipelines.cortes.pipeline as pl

    monkeypatch.setattr(pl, "USE_GPU_CLIP_ENCODE", False)
    assert _clip_uses_gpu_encoder(99, 10) is False


def test_gpu_on_all_clips_use_gpu(gpu_on) -> None:
    # Agora todos os clipes usam GPU quando USE_GPU_CLIP_ENCODE está ligado.
    assert _clip_uses_gpu_encoder(1, 10) is True
    assert _clip_uses_gpu_encoder(5, 10) is True
    assert _clip_uses_gpu_encoder(10, 10) is True
    assert _clip_uses_gpu_encoder(1, 1) is True
