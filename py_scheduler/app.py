from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import structlog
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.job import Job
from apscheduler.schedulers import SchedulerNotRunningError
from apscheduler.schedulers.blocking import BlockingScheduler
from tenacity import RetryCallState, retry, stop_after_attempt, wait_exponential

from py_scheduler.loader import load_scheduler_config
from py_scheduler.metrics import (
    inc_terminal_failure,
    observe_attempt_duration_ms,
    start_metrics_server,
)
from py_scheduler.models import JobConfig, SchedulerConfig
from py_scheduler.persistence import ExecutionOutcome, JobExecutionStore
from py_scheduler.registry import JobRegistry
from py_scheduler.webhooks import WebhookNotifier

structured_logger = structlog.get_logger(__name__)
_EXECUTOR_LOGGER = "apscheduler.executors.default"


def _safe_scheduler_trace(record: logging.LogRecord) -> str:
    """Keep locations and the exception class without formatting its values or source."""
    if not record.exc_info:
        return "Exception: message omitted"
    _, exception, tb = record.exc_info
    lines = ["Traceback locations (exception values and source omitted):"]
    for frame, lineno in traceback.walk_tb(tb):
        lines.append(
            f"  File {frame.f_code.co_filename!r}, line {lineno}, in {frame.f_code.co_name}"
        )
    lines.append(f"{type(exception).__name__}: message omitted")
    return "\n".join(lines)


def _scheduler_log_job_id(record: logging.LogRecord) -> str | None:
    if not isinstance(record.args, tuple) or not record.args:
        return None
    first = record.args[0]
    if isinstance(first, Job):
        return first.id
    if record.msg == "Error running job %s" and isinstance(first, str):
        return first
    return None


