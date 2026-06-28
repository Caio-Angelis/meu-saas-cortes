"""Cache em disco: JSON atômico, fingerprint e chaves determinísticas."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.cache import cache_path, fingerprint_file, key_hash, read_json, sha256_bytes, write_json


def test_sha256_bytes_stable() -> None:
    assert sha256_bytes(b"x") == sha256_bytes(b"x")
    assert sha256_bytes(b"x") != sha256_bytes(b"y")


def test_key_hash_dict_key_order_invariant() -> None:
    assert key_hash({"b": 1, "a": 2}) == key_hash({"a": 2, "b": 1})


def test_write_read_json_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "d" / "x.json"
    payload = {"ok": True, "items": [1, None, "z"]}
    write_json(p, payload)
    assert read_json(p) == payload


def test_read_json_missing_returns_none(tmp_path: Path) -> None:
    assert read_json(tmp_path / "nope.json") is None


def test_read_json_invalid_returns_none(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_json(bad) is None


def test_fingerprint_file_repeatable(tmp_path: Path) -> None:
    f = tmp_path / "v.mp4"
    f.write_bytes(b"hello" * 2000)
    assert fingerprint_file(f, sample_bytes=64) == fingerprint_file(f, sample_bytes=64)


def test_fingerprint_small_file_reads_whole(tmp_path: Path) -> None:
    f = tmp_path / "small.bin"
    f.write_bytes(b"x" * 50)
    a = fingerprint_file(f, sample_bytes=64)
    f.write_bytes(b"x" * 49 + b"y")
    b = fingerprint_file(f, sample_bytes=64)
    assert a != b


def test_cache_path_respects_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    p = cache_path("segments", "abc123", ext=".json")
    assert p.parent.name == "segments"
    assert p.name == "abc123.json"
    assert tmp_path in p.parents


def test_cache_path_sanitizes_namespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    p = cache_path("weird/ns!@", "k")
    assert p.parent.name == "weirdns"
