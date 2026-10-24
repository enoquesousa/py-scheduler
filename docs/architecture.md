# Architecture

**py-scheduler** is a thin orchestration layer around **APScheduler** with a YAML configuration file and a small Python extension point for registering callables.

## High-level flow

1. **CLI** (`py_scheduler/cli.py`) resolves the config path, configures **structlog**, loads YAML via **`load_scheduler_config`**, imports the jobs module, and calls **`register(registry)`**.
2. **`SchedulerApp`** (`py_scheduler/app.py`) reads the same YAML, optionally starts the **Prometheus** HTTP server, optionally opens **SQLite** for execution history, builds a **`BlockingScheduler`**, and registers one APScheduler job per YAML entry.
3. Each YAML job wraps the user callable with **Tenacity** retries (`_with_retry`). On failure paths it records rows (if persistence is enabled), updates metrics, logs, and may call the **webhook** client after the **final** failed attempt.
4. **Webhooks** (`py_scheduler/webhooks.py`) send the scheduler's JSON event format to a configured HTTPS receiver. The client verifies the server certificate and hostname, sends an optional Bearer credential in the `Authorization` header, and never follows redirects. When `failure_alert_silence_minutes` is set and SQLite is enabled, a per-job muzzle timestamp suppresses repeated failure notifications across restarts.

## Trust and credentials

This is a local scheduler, not an HTTP job-management API. It has no user accounts, login, JWT, session cookie, refresh token, or per-job authorization layer. The operating-system account and permissions on the configuration, installed Python modules, environment, and SQLite files form its access boundary.

The YAML configuration selects Python callables through a module's `register(registry)` function. Importing that module executes Python code with the scheduler process's privileges. Only trusted operators should control configuration, module selection, packages, and job implementations. YAML parsing with `safe_load` does not sandbox the Python jobs.

A webhook URL may itself contain a provider credential in its path or query. Prefer an environment-variable reference in YAML for that URL, and a separate environment-variable reference for an optional Bearer token. The scheduler does not read `.env` files automatically. Resolved values stay in process memory and are omitted from configuration representations and delivery diagnostics; they are not encrypted in memory. Restart the scheduler after rotating the value at the receiving service and replacing the process environment. There is no automatic refresh or revocation protocol.

HTTPS authenticates the receiving server. The optional Bearer token lets that server authenticate the sender according to its own policy. Without a Bearer token, authentication depends on the receiving endpoint, such as a secret capability URL. The scheduler does not implement webhook signatures, replay detection, or a receiver. Its generic JSON payload is not automatically adapted to Slack or Discord message schemas.

Delivery accepts only a successful HTTP status in the 2xx range. A redirect is a delivery failure and receives no follow-up request. The response body is not needed for the notification result. HTTP is reserved for an explicit development opt-in to a literal loopback address, without a Bearer token; use synthetic data with that mode. See [configuration.md](configuration.md) for the exact fields and migration examples.

## Persistence schema (SQLite)

When `database_path` is non-empty, `JobExecutionStore` maintains a **`job_executions`** table with timestamps, duration, attempt number, outcome (`success`, `failed_retry`, `failed_final`), and optional error fields. Setting `database_path` to `""` disables all SQLite access from the app runtime path shown in `run_forever`.

The database is an ordinary, unencrypted local SQLite file. Restrict access to it and its WAL/SHM companions using the operating system. Changes to diagnostics do not remove sensitive error text already stored by an older version, or copies in backups and external log systems.

The built-in execution path retains the exception class and execution metadata while omitting arbitrary exception values from new logs, history rows, and failure notifications. Custom callers of the persistence or notifier APIs still control the data they pass. Those low-level APIs are not a general-purpose secret detector.

Retries still raise the original terminal exception. APScheduler's error event continues to contain the original exception and traceback for in-process listeners; the scheduler's executor log record is sanitized before emission. A custom listener that prints those objects must apply its own diagnostic policy. This preserves failure semantics and event consumers without copying arbitrary job exception text into the built-in outputs.

## Metrics

`py_scheduler/metrics.py` starts `prometheus_client`’s WSGI server when enabled. Histograms and counters are labelled by `job_id` and `job_name` for Prometheus scrape compatibility.

For a loopback bind, the wrapper checks the actual peer, a local `Host` with the listener port, and browser `Origin`/fetch metadata. It does not use forwarded headers as proof that a client is local. These checks restrict browser access to the local exporter; they do not identify a user. An explicit non-loopback bind keeps the existing unauthenticated scrape interface, which requires appropriate network controls or an authenticated TLS proxy. Do not use job identifiers or names to carry secrets.

## Extension point

User code should expose **`register(registry: JobRegistry) -> None`** and register stable string keys that match YAML `name` fields. See [ADDING_JOBS.md](ADDING_JOBS.md).
