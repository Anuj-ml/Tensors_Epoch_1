"""ReelMind FastAPI application — local-only services."""
from __future__ import annotations

import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, db

logger = logging.getLogger("reelmind.api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)


def _warm_embedding_model() -> None:
    """Load the local model in the background so the first search is fast."""
    try:
        from .services import embeddings

        info = embeddings.model_info()
        logger.info("embedding model ready: %s (%s-dim)", info["model"], info["dim"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding model warmup failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    threading.Thread(target=_warm_embedding_model, daemon=True).start()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="ReelMind",
        description="Local-first AI B-roll discovery and editing API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from .routers import media, system, videos
    from .routers import search as search_router
    from .routers import story, history, explore, script  # noqa: F401

    app.include_router(system.router)
    app.include_router(search_router.router)
    app.include_router(media.router)
    app.include_router(videos.router)
    app.include_router(story.router)
    app.include_router(history.router)
    app.include_router(explore.router)
    app.include_router(script.router)

    app.mount("/media/thumbnails", StaticFiles(directory=config.THUMBNAIL_DIR), name="thumbnails")
    app.mount("/media/previews", StaticFiles(directory=config.PREVIEW_DIR), name="previews")
    app.mount("/media/roughcuts", StaticFiles(directory=config.ROUGHCUT_DIR), name="roughcuts")
    return app


app = create_app()
