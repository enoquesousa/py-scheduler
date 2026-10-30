from __future__ import annotations

import io
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
import structlog
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.executors.base import run_job
from py_scheduler import app, cli
from py_scheduler.models import IntervalConfig, JobConfig, RetryConfig, SchedulerConfig
from py_scheduler.persistence import JobExecutionStore
from py_scheduler.registry import JobRegistry
from scripts import smoke_validate
from structlog.testing import capture_logs

SECRET = "synthetic-private-runtime-credential"


class OpaqueJobError(RuntimeError):
    def __str__(self) -> str:
        raise AssertionError("exception values must never be formatted by the runtime")


class RecorderNotifier:
    def __init__(self) -> None:
        self.failed: list[tuple[str, str, str | None]] = []
        self.succeeded: list[str] = []
        self.cleared: list[str] = []

    def notify_job_failed(self, job_id: str, job_name: str, error: str | None) -> None:
        self.failed.append((job_id, job_name, error))

    def notify_job_succeeded(self, job_name: str) -> None:
        self.succeeded.append(job_name)

    def clear_failure_alert_silence(self, job_id: str) -> None:
        self.cleared.append(job_id)


def job_config(attempts: int = 3) -> JobConfig:
    return JobConfig(
        id="synthetic-job-id",
        name="synthetic-job",
        interval=IntervalConfig(seconds=60),
        retry=RetryConfig(
            attempts=attempts,
            wait_multiplier_seconds=0.001,
            wait_min_seconds=0,
            wait_max_seconds=0,
        ),
        notify_on_success=True,
    )


def history_rows(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT job_id, job_name, finished_at, duration_ms, outcome, attempt_number, "
            "error_type, error_message FROM job_executions ORDER BY id"
        ).fetchall()


@pytest.fixture
def isolated_executor_log():
    logger = logging.getLogger(app._EXECUTOR_LOGGER)
    original = list(logger.filters), list(logger.handlers), logger.level, logger.propagate
    logger.filters.clear()
    logger.handlers.clear()
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    yield logger
    logger.filters[:], logger.handlers[:], logger.level, logger.propagate = original


@pytest.mark.parametrize("error_class", [ValueError, OpaqueJobError])
def test_retry_errors_keep_metadata_but_not_values_in_logs_history_or_notification(
    error_class, tmp_path, monkeypatch
):
    database = tmp_path / "history.sqlite"
    store = JobExecutionStore(str(database))
    notifier = RecorderNotifier()
    config = job_config()
    error = error_class(f"postgresql://operator:{SECRET}@database.invalid/db?token={SECRET}")
    job = Mock(side_effect=error)
    durations = Mock()
    failures = Mock()
    monkeypatch.setattr(app, "observe_attempt_duration_ms", durations)
    monkeypatch.setattr(app, "inc_terminal_failure", failures)
    with capture_logs() as logs:
        monkeypatch.setattr(app, "structured_logger", structlog.get_logger("runtime-test"))
        wrapped = app._with_retry(config, job, webhook_notifier=notifier, execution_store=store)
        with pytest.raises(error_class) as caught:
            wrapped()

    assert caught.value is error
    assert job.call_count == 3 and durations.call_count == 3
    failures.assert_called_once_with(job_id=config.id, job_name=config.name)
    assert [entry["status"] for entry in logs] == ["retry", "retry", "failed"]
    assert len(notifier.failed) == 1 and not notifier.succeeded
    rows = history_rows(database)
    assert [row[4] for row in rows] == ["failed_retry", "failed_retry", "failed_final"]
    assert [row[5] for row in rows] == [1, 2, 3]
    assert all(row[0:2] == (config.id, config.name) for row in rows)
    assert all(datetime.fromisoformat(row[2]).tzinfo is not None and row[3] >= 0 for row in rows)
    assert all(row[6] == error_class.__name__ and "omitida" in row[7] for row in rows)
    assert SECRET not in json.dumps([logs, rows, notifier.failed], ensure_ascii=False)


def test_retry_success_retains_return_value_history_and_success_notification(tmp_path, monkeypatch):
    database = tmp_path / "history.sqlite"
    store = JobExecutionStore(str(database))
    notifier = RecorderNotifier()
    result = object()
    job = Mock(side_effect=[ValueError(SECRET), result])
    failures = Mock()
    monkeypatch.setattr(app, "inc_terminal_failure", failures)
    with capture_logs() as logs:
        monkeypatch.setattr(app, "structured_logger", structlog.get_logger("runtime-test"))
        wrapped = app._with_retry(
            job_config(), job, webhook_notifier=notifier, execution_store=store
        )
        assert wrapped() is result

    assert job.call_count == 2
    assert notifier.succeeded == ["synthetic-job"]
    assert notifier.cleared == ["synthetic-job-id"] and not notifier.failed
    failures.assert_not_called()
    rows = history_rows(database)
    assert [row[4] for row in rows] == ["failed_retry", "success"]
    assert rows[-1][5:] == (2, None, None)
    assert SECRET not in json.dumps([logs, rows])


