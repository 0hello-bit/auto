"""Orchestration / service layer.

Resolves request parameters against configuration, creates DB rows, validates
SMS configuration up-front (so misconfiguration is reported immediately), and
schedules the appropriate worker coroutine as a background asyncio task.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from . import account_sync, database, email_pool, sub2api_client
from .config import AVAILABLE_SMS_PROVIDERS, config
from .identity_generator import generate_age, generate_chinese_name
from .models import (
    AutoRunBatchRequest,
    AutoStartRequest,
    RegisterAndImportStartRequest,
    RegisterStartRequest,
    ResumeImportsRequest,
    Sub2apiImportStartRequest,
)
from .register_and_import_worker import run_register_and_import_job
from .register_worker import run_register_job
from .sms_provider_factory import build_5sim_provider, build_sms_provider
from .sub2api_import_worker import run_import_job

log = logging.getLogger(__name__)


class ServiceError(RuntimeError):
    """A request-level error that should surface to the API caller."""


def _new_job_id() -> str:
    return uuid.uuid4().hex


def _resolve_bool(value: Optional[bool], default: bool) -> bool:
    return default if value is None else value


class JobService:
    def __init__(self) -> None:
        # Keep strong references to background tasks so they are not GC'd.
        self._tasks: Set[asyncio.Task] = set()
        # Sequential batch runner state (only one batch runs at a time).
        self._batches: Dict[str, Dict[str, Any]] = {}
        self._batch_task: Optional[asyncio.Task] = None

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ #
    # SMS validation (requirements 7 & 8) + 5sim override helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fivesim_opts(req: Any) -> Dict[str, Any]:
        """Collect 5sim overrides from a request (any may be None)."""
        return {
            "country": getattr(req, "fivesim_country", None),
            "product": getattr(req, "fivesim_product", None),
            "operator": getattr(req, "fivesim_operator", None),
            "strategy": getattr(req, "fivesim_operator_strategy", None),
            "max_price": getattr(req, "fivesim_max_price", None),
        }

    @staticmethod
    def _effective_fivesim(fivesim: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve overrides over .env for display in the start response."""
        return {
            "country": fivesim.get("country") or config.fivesim_country,
            "product": fivesim.get("product") or config.fivesim_product,
            "operator": fivesim.get("operator") or config.fivesim_operator,
            "operator_strategy": fivesim.get("strategy") or config.fivesim_operator_strategy,
        }

    def _validate_sms(self, enable_sms: bool, provider_name: Optional[str], fivesim: Dict[str, Any]) -> None:
        if not enable_sms:
            return
        # Raises SmsError with an explicit message when misconfigured.
        build_sms_provider(
            provider_name,
            fivesim_country=fivesim.get("country"),
            fivesim_product=fivesim.get("product"),
            fivesim_operator=fivesim.get("operator"),
            fivesim_operator_strategy=fivesim.get("strategy"),
            fivesim_max_price=fivesim.get("max_price"),
        )

    # ------------------------------------------------------------------ #
    # register only
    # ------------------------------------------------------------------ #
    def start_register(self, req: RegisterStartRequest) -> Dict[str, Any]:
        url = (req.url or config.register_url or "").strip()
        if not url:
            raise ServiceError("no register URL provided and REGISTER_URL is empty")

        email = email_pool.get_next_email(req.email)
        name = req.name or generate_chinese_name()
        age = req.age if req.age is not None else generate_age(config.min_age, config.max_age)
        headless = _resolve_bool(req.headless, config.headless)
        code_timeout = req.timeout or config.code_timeout_seconds

        job_id = _new_job_id()
        database.create_register_job(job_id, url=url, email=email, name=name, age=age, status="pending")
        email_pool.mark_in_use(email, job_id)

        self._spawn(
            run_register_job(
                job_id, url=url, email=email, name=name, age=age, headless=headless, code_timeout=code_timeout
            )
        )
        log.info("Scheduled register job %s (email=%s)", job_id, email)
        return {"job_id": job_id, "type": "register", "url": url, "email": email, "name": name, "age": age}

    # ------------------------------------------------------------------ #
    # import only
    # ------------------------------------------------------------------ #
    def start_import(self, req: Sub2apiImportStartRequest) -> Dict[str, Any]:
        enable_sms = _resolve_bool(req.enable_sms, config.enable_sms)
        provider_name = (req.sms_provider or config.sms_provider).lower()
        fivesim = self._fivesim_opts(req)
        self._validate_sms(enable_sms, provider_name, fivesim)

        email = req.email.strip() if req.email else None
        name = req.name or email or "sub2api-account"
        group_ids = req.group_ids if req.group_ids is not None else list(config.sub2api_default_group_ids)
        concurrency = req.concurrency if req.concurrency is not None else config.sub2api_default_concurrency
        priority = req.priority if req.priority is not None else config.sub2api_default_priority
        headless = _resolve_bool(req.headless, config.headless)
        timeout = req.timeout or (config.sms_timeout_seconds + 120 if enable_sms else 180)

        job_id = _new_job_id()
        database.create_import_job(
            job_id,
            email=email,
            register_job_id=req.register_job_id,
            group_ids=group_ids,
            concurrency=concurrency,
            priority=priority,
            status="pending",
        )

        self._spawn(
            run_import_job(
                job_id,
                email=email,
                name=name,
                group_ids=group_ids,
                concurrency=concurrency,
                priority=priority,
                headless=headless,
                enable_sms=enable_sms,
                sms_provider_name=provider_name if enable_sms else None,
                timeout=timeout,
                fivesim=fivesim,
            )
        )
        log.info("Scheduled import job %s (email=%s, sms=%s/%s)", job_id, email, enable_sms, provider_name)
        data = {
            "job_id": job_id,
            "type": "import",
            "email": email,
            "name": name,
            "group_ids": group_ids,
            "enable_sms": enable_sms,
            "sms_provider": provider_name if enable_sms else None,
        }
        if enable_sms and provider_name == "5sim":
            data["fivesim"] = self._effective_fivesim(fivesim)
        return data

    # ------------------------------------------------------------------ #
    # register + import (one click)
    # ------------------------------------------------------------------ #
    def start_register_and_import(self, req: RegisterAndImportStartRequest) -> Dict[str, Any]:
        url = (req.url or config.register_url or "").strip()
        if not url:
            raise ServiceError("no register URL provided and REGISTER_URL is empty")

        enable_sms = _resolve_bool(req.enable_sms, config.enable_sms)
        provider_name = (req.sms_provider or config.sms_provider).lower()
        fivesim = self._fivesim_opts(req)
        self._validate_sms(enable_sms, provider_name, fivesim)

        email = email_pool.get_next_email(req.email)
        name = generate_chinese_name()
        age = generate_age(config.min_age, config.max_age)
        account_name = req.name or email
        group_ids = req.group_ids if req.group_ids is not None else list(config.sub2api_default_group_ids)
        concurrency = req.concurrency if req.concurrency is not None else config.sub2api_default_concurrency
        priority = req.priority if req.priority is not None else config.sub2api_default_priority
        headless = _resolve_bool(req.headless, config.headless)
        code_timeout = config.code_timeout_seconds
        timeout = req.timeout or 300

        register_job_id = _new_job_id()
        import_job_id = _new_job_id()
        database.create_register_job(register_job_id, url=url, email=email, name=name, age=age, status="pending")
        database.create_import_job(
            import_job_id,
            email=email,
            register_job_id=register_job_id,
            group_ids=group_ids,
            concurrency=concurrency,
            priority=priority,
            status="pending",
        )
        email_pool.mark_in_use(email, register_job_id)

        self._spawn(
            run_register_and_import_job(
                register_job_id=register_job_id,
                import_job_id=import_job_id,
                url=url,
                email=email,
                name=name,
                age=age,
                account_name=account_name,
                headless=headless,
                code_timeout=code_timeout,
                timeout=timeout,
                enable_sms=enable_sms,
                sms_provider_name=provider_name if enable_sms else None,
                group_ids=group_ids,
                concurrency=concurrency,
                priority=priority,
                fivesim=fivesim,
            )
        )
        log.info(
            "Scheduled register+import (register=%s, import=%s, email=%s, sms=%s/%s)",
            register_job_id, import_job_id, email, enable_sms, provider_name,
        )
        data = {
            "job_id": register_job_id,
            "register_job_id": register_job_id,
            "import_job_id": import_job_id,
            "type": "register_and_import",
            "url": url,
            "email": email,
            "name": name,
            "age": age,
            "account_name": account_name,
            "group_ids": group_ids,
            "enable_sms": enable_sms,
            "sms_provider": provider_name if enable_sms else None,
        }
        if enable_sms and provider_name == "5sim":
            data["fivesim"] = self._effective_fivesim(fivesim)
        return data

    # ------------------------------------------------------------------ #
    # resume: finish import for registered-but-not-imported mailboxes
    # ------------------------------------------------------------------ #
    def resume_registered_imports(self, req: Any) -> Dict[str, Any]:
        """Schedule import-only jobs for every mailbox that registered on the
        site but was never imported into Sub2API ('registered' status).

        Each job opens a fresh context and runs the OAuth import; the import
        worker handles Scenario 1 (re-enter email + email code) automatically.
        Email status stays 'registered' until a job succeeds (-> 'used'), so a
        failed resume leaves the mailbox resumable again.
        """
        enable_sms = _resolve_bool(req.enable_sms, config.enable_sms)
        provider_name = (req.sms_provider or config.sms_provider).lower()
        fivesim = self._fivesim_opts(req)
        self._validate_sms(enable_sms, provider_name, fivesim)

        resumable = email_pool.list_resumable_emails()
        emails = resumable
        if req.limit is not None and req.limit >= 0:
            emails = resumable[: req.limit]

        group_ids = req.group_ids if req.group_ids is not None else list(config.sub2api_default_group_ids)
        concurrency = req.concurrency if req.concurrency is not None else config.sub2api_default_concurrency
        priority = req.priority if req.priority is not None else config.sub2api_default_priority
        headless = _resolve_bool(req.headless, config.headless)
        timeout = req.timeout or (config.sms_timeout_seconds + 120 if enable_sms else 180)

        scheduled: List[Dict[str, Any]] = []
        for email in emails:
            job_id = _new_job_id()
            database.create_import_job(
                job_id, email=email, group_ids=group_ids,
                concurrency=concurrency, priority=priority, status="pending",
            )
            self._spawn(
                run_import_job(
                    job_id,
                    email=email,
                    name=email,
                    group_ids=group_ids,
                    concurrency=concurrency,
                    priority=priority,
                    headless=headless,
                    enable_sms=enable_sms,
                    sms_provider_name=provider_name if enable_sms else None,
                    timeout=timeout,
                    fivesim=fivesim,
                )
            )
            scheduled.append({"job_id": job_id, "email": email})
            log.info("Scheduled resume-import job %s (email=%s)", job_id, email)

        return {
            "resumable_total": len(resumable),
            "scheduled": len(scheduled),
            "jobs": scheduled,
            "enable_sms": enable_sms,
            "sms_provider": provider_name if enable_sms else None,
        }

    # ------------------------------------------------------------------ #
    # email pool sync (accounts.txt -> emails.txt)
    # ------------------------------------------------------------------ #
    def sync_emails(self) -> Dict[str, Any]:
        """Rewrite emails.txt from project A's accounts.txt, keeping only the
        mailboxes not yet imported into Sub2API (and not used/unavailable)."""
        return account_sync.sync_emails_file()

    # ------------------------------------------------------------------ #
    # unified auto entry (decides resume-import vs register-and-import)
    # ------------------------------------------------------------------ #
    def _import_req_from_auto(self, req: Any, email: Optional[str]) -> Sub2apiImportStartRequest:
        return Sub2apiImportStartRequest(
            email=email, name=email, group_ids=req.group_ids, concurrency=req.concurrency,
            priority=req.priority, headless=req.headless, enable_sms=req.enable_sms,
            sms_provider=req.sms_provider, fivesim_country=req.fivesim_country,
            fivesim_product=req.fivesim_product, fivesim_operator=req.fivesim_operator,
            fivesim_operator_strategy=req.fivesim_operator_strategy,
            fivesim_max_price=req.fivesim_max_price, timeout=req.timeout,
        )

    def _regimport_req_from_auto(self, req: Any, email: Optional[str]) -> RegisterAndImportStartRequest:
        return RegisterAndImportStartRequest(
            url=req.url, email=email, name=email, headless=req.headless, timeout=req.timeout,
            enable_sms=req.enable_sms, sms_provider=req.sms_provider,
            fivesim_country=req.fivesim_country, fivesim_product=req.fivesim_product,
            fivesim_operator=req.fivesim_operator, fivesim_operator_strategy=req.fivesim_operator_strategy,
            fivesim_max_price=req.fivesim_max_price, group_ids=req.group_ids,
            concurrency=req.concurrency, priority=req.priority,
        )

    def auto_start(self, req: AutoStartRequest) -> Dict[str, Any]:
        """Single entry that auto-routes to the right flow (see AutoStartRequest)."""
        def _result(mode, email, reason, register_job_id=None, import_job_id=None, extra=None):
            data = {
                "mode": mode, "email": email, "reason": reason,
                "register_job_id": register_job_id, "import_job_id": import_job_id,
            }
            if extra:
                data.update(extra)
            return data

        # ---- explicit email: route by its current lifecycle ----
        if req.email and req.email.strip():
            email = req.email.strip()
            if email_pool.is_email_used_or_imported(email):
                return _result("already_used", email,
                               "email already imported into Sub2API / marked used")
            status = (database.get_email_status(email) or {}).get("status")
            if status == "unavailable":
                return _result("unavailable", email,
                               "mailbox unavailable (service-abuse / dead refresh_token); skipped")
            if status == "in_use":
                return _result("in_use", email, "a job is already running for this email")
            if status == "registered":
                data = self.start_import(self._import_req_from_auto(req, email))
                return _result("resume_import", email,
                               "registered but not yet in Sub2API -> import-only",
                               import_job_id=data["job_id"])
            # no record or 'failed' -> full register + import
            data = self.start_register_and_import(self._regimport_req_from_auto(req, email))
            return _result("register_and_import", email,
                           "new/failed email -> full register + import",
                           register_job_id=data["register_job_id"], import_job_id=data["import_job_id"])

        # ---- no email: prefer resuming a registered mailbox ----
        registered = email_pool.list_resumable_emails()
        if registered:
            limit = req.limit if req.limit is not None else 1
            res = self.resume_registered_imports(
                ResumeImportsRequest(
                    limit=limit, group_ids=req.group_ids, concurrency=req.concurrency,
                    priority=req.priority, headless=req.headless, enable_sms=req.enable_sms,
                    sms_provider=req.sms_provider, fivesim_country=req.fivesim_country,
                    fivesim_product=req.fivesim_product, fivesim_operator=req.fivesim_operator,
                    fivesim_operator_strategy=req.fivesim_operator_strategy,
                    fivesim_max_price=req.fivesim_max_price, timeout=req.timeout,
                )
            )
            jobs = res.get("jobs", [])
            first = jobs[0] if jobs else {}
            return _result(
                "resume_import", first.get("email"),
                f"{len(registered)} registered mailbox(es); scheduled {res.get('scheduled')} resume import(s)",
                import_job_id=first.get("job_id"), extra={"jobs": jobs},
            )

        # ---- no registered mailbox: register a fresh one from emails.txt ----
        try:
            data = self.start_register_and_import(self._regimport_req_from_auto(req, None))
        except email_pool.EmailPoolError as exc:
            raise ServiceError(
                "没有可用邮箱：没有 registered 可续传邮箱，也没有新邮箱可注册 / "
                "no available mailbox: nothing to resume and no fresh email to register"
            ) from exc
        return _result("register_and_import", data["email"],
                       "no registered mailbox; registering a fresh one",
                       register_job_id=data["register_job_id"], import_job_id=data["import_job_id"])

    # ------------------------------------------------------------------ #
    # sequential streaming batch (process the whole pool one-by-one)
    # ------------------------------------------------------------------ #
    def start_auto_batch(self, req: AutoRunBatchRequest) -> Dict[str, Any]:
        """Schedule a single background coordinator that processes the mailbox
        pool sequentially (one mailbox fully finished before the next starts)."""
        if self._batch_task is not None and not self._batch_task.done():
            running = next((b for b in self._batches.values() if b.get("status") == "running"), None)
            raise ServiceError(
                f"a batch is already running (batch_id={running.get('batch_id') if running else '?'}); "
                f"wait for it to finish or restart the service"
            )

        enable_sms = _resolve_bool(req.enable_sms, config.enable_sms)
        provider_name = (req.sms_provider or config.sms_provider).lower()
        fivesim = self._fivesim_opts(req)
        # Validate SMS config once, up-front (raises SmsError on misconfiguration).
        self._validate_sms(enable_sms, provider_name, fivesim)

        batch_id = _new_job_id()
        parallelism = max(1, req.parallelism if req.parallelism else config.batch_parallelism)
        state: Dict[str, Any] = {
            "batch_id": batch_id, "status": "running", "started_at": database.now(),
            "finished_at": None, "sync": None, "current": None, "in_flight": [],
            "processed": 0, "results": [], "error": None, "parallelism": parallelism,
            "enable_sms": enable_sms, "sms_provider": provider_name if enable_sms else None,
        }
        self._batches[batch_id] = state
        self._batch_task = asyncio.create_task(
            self._auto_batch_coro(
                batch_id,
                url=(req.url or config.register_url or "").strip(),
                headless=_resolve_bool(req.headless, config.headless),
                enable_sms=enable_sms,
                provider_name=provider_name,
                fivesim=fivesim,
                group_ids=req.group_ids if req.group_ids is not None else list(config.sub2api_default_group_ids),
                concurrency=req.concurrency if req.concurrency is not None else config.sub2api_default_concurrency,
                priority=req.priority if req.priority is not None else config.sub2api_default_priority,
                timeout=req.timeout or 300,
                do_sync=_resolve_bool(req.sync, True),
                max_emails=req.max_emails,
                parallelism=parallelism,
            )
        )
        log.info("Scheduled auto batch %s (sms=%s/%s, parallelism=%d)", batch_id, enable_sms, provider_name, parallelism)
        return {"batch_id": batch_id, "status": "running", "parallelism": parallelism,
                "poll": f"GET /api/auto/batches/{batch_id}",
                "note": f"processes emails.txt with up to {parallelism} concurrent worker(s); watch /api/emails and /api/jobs"}

    def _next_batch_target(self, attempted: Set[str]) -> Optional[Dict[str, str]]:
        """Pick the next mailbox to process (emails.txt order), skipping ones
        already attempted in this batch and ones that no longer need work."""
        try:
            file_emails = email_pool._read_email_file()
        except email_pool.EmailPoolError:
            return None
        imported = email_pool._sub2api_imported_emails()
        for email in file_emails:
            if email in attempted:
                continue
            if email.strip().lower() in imported:
                continue
            status = (database.get_email_status(email) or {}).get("status")
            if status in ("used", "unavailable", "in_use"):
                continue
            mode = "resume_import" if status == "registered" else "register_and_import"
            return {"email": email, "mode": mode}
        return None

    async def _auto_batch_coro(
        self, batch_id: str, *, url: str, headless: bool, enable_sms: bool,
        provider_name: str, fivesim: Dict[str, Any], group_ids: List[int],
        concurrency: int, priority: int, timeout: int, do_sync: bool,
        max_emails: Optional[int], parallelism: int = 1,
    ) -> None:
        state = self._batches[batch_id]
        try:
            if do_sync:
                report = await asyncio.to_thread(account_sync.sync_emails_file)
                state["sync"] = {"kept_count": report["kept_count"],
                                 "skipped_count": report["skipped_count"],
                                 "kept": report["kept"]}
                log.info("[batch %s] synced emails.txt: %d kept, %d skipped",
                         batch_id, report["kept_count"], report["skipped_count"])

            attempted: Set[str] = set()
            claim_lock = asyncio.Lock()

            def _claim() -> Optional[Dict[str, str]]:
                """Atomically pick & reserve the next mailbox. Caller holds claim_lock.

                ``attempted`` is the in-memory claim ledger: ``_next_batch_target``
                skips anything already in it, so no two workers grab the same email.
                """
                if max_emails is not None and state["processed"] >= max_emails:
                    return None
                target = self._next_batch_target(attempted)
                if target is None:
                    return None
                attempted.add(target["email"])
                state["processed"] += 1
                state["current"] = {"email": target["email"], "mode": target["mode"]}
                state["in_flight"].append({"email": target["email"], "mode": target["mode"]})
                return target

            async def _worker(widx: int) -> None:
                # Stagger starts so N registrations don't all hit the same IP at t=0
                # (lowers OpenAI/Cloudflare anti-abuse risk on simultaneous signups).
                if widx:
                    await asyncio.sleep(widx * 5)
                while True:
                    async with claim_lock:
                        target = _claim()
                    if target is None:
                        return
                    email, mode = target["email"], target["mode"]
                    log.info("[batch %s] (%d) %s -> %s", batch_id, state["processed"], email, mode)
                    try:
                        if mode == "resume_import":
                            await self._process_resume_one(
                                email, group_ids=group_ids, concurrency=concurrency, priority=priority,
                                headless=headless, enable_sms=enable_sms, provider_name=provider_name,
                                fivesim=fivesim, timeout=timeout)
                        else:
                            await self._process_register_and_import(
                                email, url=url, group_ids=group_ids, concurrency=concurrency,
                                priority=priority, headless=headless, enable_sms=enable_sms,
                                provider_name=provider_name, fivesim=fivesim, timeout=timeout)
                    except Exception as exc:  # workers finalize status themselves; never abort the batch
                        log.warning("[batch %s] %s raised (continuing): %s", batch_id, email, str(exc)[:200])
                    row = database.get_email_status(email) or {}
                    result = {"email": email, "mode": mode, "status": row.get("status"),
                              "error": (row.get("last_error") or "")[:300]}
                    async with claim_lock:
                        state["results"].append(result)
                        state["in_flight"] = [f for f in state["in_flight"] if f.get("email") != email]
                    log.info("[batch %s] %s -> status=%s", batch_id, email, row.get("status"))

            n = max(1, parallelism)
            log.info("[batch %s] starting %d worker(s)", batch_id, n)
            await asyncio.gather(*[asyncio.create_task(_worker(i)) for i in range(n)])

            state["status"] = "completed"
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = str(exc)[:500]
            log.exception("[batch %s] coordinator failed", batch_id)
        finally:
            state["current"] = None
            state["in_flight"] = []
            state["finished_at"] = database.now()
            log.info("[batch %s] done: status=%s processed=%d",
                     batch_id, state["status"], state["processed"])

    async def _process_register_and_import(
        self, email: str, *, url: str, group_ids: List[int], concurrency: int,
        priority: int, headless: bool, enable_sms: bool, provider_name: str,
        fivesim: Dict[str, Any], timeout: int,
    ) -> None:
        if not url:
            raise ServiceError("no register URL provided and REGISTER_URL is empty")
        register_job_id = _new_job_id()
        import_job_id = _new_job_id()
        name = generate_chinese_name()
        age = generate_age(config.min_age, config.max_age)
        database.create_register_job(register_job_id, url=url, email=email, name=name, age=age, status="pending")
        database.create_import_job(import_job_id, email=email, register_job_id=register_job_id,
                                   group_ids=group_ids, concurrency=concurrency, priority=priority, status="pending")
        email_pool.mark_in_use(email, register_job_id)
        await run_register_and_import_job(
            register_job_id=register_job_id, import_job_id=import_job_id, url=url, email=email,
            name=name, age=age, account_name=email, headless=headless,
            code_timeout=config.code_timeout_seconds, timeout=timeout, enable_sms=enable_sms,
            sms_provider_name=provider_name if enable_sms else None, group_ids=group_ids,
            concurrency=concurrency, priority=priority, fivesim=fivesim,
        )

    async def _process_resume_one(
        self, email: str, *, group_ids: List[int], concurrency: int, priority: int,
        headless: bool, enable_sms: bool, provider_name: str, fivesim: Dict[str, Any], timeout: int,
    ) -> None:
        import_job_id = _new_job_id()
        database.create_import_job(import_job_id, email=email, group_ids=group_ids,
                                   concurrency=concurrency, priority=priority, status="pending")
        await run_import_job(
            import_job_id, email=email, name=email, group_ids=group_ids, concurrency=concurrency,
            priority=priority, headless=headless, enable_sms=enable_sms,
            sms_provider_name=provider_name if enable_sms else None,
            timeout=timeout or (config.sms_timeout_seconds + 120 if enable_sms else 180), fivesim=fivesim,
        )

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        return self._batches.get(batch_id)

    def list_batches(self) -> List[Dict[str, Any]]:
        return sorted(self._batches.values(), key=lambda b: b.get("started_at") or 0, reverse=True)

    # ------------------------------------------------------------------ #
    # queries
    # ------------------------------------------------------------------ #
    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return database.list_jobs(limit)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return database.get_job(job_id)

    def list_accounts(self, limit: int = 200) -> List[Dict[str, Any]]:
        return database.list_accounts(limit)

    def list_emails(self) -> List[Dict[str, Any]]:
        """All tracked mailboxes + status (in_use / registered / used / failed)."""
        return database.list_email_usage()

    async def get_groups(self) -> Any:
        return await asyncio.to_thread(sub2api_client.get_groups)

    def sms_providers(self) -> Dict[str, Any]:
        return {"current": config.sms_provider, "available": list(AVAILABLE_SMS_PROVIDERS)}

    async def get_sms_profile(self, provider_name: Optional[str] = None) -> Dict[str, Any]:
        name = (provider_name or config.sms_provider).lower()
        provider = build_sms_provider(name)
        profile = await asyncio.to_thread(provider.check_profile)
        return {"provider": name, "profile": profile}

    # ------------------------------------------------------------------ #
    # 5sim discovery (preview only -- never buys, never charges)
    # ------------------------------------------------------------------ #
    async def list_5sim_products(self, country: Optional[str] = None, operator: Optional[str] = None) -> Dict[str, Any]:
        eff_country = (country or config.fivesim_country or "").strip()
        eff_operator = (operator or config.fivesim_operator or "any").strip()
        if not eff_country:
            raise ServiceError("FIVESIM_COUNTRY 未配置，且请求中没有传 country")
        # product not required for product discovery
        provider = build_5sim_provider(require_country=False, require_product=False)
        return await asyncio.to_thread(provider.list_products, eff_country, eff_operator)

    async def best_5sim_operator(
        self,
        country: Optional[str] = None,
        product: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        eff_country = (country or config.fivesim_country or "").strip()
        eff_product = (product or config.fivesim_product or "").strip()
        eff_strategy = (strategy or config.fivesim_operator_strategy or "highest_success").strip().lower()
        if not eff_product:
            raise ServiceError("FIVESIM_PRODUCT 未配置，且请求中没有传 product")
        if not eff_country:
            raise ServiceError("FIVESIM_COUNTRY 未配置，且请求中没有传 country")
        provider = build_5sim_provider(require_country=False, require_product=False)
        return await asyncio.to_thread(provider.select_best_operator, eff_country, eff_product, eff_strategy)


# Singleton used by the API layer.
job_service = JobService()
