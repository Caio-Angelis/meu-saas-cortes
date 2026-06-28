"""Factory FastAPI — templates, estáticos e routers."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.web.routers import jobs, playlist, progress, runs

_WEB_ROOT = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_ROOT / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(
        title="Cortes Virais — Web Local",
        description="Interface web local alternativa à GUI Tkinter.",
        version="0.1.0",
    )

    static_dir = _WEB_ROOT / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(jobs.router)
    app.include_router(playlist.router)
    app.include_router(progress.router)
    app.include_router(runs.router)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={"title": "Cortes Virais"},
        )

    return app
