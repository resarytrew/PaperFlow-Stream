"""PaperFlow Stream FastAPI application."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app.api import routes_camera, routes_catalog, routes_export, routes_review, routes_scan, routes_sessions
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services.events import OCR_TOPIC, hub
from app.services.ocr_queue import ocr_queue
from app.services.settings_service import load_config

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("paperflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()

    with SessionLocal() as db:
        config = load_config(db, use_cache=False)
        from app.services.seed import ensure_default_template

        ensure_default_template(db)

    await ocr_queue.start(config.ocr.concurrency)
    logger.info("%s %s ready — data dir: %s", settings.app_name, settings.version, settings.data_dir)
    try:
        yield
    finally:
        await ocr_queue.stop()


app = FastAPI(
    title="PaperFlow Stream API",
    version=settings.version,
    description="Локальная система потокового сканирования письменных ответов",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Form-Count"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to the browser; always log it locally."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Внутренняя ошибка: {type(exc).__name__}"})


@app.get("/api/health")
def health() -> dict:
    from app.cv.qr import get_backend
    from app.ocr.providers import get_provider

    qr_backends: dict[str, bool] = {}
    for name in ("opencv", "pyzbar", "zxing"):
        try:
            backend = get_backend(name)
            check = getattr(backend, "_check", None)
            qr_backends[name] = bool(check()) if callable(check) else True
        except Exception:
            qr_backends[name] = False

    return {
        "status": "ok",
        "version": settings.version,
        "dataDir": str(settings.data_dir),
        "qrBackends": qr_backends,
        "ocr": {"queue": ocr_queue.snapshot(), "local": get_provider("local").describe()},
    }


app.include_router(routes_catalog.router, prefix="/api")
app.include_router(routes_sessions.router, prefix="/api")
app.include_router(routes_scan.router, prefix="/api")
app.include_router(routes_camera.router, prefix="/api")
app.include_router(routes_review.router, prefix="/api")
app.include_router(routes_export.router, prefix="/api")


@app.websocket("/api/ws/ocr")
async def ocr_socket(websocket: WebSocket) -> None:
    """Broadcast channel for OCR progress (review screen)."""
    await websocket.accept()
    await hub.subscribe(OCR_TOPIC, websocket)
    try:
        await websocket.send_json({"type": "ready", "queue": ocr_queue.snapshot()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        logger.debug("ocr socket closed")
    finally:
        await hub.unsubscribe(OCR_TOPIC, websocket)


# ------------------------------------------------------------------ frontend
# In production the teacher runs a single process: the built SPA
# (frontend/dist) is served straight from FastAPI, so http://localhost:8000
# is all they need. During development the Vite dev server proxies /api.


def _frontend_dist() -> Path | None:
    override = os.getenv("PAPERFLOW_FRONTEND_DIST")
    candidates = [Path(override)] if override else []
    candidates.append(Path(__file__).resolve().parent.parent.parent / "frontend" / "dist")
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


_dist = _frontend_dist()
if _dist is not None:
    # html=True serves index.html at "/"; the SPA uses hash routing, so no
    # extra history-mode fallback is required.
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
    logger.info("serving frontend from %s", _dist)
else:  # pragma: no cover - depends on whether the SPA was built
    @app.get("/")
    def index_placeholder() -> JSONResponse:
        return JSONResponse(
            {
                "app": settings.app_name,
                "version": settings.version,
                "hint": "Соберите интерфейс (cd frontend && npm run build) или откройте dev-сервер Vite на http://localhost:5173",
                "api": "/api/health",
            }
        )

