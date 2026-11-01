"""Upload e análise dos relatórios de desempenho na interface web."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.analytics.performance import analyze_performance_csv
from app.analytics.retention_loop import analyze_retention_report_file, save_growth_profile
from app.web.worker import save_uploaded_file

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.post("/performance")
async def analyze_performance(
    file: UploadFile = File(...),  # noqa: B008
    use_ai: bool = Form(True),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Envie um arquivo CSV de desempenho.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="O CSV está vazio.")
    path = save_uploaded_file(content, file.filename)
    try:
        report = await asyncio.to_thread(analyze_performance_csv, path, use_ai=use_ai)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "summary": report.summary,
        "row_count": report.row_count,
        "valid_row_count": report.valid_row_count,
        "mapped_columns": report.mapped_columns,
        "used_ai": report.used_ai,
        "recommendations": [asdict(item) for item in report.recommendations],
    }


@router.post("/retention")
async def analyze_retention(file: UploadFile = File(...)) -> dict:  # noqa: B008
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Envie um relatório JSON do TikTok.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="O JSON está vazio.")
    path = save_uploaded_file(content, file.filename)
    try:
        insight = await asyncio.to_thread(analyze_retention_report_file, path)
        profile = await asyncio.to_thread(save_growth_profile, insight)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = insight.to_dict()
    data["profile_path"] = str(profile)
    return data
