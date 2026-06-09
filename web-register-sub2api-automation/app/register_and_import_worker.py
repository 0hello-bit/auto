"""Combined worker: web registration + (optional) phone verification + Sub2API
import, all inside a SINGLE Playwright browser context.

Keeping registration and authorization in the same context preserves the
incognito session / cookies established during registration, which the OAuth
authorization page relies on. This worker depends only on the unified SMS
provider abstraction (via the import worker), never on a concrete platform.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from . import database, register_worker
from .browser_manager import browser_session
from .database import now
from .register_worker import (
    finalize_register_failure,
    finalize_register_success,
    perform_registration,
    save_screenshot,
)
from .sub2api_import_worker import perform_import

log = logging.getLogger(__name__)


async def run_register_and_import_job(
    *,
    register_job_id: str,
    import_job_id: str,
    url: str,
    email: str,
    name: str,            # generated Chinese display name used in the web form
    age: int,
    account_name: str,    # Sub2API account name (defaults to email)
    headless: bool,
    code_timeout: int,
    timeout: int,         # authorization-phase timeout (seconds)
    enable_sms: bool,
    sms_provider_name: Optional[str],
    group_ids: Optional[Iterable[int]],
    concurrency: Optional[int],
    priority: Optional[int],
    fivesim: Optional[dict] = None,
) -> None:
    try:
        async with browser_session(f"regimport_{register_job_id}", headless=headless) as context:
            # ---------------- registration phase ----------------
            page_register = await context.new_page()
            try:
                await perform_registration(
                    page_register,
                    register_job_id=register_job_id,
                    url=url,
                    email=email,
                    name=name,
                    age=age,
                    code_timeout=code_timeout,
                )
            except Exception as exc:
                shot = await save_screenshot(page_register, register_job_id, "register_error")
                finalize_register_failure(register_job_id, email, exc, shot)
                database.update_import_job(
                    import_job_id,
                    status="failed",
                    error_message=f"registration failed: {str(exc)[:900]}",
                    finished_at=now(),
                )
                return

            # Registration succeeded. The email is marked 'registered' (NOT
            # 'used') so that if the import below fails the mailbox is NOT
            # burned -- it stays recoverable via the import-only resume path.
            # The account row + 'used' mark happen only after the import succeeds.
            finalize_register_success(register_job_id, email, name, age, record_account=False)

            # ---------------- authorization + import phase (same context) ----------------
            try:
                await perform_import(
                    context,
                    import_job_id=import_job_id,
                    register_job_id=register_job_id,
                    email=email,
                    name=account_name,
                    group_ids=group_ids,
                    concurrency=concurrency,
                    priority=priority,
                    enable_sms=enable_sms,
                    sms_provider_name=sms_provider_name,
                    timeout=timeout,
                    fivesim=fivesim,
                )
            except Exception:
                # perform_import already finalized the import job (failed/timeout),
                # took a screenshot, and cancelled the SMS order if needed.
                return

    except Exception as exc:  # browser launch / unexpected context-level failure
        reg = database.get_register_job(register_job_id)
        if not reg or not register_worker.is_terminal(reg.get("status")):
            finalize_register_failure(register_job_id, email, exc, "")
        imp = database.get_import_job(import_job_id)
        if not imp or not register_worker.is_terminal(imp.get("status")):
            database.update_import_job(
                import_job_id,
                status=register_worker.status_for_exc(exc),
                error_message=str(exc)[:1000],
                finished_at=now(),
            )
