"""PaperFlow Hybrid Local Hub FastAPI application."""

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

from app.api import (
    routes_camera,
    routes_catalog,
    routes_export,
    routes_hub,
    routes_maintenance,
    routes_review,
    routes_scan,
    routes_sessions,
)
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.hub.identity import get_hub_identity_store
from app.middleware.hub_security import HubSecurityMiddleware, PrivateNetworkAccessMiddleware
from app.services.events import OCR_TOPIC, hub
from app.services.ocr_queue import ocr_queue
from app.services.ocr_recovery import recover_interrupted_ocr_jobs
from app.services.settings_service import load_config

settings = get_settings()
hub_identity = get_hub_identity_store()

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
    recovered = recover_interrupted_ocr_jobs()
    logger.info(
        "%s %s ready — mode: %s; installation: %s; data dir: %s; recovered OCR jobs: %s",
        settings.app_name,
        settings.version,
        settings.hub_mode,
        hub_identity.installation_id,
        settings.data_dir,
        recovered,
    )
    try:
        yield
    finally:
        await ocr_queue.stop()


app = FastAPI(
    title="PaperFlow Hub API",
    version=settings.version,
    description="Локальный контур PaperFlow: сканирование, OCR и хранение без передачи ученических данных в облако",
    lifespan=lifespan,
)

# Order matters: Hub security is inside CORS so authorization failures still
# receive correct CORS headers. PNA is outermost and amends browser preflights.
app.add_middleware(HubSecurityMiddleware, settings=settings, identity=hub_identity)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_cors_origins,
    # A previously unknown HTTPS web app may only call the public discovery and
    # pairing routes. HubSecurityMiddleware still rejects all private traffic
    # until the exact Origin has been confirmed locally and stored as a client.
    allow_origin_regex=r"^(https://[^/]+|http://(?:localhost|127(?:\.\d{1,3}){3})(?::\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-PaperFlow-Hub-Token",
        "X-PaperFlow-Workspace",
    ],
    expose_headers=["Content-Disposition", "X-Form-Count"],
)
app.add_middleware(PrivateNetworkAccessMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to the browser; always log it locally."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Внутренняя ошибка: {type(exc).__name__}"})


@app.get("/api/health")
def health() -> dict:
    """Public liveness probe without paths, job history or student data."""
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

    queue = ocr_queue.snapshot()
    return {
        "status": "ok",
        "product": "PaperFlow Hub",
        "version": settings.version,
        "protocolVersion": 1,
        "deploymentMode": settings.hub_mode,
        "qrBackends": qr_backends,
        "ocr": {
            "running": bool(queue.get("running")),
            "workers": int(queue.get("workers", 0)),
            "pending": int(queue.get("pending", 0)),
            "local": get_provider("local").describe(),
        },
    }


app.include_router(routes_hub.router, prefix="/api")
app.include_router(routes_catalog.router, prefix="/api")
app.include_router(routes_sessions.router, prefix="/api")
app.include_router(routes_scan.router, prefix="/api")
app.include_router(routes_camera.router, prefix="/api")
app.include_router(routes_review.router, prefix="/api")
app.include_router(routes_export.router, prefix="/api")
app.include_router(routes_maintenance.router, prefix="/api")


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
# Personal/offline builds may still serve the SPA directly from the Hub. The
# cloud deployment serves the same frontend separately and connects through the
# pairing protocol above.


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
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
    logger.info("serving frontend from %s", _dist)
else:  # pragma: no cover - depends on whether the SPA was built
    @app.get("/")
    def index_placeholder() -> JSONResponse:
        return JSONResponse(
            {
                "app": settings.app_name,
                "version": settings.version,
                "mode": settings.hub_mode,
                "installationId": hub_identity.installation_id,
                "hint": "Откройте облачный PaperFlow Web или соберите frontend локально",
                "hubInfo": "/api/hub/info",
            }
        )
