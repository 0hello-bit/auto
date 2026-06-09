"""Pydantic request/response models and the standard response envelope."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# request bodies
# --------------------------------------------------------------------------- #
class RegisterStartRequest(BaseModel):
    """POST /api/register/start - web email registration only."""

    url: Optional[str] = Field(default=None, description="Target register URL; falls back to REGISTER_URL")
    email: Optional[str] = Field(default=None, description="Email to use; falls back to next unused from emails.txt")
    name: Optional[str] = Field(default=None, description="Override the randomly generated Chinese name")
    age: Optional[int] = Field(default=None, description="Override the randomly generated age")
    headless: Optional[bool] = Field(default=None, description="Override HEADLESS")
    timeout: Optional[int] = Field(default=None, description="Email verification code timeout (seconds)")


class Sub2apiImportStartRequest(BaseModel):
    """POST /api/sub2api/import/start - Sub2API OAuth import only."""

    email: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None, description="Sub2API account name; defaults to email")
    group_ids: Optional[List[int]] = Field(default=None)
    concurrency: Optional[int] = Field(default=None)
    priority: Optional[int] = Field(default=None)
    headless: Optional[bool] = Field(default=None)
    enable_sms: Optional[bool] = Field(default=None)
    sms_provider: Optional[str] = Field(default=None, description="Override SMS_PROVIDER: 62us | 5sim")
    # 5sim overrides (fall back to .env when omitted)
    fivesim_country: Optional[str] = Field(default=None, description="Override FIVESIM_COUNTRY")
    fivesim_product: Optional[str] = Field(default=None, description="Override FIVESIM_PRODUCT, e.g. google/telegram")
    fivesim_operator: Optional[str] = Field(default=None, description="Override FIVESIM_OPERATOR (manual strategy)")
    fivesim_operator_strategy: Optional[str] = Field(default=None, description="highest_success | lowest_price | most_available | manual")
    fivesim_max_price: Optional[float] = Field(default=None, description="Override FIVESIM_MAX_PRICE (operator=any only)")
    timeout: Optional[int] = Field(default=None, description="Overall authorization timeout (seconds)")
    register_job_id: Optional[str] = Field(default=None, description="Optional source register job id for linkage")


class ResumeImportsRequest(BaseModel):
    """POST /api/imports/resume - finish the Sub2API import for mailboxes that
    registered successfully but were never imported ('registered' status)."""

    limit: Optional[int] = Field(default=None, description="Max mailboxes to resume (default: all resumable)")
    group_ids: Optional[List[int]] = Field(default=None)
    concurrency: Optional[int] = Field(default=None)
    priority: Optional[int] = Field(default=None)
    headless: Optional[bool] = Field(default=None)
    enable_sms: Optional[bool] = Field(default=None)
    sms_provider: Optional[str] = Field(default=None, description="Override SMS_PROVIDER: 62us | 5sim")
    fivesim_country: Optional[str] = Field(default=None)
    fivesim_product: Optional[str] = Field(default=None)
    fivesim_operator: Optional[str] = Field(default=None)
    fivesim_operator_strategy: Optional[str] = Field(default=None)
    fivesim_max_price: Optional[float] = Field(default=None)
    timeout: Optional[int] = Field(default=None, description="Per-job authorization timeout (seconds)")


class RegisterAndImportStartRequest(BaseModel):
    """POST /api/register-and-import/start - full flow in one browser context."""

    url: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None, description="Sub2API account name; defaults to email")
    headless: Optional[bool] = Field(default=None)
    timeout: Optional[int] = Field(default=None, description="Overall authorization timeout (seconds)")
    enable_sms: Optional[bool] = Field(default=None)
    sms_provider: Optional[str] = Field(default=None, description="Override SMS_PROVIDER: 62us | 5sim")
    # 5sim overrides (fall back to .env when omitted)
    fivesim_country: Optional[str] = Field(default=None, description="Override FIVESIM_COUNTRY")
    fivesim_product: Optional[str] = Field(default=None, description="Override FIVESIM_PRODUCT, e.g. google/telegram")
    fivesim_operator: Optional[str] = Field(default=None, description="Override FIVESIM_OPERATOR (manual strategy)")
    fivesim_operator_strategy: Optional[str] = Field(default=None, description="highest_success | lowest_price | most_available | manual")
    fivesim_max_price: Optional[float] = Field(default=None, description="Override FIVESIM_MAX_PRICE (operator=any only)")
    group_ids: Optional[List[int]] = Field(default=None)
    concurrency: Optional[int] = Field(default=None)
    priority: Optional[int] = Field(default=None)


class AutoStartRequest(BaseModel):
    """POST /api/auto/start - one unified entry that auto-decides the flow.

    With ``email``: resume-import if it is 'registered', full register+import if
    it is new/failed, or a no-op if it is already used / in_use. Without
    ``email``: resume the next 'registered' mailbox if any, else register the
    next fresh mailbox from emails.txt.
    """

    url: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    name: Optional[str] = Field(default=None, description="Sub2API account name; defaults to email")
    headless: Optional[bool] = Field(default=None)
    timeout: Optional[int] = Field(default=None)
    enable_sms: Optional[bool] = Field(default=None)
    sms_provider: Optional[str] = Field(default=None, description="Override SMS_PROVIDER: 62us | 5sim")
    fivesim_country: Optional[str] = Field(default=None)
    fivesim_product: Optional[str] = Field(default=None)
    fivesim_operator: Optional[str] = Field(default=None)
    fivesim_operator_strategy: Optional[str] = Field(default=None)
    fivesim_max_price: Optional[float] = Field(default=None)
    group_ids: Optional[List[int]] = Field(default=None)
    concurrency: Optional[int] = Field(default=None)
    priority: Optional[int] = Field(default=None)
    limit: Optional[int] = Field(default=1, description="When no email: how many 'registered' mailboxes to resume")


class AutoRunBatchRequest(BaseModel):
    """POST /api/auto/run-batch - process the whole mailbox pool sequentially.

    First (optionally) syncs emails.txt from accounts.txt, then walks the pool
    one mailbox at a time, in file order: resume 'registered' mailboxes, then
    register+import fresh ones. Mailboxes whose code can never arrive (service
    abuse mode) are marked 'unavailable' and skipped.
    """

    sync: Optional[bool] = Field(default=True, description="Sync emails.txt from accounts.txt before running")
    url: Optional[str] = Field(default=None)
    headless: Optional[bool] = Field(default=None)
    timeout: Optional[int] = Field(default=None)
    enable_sms: Optional[bool] = Field(default=None)
    sms_provider: Optional[str] = Field(default=None, description="Override SMS_PROVIDER: 62us | 5sim")
    fivesim_country: Optional[str] = Field(default=None)
    fivesim_product: Optional[str] = Field(default=None)
    fivesim_operator: Optional[str] = Field(default=None)
    fivesim_operator_strategy: Optional[str] = Field(default=None)
    fivesim_max_price: Optional[float] = Field(default=None)
    group_ids: Optional[List[int]] = Field(default=None)
    concurrency: Optional[int] = Field(default=None)
    priority: Optional[int] = Field(default=None)
    max_emails: Optional[int] = Field(default=None, description="Cap how many mailboxes to process this run")
    parallelism: Optional[int] = Field(default=None, description="并发处理的邮箱数；缺省用 BATCH_PARALLELISM")


# --------------------------------------------------------------------------- #
# response envelope
# --------------------------------------------------------------------------- #
class ApiResponse(BaseModel):
    """Uniform ``{code, msg, data}`` envelope used by every endpoint."""

    code: int = 1
    msg: str = "ok"
    data: Optional[Any] = None


def ok(data: Any = None, msg: str = "ok") -> dict:
    return {"code": 1, "msg": msg, "data": data}


def err(msg: str, data: Any = None, code: int = 0) -> dict:
    return {"code": code, "msg": msg, "data": data}
