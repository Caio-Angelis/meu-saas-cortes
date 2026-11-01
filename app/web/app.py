"""Factory FastAPI — templates, estáticos e routers."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import OUTPUT_DIR, TEMP_DIR
from app.web.routers import analytics, jobs, playlist, progress, publish, runs, studio

_WEB_ROOT = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_ROOT / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(
        title="Cortes Lab — Creative Automation Studio",
        description="Studio web local-first para criação, edição e distribuição de vídeos.",
        version="2.0.0",
    )

    static_dir = _WEB_ROOT / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(OUTPUT_DIR)), name="media")
    app.mount("/preview-media", StaticFiles(directory=str(TEMP_DIR)), name="preview-media")

    app.include_router(jobs.router)
    app.include_router(playlist.router)
    app.include_router(progress.router)
    app.include_router(runs.router)
    app.include_router(studio.router)
    app.include_router(analytics.router)
    app.include_router(publish.router)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={"title": "Cortes Lab"},
        )

    return app