def test_real_apscheduler_executor_sanitizes_logs_without_changing_error_events(
    tmp_path, monkeypatch, isolated_executor_log
):
    error = OpaqueJobError(SECRET)
    error.add_note(SECRET)

    def failing_job():
        # A source line must not be copied into the executor log either.
        raise error  # synthetic-private-source-comment

    registry = JobRegistry()
    registry.register("synthetic-job", failing_job)
    config = SchedulerConfig(jobs=(job_config(1),), metrics_enabled=False, database_path="")
    scheduler = app.SchedulerApp(registry, tmp_path / "unused.yaml").build_scheduler(config)
    scheduled = scheduler.get_job("synthetic-job-id")
    assert scheduled is not None
    scheduled.modify(misfire_grace_time=None)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    isolated_executor_log.addHandler(handler)
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    isolated_executor_log.addHandler(CaptureHandler())
    with capture_logs():
        monkeypatch.setattr(app, "structured_logger", structlog.get_logger("runtime-test"))
        events = run_job(scheduled, "default", [datetime.now(UTC)], app._EXECUTOR_LOGGER)

    assert len(events) == 1 and events[0].code == EVENT_JOB_ERROR
    assert events[0].exception is error
    assert "failing_job" in events[0].traceback
    output = stream.getvalue()
    assert "OpaqueJobError" in output and "synthetic-job-id" in output
    assert "failing_job" in output
    assert SECRET not in output and "synthetic-private-source-comment" not in output
    assert records and records[0].exc_info is None
    assert SECRET not in repr(records[0].args) + str(records[0].exc_text)
    scheduler._executors["default"].shutdown()


def test_executor_filter_discards_cached_traceback_and_unstructured_arguments(
    isolated_executor_log,
):
    app._install_scheduler_failure_filter()
    try:
        raise ValueError(SECRET)
    except ValueError:
        record = logging.LogRecord(
            app._EXECUTOR_LOGGER,
            logging.ERROR,
            __file__,
            1,
            "raw error: %s",
            (SECRET,),
            sys.exc_info(),
        )
    record.exc_text = SECRET
    record.message = SECRET
    record.stack_info = SECRET
    stream = io.StringIO()
    isolated_executor_log.addHandler(logging.StreamHandler(stream))
    isolated_executor_log.handle(record)
    assert SECRET not in stream.getvalue()
    assert SECRET not in record.message
    assert record.exc_info is None and record.stack_info is None
    assert "ValueError" in stream.getvalue()


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("jobs: []\nmetrics_enabled: false\ndatabase_path: ''\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("stage", ["config", "registry", "startup"])
def test_cli_failures_have_nonzero_status_without_raw_values(stage, tmp_path, monkeypatch, capsys):
    path = write_config(tmp_path)
    config = SchedulerConfig(jobs=(), metrics_enabled=False, database_path="")
    error = OpaqueJobError(SECRET)
    loader = Mock(return_value=config, side_effect=error if stage == "config" else None)
    register = Mock(side_effect=error if stage == "registry" else None)
    monkeypatch.setattr(cli, "load_scheduler_config", loader)
    monkeypatch.setattr(cli, "_populate_registry", register)
    # run_forever reloads the configuration; exercise that real failure path.
    monkeypatch.setattr(app, "load_scheduler_config", Mock(side_effect=error))
    assert cli.main([str(path)]) == 1
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert "Traceback" not in output.out + output.err
    assert "OpaqueJobError" in output.out


def test_cli_invalid_yaml_never_prints_the_configuration_line(tmp_path, capsys):
    path = tmp_path / "invalid.yaml"
    path.write_text(f"webhook: [https://receiver.invalid/{SECRET}\n", encoding="utf-8")
    assert cli.main([str(path)]) == 1
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert "config_load_failed" in output.out


@pytest.mark.parametrize("stage", ["config", "registry", "build"])
def test_smoke_failures_do_not_print_configuration_or_exception_values(
    stage, tmp_path, monkeypatch, capsys
):
    path = write_config(tmp_path)
    monkeypatch.setattr(sys, "argv", ["smoke_validate.py", str(path)])
    config = SchedulerConfig(jobs=(), metrics_enabled=False, database_path="")
    error = OpaqueJobError(SECRET)
    monkeypatch.setattr(
        smoke_validate,
        "load_scheduler_config",
        Mock(return_value=config, side_effect=error if stage == "config" else None),
    )
    monkeypatch.setattr(
        smoke_validate,
        "_populate_registry",
        Mock(return_value=JobRegistry(), side_effect=error if stage == "registry" else None),
    )
    monkeypatch.setattr(app.SchedulerApp, "build_scheduler", Mock(side_effect=error))
    assert smoke_validate.main() == 1
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert "Traceback" not in output.out + output.err
    assert "OpaqueJobError" in output.err
