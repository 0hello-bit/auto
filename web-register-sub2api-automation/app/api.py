"""FastAPI routes.

Every ``/api/*`` route requires the ``x-api-key`` header (compared against
``API_KEY``). ``/health`` is public. All responses use the
``{code, msg, data}`` envelope.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from .config import config
from .account_sync import AccountSyncError
from .email_pool import EmailPoolError
from .job_service import ServiceError, job_service
from .mail_code_client import MailCodeError
from .models import (
    AutoRunBatchRequest,
    AutoStartRequest,
    RegisterAndImportStartRequest,
    RegisterStartRequest,
    ResumeImportsRequest,
    Sub2apiImportStartRequest,
    err,
    ok,
)
from .sms_provider_base import SmsError
from .sub2api_client import Sub2ApiError

log = logging.getLogger(__name__)

# Exceptions that map to a 400 with a clean message (vs an unexpected 500).
_KNOWN_ERRORS = (ServiceError, SmsError, EmailPoolError, Sub2ApiError, MailCodeError, AccountSyncError, ValueError)


# --------------------------------------------------------------------------- #
# auth dependency
# --------------------------------------------------------------------------- #
async def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="x-api-key")) -> None:
    if not config.api_key or x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing x-api-key")


def _fail(exc: Exception) -> JSONResponse:
    log.warning("request failed: %s", exc)
    return JSONResponse(status_code=400, content=err(str(exc)))


# --------------------------------------------------------------------------- #
# public
# --------------------------------------------------------------------------- #
public_router = APIRouter()


@public_router.get("/health")
async def health() -> dict:
    return {"code": 1, "msg": "ok"}


# --------------------------------------------------------------------------- #
# authenticated /api
# --------------------------------------------------------------------------- #
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


@api_router.post("/register/start")
async def register_start(req: RegisterStartRequest):
    try:
        return ok(job_service.start_register(req), msg="register job scheduled")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.post("/sub2api/import/start")
async def import_start(req: Sub2apiImportStartRequest):
    try:
        return ok(job_service.start_import(req), msg="import job scheduled")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.post("/register-and-import/start")
async def register_and_import_start(req: RegisterAndImportStartRequest):
    try:
        return ok(job_service.start_register_and_import(req), msg="register+import job scheduled")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.post("/auto/start")
async def auto_start(req: AutoStartRequest):
    """Unified entry: auto-decides resume-import vs register-and-import (or a
    no-op for already-used / in_use mailboxes). Recommended single entry."""
    try:
        return ok(job_service.auto_start(req), msg="auto job scheduled")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.post("/auto/run-batch")
async def auto_run_batch(req: AutoRunBatchRequest):
    """Process the whole mailbox pool sequentially, one mailbox at a time, in
    emails.txt order (syncs emails.txt from accounts.txt first by default)."""
    try:
        return ok(job_service.start_auto_batch(req), msg="auto batch scheduled")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.get("/auto/batches")
async def auto_batches():
    return ok(job_service.list_batches())


@api_router.get("/auto/batches/{batch_id}")
async def auto_batch(batch_id: str):
    state = job_service.get_batch(batch_id)
    if state is None:
        return JSONResponse(status_code=404, content=err("batch not found"))
    return ok(state)


@api_router.post("/emails/sync")
async def emails_sync():
    """Rewrite emails.txt from project A's accounts.txt, keeping only mailboxes
    not yet imported into Sub2API (and not used/unavailable)."""
    try:
        return ok(job_service.sync_emails(), msg="emails.txt synced")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.post("/imports/resume")
async def imports_resume(req: ResumeImportsRequest):
    """Finish the Sub2API import for mailboxes that registered but were never
    imported (status 'registered'). Schedules one import-only job per mailbox."""
    try:
        return ok(job_service.resume_registered_imports(req), msg="resume scheduled")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.get("/jobs")
async def list_jobs(limit: int = 50):
    return ok(job_service.list_jobs(limit))


@api_router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = job_service.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content=err("job not found"))
    return ok(job)


@api_router.get("/accounts")
async def list_accounts(limit: int = 200):
    return ok(job_service.list_accounts(limit))


@api_router.get("/emails")
async def list_emails():
    """Mailbox pool status: in_use / registered (resumable) / used / failed."""
    return ok(job_service.list_emails())


@api_router.get("/sub2api/groups")
async def sub2api_groups():
    try:
        return ok(await job_service.get_groups())
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.get("/sms/providers")
async def sms_providers():
    return ok(job_service.sms_providers())


@api_router.get("/sms/profile")
async def sms_profile(provider: Optional[str] = None):
    try:
        return ok(await job_service.get_sms_profile(provider))
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.get("/sms/5sim/products")
async def sms_5sim_products(country: Optional[str] = None, operator: Optional[str] = None):
    """List 5sim services for a country/operator (defaults from .env). Preview only."""
    try:
        return ok(await job_service.list_5sim_products(country, operator), msg="success")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)


@api_router.get("/sms/5sim/operators/best")
async def sms_5sim_best_operator(
    country: Optional[str] = None,
    product: Optional[str] = None,
    strategy: Optional[str] = None,
):
    """Preview the best 5sim operator for a country+product. Does NOT buy / charge."""
    try:
        return ok(await job_service.best_5sim_operator(country, product, strategy), msg="success")
    except _KNOWN_ERRORS as exc:
        return _fail(exc)