class _SchedulerFailureFilter(logging.Filter):
    """Sanitize executor LogRecords without changing job exceptions or events."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.exc_info and not record.exc_text:
            return True
        try:
            job_id = _scheduler_log_job_id(record)
            error_type = type(record.exc_info[1]).__name__ if record.exc_info else "Exception"
            safe_trace = _safe_scheduler_trace(record)
            record.msg = "scheduled_job_failed job_id=%r error_type=%s"
            record.args = (job_id, error_type)
        except Exception:
            record.msg = "scheduled_job_failed; exception details omitted"
            record.args = ()
            safe_trace = "Exception details unavailable"
        record.exc_info = None
        record.exc_text = safe_trace
        record.stack_info = None
        record.message = record.getMessage()
        return True


_SCHEDULER_FAILURE_FILTER = _SchedulerFailureFilter()


def _install_scheduler_failure_filter() -> None:
    logging.getLogger(_EXECUTOR_LOGGER).addFilter(_SCHEDULER_FAILURE_FILTER)


def _duration_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _with_retry(
    job: JobConfig,
    func: Callable[..., object],
    *,
    webhook_notifier: WebhookNotifier,
    execution_store: JobExecutionStore | None = None,
) -> Callable[..., object]:
    retry_config = job.retry
    job_logger = structured_logger.bind(job_id=job.id, job_name=job.name)
    attempt_started_at: dict[int, float] = {}
    current_attempt_number = {"value": 1}

    def _before_attempt(retry_state: RetryCallState) -> None:
        current_attempt_number["value"] = retry_state.attempt_number
        attempt_started_at[retry_state.attempt_number] = time.perf_counter()

    def _after_attempt(retry_state: RetryCallState) -> None:
        if retry_state.outcome is None or not retry_state.outcome.failed:
            return
        attempt_number = retry_state.attempt_number
        started_at = attempt_started_at.pop(attempt_number, retry_state.start_time)
        exception = retry_state.outcome.exception()
        status = "failed" if attempt_number >= retry_config.attempts else "retry"
        duration_ms = _duration_ms(started_at)
        err_type = type(exception).__name__ if exception is not None else None
        err_text = (
            f"Execução falhou ({err_type}); mensagem da exceção omitida."
            if exception is not None
            else None
        )
        finished_at = datetime.now(UTC).isoformat()
        outcome: ExecutionOutcome = "failed_final" if status == "failed" else "failed_retry"
        if execution_store is not None:
            execution_store.record_execution(
                job_id=job.id,
                job_name=job.name,
                finished_at_iso=finished_at,
                duration_ms=duration_ms,
                outcome=outcome,
                attempt_number=attempt_number,
                error_type=err_type,
                error_message=err_text,
            )
        observe_attempt_duration_ms(job_id=job.id, job_name=job.name, duration_ms=duration_ms)
        if outcome == "failed_final":
            inc_terminal_failure(job_id=job.id, job_name=job.name)
        log_method = job_logger.error if status == "failed" else job_logger.warning
        log_method(
            "job_execution",
            status=status,
            duration_ms=duration_ms,
            attempt_number=attempt_number,
            error_type=err_type,
            error_message=err_text,
        )
        if status == "failed":
            webhook_notifier.notify_job_failed(job.id, job.name, err_text)

    def _run_job() -> object:
        started_at = time.perf_counter()
        attempt_number = current_attempt_number["value"]
        result = func()
        webhook_notifier.clear_failure_alert_silence(job.id)
        attempt_started_at.pop(attempt_number, None)
        duration_ms = _duration_ms(started_at)
        finished_at = datetime.now(UTC).isoformat()
        if execution_store is not None:
            execution_store.record_execution(
                job_id=job.id,
                job_name=job.name,
                finished_at_iso=finished_at,
                duration_ms=duration_ms,
                outcome="success",
                attempt_number=attempt_number,
                error_type=None,
                error_message=None,
            )
        observe_attempt_duration_ms(job_id=job.id, job_name=job.name, duration_ms=duration_ms)
        job_logger.info(
            "job_execution",
            status="success",
            duration_ms=duration_ms,
            attempt_number=attempt_number,
        )
        if job.notify_on_success:
            webhook_notifier.notify_job_succeeded(job.name)
        return result

    return retry(
        reraise=True,
        stop=stop_after_attempt(retry_config.attempts),
        wait=wait_exponential(
            multiplier=retry_config.wait_multiplier_seconds,
            min=retry_config.wait_min_seconds,
            max=retry_config.wait_max_seconds,
        ),
        before=_before_attempt,
        after=_after_attempt,
    )(_run_job)


class SchedulerApp:
    """Monta APScheduler a partir de registry + arquivo YAML."""

    __slots__ = ("_registry", "_config_path")

    def __init__(self, registry: JobRegistry, config_path: str | Path) -> None:
        self._registry = registry
        self._config_path = Path(config_path)

    def load_config(self) -> SchedulerConfig:
        return load_scheduler_config(self._config_path)

    def build_scheduler(
        self,
        config: SchedulerConfig | None = None,
        *,
        execution_store: JobExecutionStore | None = None,
    ) -> BlockingScheduler:
        cfg = config if config is not None else self.load_config()
        _install_scheduler_failure_filter()
        executors = {"default": ThreadPoolExecutor(max_workers=4)}
        job_defaults: dict[str, object] = {"coalesce": True, "max_instances": 1}
        scheduler = BlockingScheduler(
            executors=executors,
            job_defaults=job_defaults,
        )
        notifier = WebhookNotifier.from_config(cfg.webhook, execution_store=execution_store)
        for job in cfg.jobs:
            func = self._registry.get(job.name)
            retrying_func = _with_retry(
                job,
                func,
                webhook_notifier=notifier,
                execution_store=execution_store,
            )
            kwargs = job.interval.to_apscheduler_kwargs()
            scheduler.add_job(
                retrying_func,
                trigger="interval",
                id=job.id,
                replace_existing=True,
                **kwargs,
            )
        return scheduler

    def run_forever(self) -> None:
        """Inicia o scheduler até interrupção; encerra com shutdown(wait=True)."""
        scheduler: BlockingScheduler | None = None
        try:
            cfg = self.load_config()
            start_metrics_server(cfg)
            execution_store: JobExecutionStore | None = None
            if cfg.database_path:
                execution_store = JobExecutionStore(cfg.database_path)
            scheduler = self.build_scheduler(cfg, execution_store=execution_store)
            n = len(scheduler.get_jobs())
            structured_logger.info("scheduler_started", job_count=n)
            scheduler.start()
        except KeyboardInterrupt:
            structured_logger.info("shutdown_requested")
        finally:
            if scheduler is not None:
                try:
                    scheduler.shutdown(wait=True)
                    structured_logger.info("scheduler_stopped")
                except SchedulerNotRunningError:
                    structured_logger.debug("scheduler_shutdown_skipped")
