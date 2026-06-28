from pathlib import Path

from app.gui.gui_export import export_cortes_zip, format_duration_hms


def test_format_duration_hms():
    assert format_duration_hms(None) == "—"
    assert format_duration_hms(45.2) == "0:45"
    assert format_duration_hms(125) == "2:05"


def test_export_cortes_zip_writes_readme_and_mp4(tmp_path: Path):
    mp4 = tmp_path / "1_teste.mp4"
    mp4.write_bytes(b"fake")
    txt = tmp_path / "1_teste.txt"
    txt.write_text("caption\n", encoding="utf-8")
    out = export_cortes_zip([str(mp4)], tmp_path)
    assert out is not None
    assert out.suffix == ".zip"
    assert out.is_file()


def test_export_cortes_zip_empty_returns_none(tmp_path: Path):
    assert export_cortes_zip([], tmp_path) is None
    assert export_cortes_zip([str(tmp_path / "missing.mp4")], tmp_path) is None
