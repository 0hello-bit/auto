"""FastAPI application entry point."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import database
from .account_parser import parse_text
from .api import router
from .config import settings
from .poller import start_background_poller, stop_background_poller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("owned_mail_code_service")


def _load_accounts_file() -> None:
    path = settings.ACCOUNTS_FILE
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        log.warning("could not read accounts file %s: %s", path, exc)
        return
    accounts, errors = parse_text(text)
    for account in accounts:
        database.upsert_account(account)
    for err in errors:
        log.warning("accounts file: %s", err)
    log.info("loaded %d account(s) from %s", len(accounts), path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    _load_accounts_file()
    if not settings.API_KEY:
        log.warning("API_KEY is not set; all /api/* requests will be rejected. "
                    "Set API_KEY in your .env file.")
    if settings.ENABLE_BACKGROUND_POLLER:
        start_background_poller()
    else:
        log.info("background poller disabled; /api/code will poll only the requested mailbox")
    try:
        yield
    finally:
        if settings.ENABLE_BACKGROUND_POLLER:
            stop_background_poller()


app = FastAPI(
    title="Owned Mail Verification Code Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.status_code, "msg": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"code": 422, "msg": "invalid request",
                 "data": jsonable_encoder(exc.errors())},
    )


@app.get("/health")
def health():
    return {"code": 1, "msg": "ok"}


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False)
