"""Timestamps SRT (HH:MM:SS,mmm)."""

from __future__ import annotations

from app.subtitle.formatter import seconds_to_srt_timestamp


def test_seconds_zero() -> None:
    assert seconds_to_srt_timestamp(0.0) == "00:00:00,000"


def test_seconds_with_fractional_ms() -> None:
    assert seconds_to_srt_timestamp(1.501) == "00:00:01,501"


def test_negative_seconds_clamped_to_zero() -> None:
    assert seconds_to_srt_timestamp(-5.0) == "00:00:00,000"
