# Adding jobs without changing core code

The extension point is a **Python module** that exposes **`register(registry)`**, where `registry` is a **`JobRegistry`**. The CLI loads that module and calls `register`; job bodies live in your package or a standalone module.

## Steps

1. **Create a module** (for example `mycompany/scheduler_jobs.py`) with no-arg callables (or defaults-only parameters) that APScheduler can invoke, plus `register`:

   ```python
   from py_scheduler.registry import JobRegistry


   def daily_backup() -> None: ...


   def register(registry: JobRegistry) -> None:
       registry.register("daily_backup", daily_backup)
   ```

2. **Point the scheduler** at the module in either way:

   - Environment variable `PY_SCHEDULER_JOBS_MODULE=mycompany.scheduler_jobs` (handy in Docker/Kubernetes).
   - Or root YAML key `jobs_register_module: mycompany.scheduler_jobs` (wins over the environment variable when set).

3. **Declare jobs in YAML** with `name` equal to the string passed to `registry.register(...)`, and configure `interval` / `retry` as documented in [configuration.md](configuration.md).

You do not need to edit `main.py` when adding tasks: ship a new module plus YAML entries (and install any extra dependencies in the environment, for example `pip install -e .` from your own package).

## Docker

Mount the config file at `/app/config/config.yaml`. If you use a private package, install it in the image (multi-stage build or `pip install`) or mount code onto `PYTHONPATH`.

Example:

```bash
docker build -t py-scheduler .
docker run --rm \
  -v /path/to/config.yaml:/app/config/config.yaml:ro \
  -v py-scheduler-data:/data \
  -e PY_SCHEDULER_JOBS_MODULE=mycompany.scheduler_jobs \
  py-scheduler
```

Set `database_path` in YAML to `/data/scheduler.sqlite` if you want durable history inside the volume.

Modules run with the scheduler's operating-system permissions and environment. Only load trusted packages and configuration. Avoid including credentials in job names, identifiers, exception messages, or your own logs. The scheduler's diagnostic policy cannot prevent arbitrary job code from printing or transmitting its environment.

The default metrics bind is loopback inside the container. See [development.md](development.md) for a deliberate external container bind with host-loopback port publishing and the remaining network boundary.

## Prometheus metrics

The HTTP server exposes **`/metrics`** (port configurable, default **9100**).

- **`py_scheduler_job_failures_total`** — labels `job_id`, `job_name`. Counts **terminal** failures only (after retries), aligned with failure webhooks.

- **`py_scheduler_job_execution_seconds`** — histogram by `job_id` and `job_name`, one observation per **attempt** (success or failure). Example average latency in PromQL:

  ```text
  sum by (job_id, job_name) (
    rate(py_scheduler_job_execution_seconds_sum[5m])
  )
  /
  sum by (job_id, job_name) (
    rate(py_scheduler_job_execution_seconds_count[5m])
  )
  ```

Tune the `[5m]` window to your scrape interval and SLOs.

## SQLite history

Each attempt inserts a row into **`job_executions`** with `outcome` in `success`, `failed_retry`, or `failed_final`, `duration_ms`, and error metadata when applicable. Disable persistence with `database_path: ""` in YAML.
