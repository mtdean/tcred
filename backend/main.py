"""
situation-monitor/backend/main.py
FastAPI application — serves the dashboard API and static frontend.
Run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router
from data.scheduler import start_scheduler, shutdown_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("../logs/monitor.log"),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: run feed health checks and kick off background scheduler."""
    logger.info("Starting Situation Monitor...")
    from data.feeds import backfill_source_types

    backfill_source_types()
    await start_scheduler()
    yield
    logger.info("Shutting down...")
    await shutdown_scheduler()


app = FastAPI(
    title="<TCRED>",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # LAN-only; restrict if ever exposed externally
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# Serve the built React frontend (frontend/dist) from this same server, so the
# whole app is reachable on one port (:8000) — ideal for LAN/iPad access.
# Build it first: `cd frontend && npm run build`.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve a real static file if it exists, else the SPA index (client routing)."""
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    logger.warning("Frontend dist/ not found — run `npm run build` in frontend/")


if __name__ == "__main__":
    import uvicorn
    # Production serve: no reload (this server hosts the built frontend + API).
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
