"""Application entry point.

Creates the FastAPI app, initializes the database on startup, and (when run as
``python -m app.main``) starts Uvicorn on HOST:PORT (default 127.0.0.1:5060).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__, database
from .api import api_router, public_router
from .config import config, mask_secret

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    database.init_db()
    log.info("Database initialized at %s", config.db_path)
    log.info("Service API key: %s", mask_secret(config.api_key))
    log.info("SMS enabled=%s provider=%s", config.enable_sms, config.sms_provider)
    log.info("Listening on http://%s:%s", config.host, config.port)
    yield


app = FastAPI(
    title="Web Register And Sub2API Import Automation",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(public_router)
app.include_router(api_router)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    run()
