from pathlib import Path

import pytest

from app.analytics.performance import PerformanceAnalysisError, analyze_performance_csv


def test_analyze_pt_br_csv_groups_explicit_themes(tmp_path: Path):
    csv_path = tmp_path / "ultimos_7_dias.csv"
    csv_path.write_text(
        "Tema;Título;Visualizações;Curtidas;Comentários;Compartilhamentos;Salvamentos;Taxa de conclusão\n"
        "Cinema;Final explicado de Odisseia;12.000;980;90;210;150;68%\n"
        "Cinema;3 detalhes escondidos no filme;9.500;760;65;180;120;61%\n"
        "Tecnologia;Celular novo vale a pena;8.000;400;30;60;45;42%\n"
        "História;O império que desapareceu;7.000;500;55;100;80;57%\n",
        encoding="utf-8",
    )

    result = analyze_performance_csv(csv_path, use_ai=False)

    assert result.valid_row_count == 4
    assert result.mapped_columns["theme"] == "Tema"
    assert len(result.recommendations) == 3
    assert result.recommendations[0].theme == "Cinema"
    assert result.recommendations[0].score > result.recommendations[1].score
    assert result.used_ai is False


def test_analyze_tiktok_english_csv_without_theme_uses_titles(tmp_path: Path):
    csv_path = tmp_path / "tiktok.csv"
    csv_path.write_text(
        "Video title,Video views,Likes,Comments,Shares,Average watch time,Video duration,New followers\n"
        '"Mistérios do espaço","12,000",900,55,180,00:18,00:30,42\n'
        '"Curiosidades sobre Marte","9,500",720,44,130,00:16,00:28,30\n'
        '"Erros famosos do cinema","7,000",410,20,60,00:11,00:30,12\n',
        encoding="utf-8-sig",
    )

    result = analyze_performance_csv(csv_path, use_ai=False)

    assert result.valid_row_count == 3
    assert result.mapped_columns["content"] == "Video title"
    assert result.mapped_columns["views"] == "Video views"
    assert len(result.recommendations) == 3
    assert result.recommendations[0].theme == "Mistérios do espaço"
    assert "visualizações" in result.recommendations[0].evidence


def test_analysis_rejects_csv_without_exposure_metric(tmp_path: Path):
    csv_path = tmp_path / "invalido.csv"
    csv_path.write_text("Tema;Curtidas\nCinema;100\n", encoding="utf-8")

    with pytest.raises(PerformanceAnalysisError, match="visualizações"):
        analyze_performance_csv(csv_path, use_ai=False)
