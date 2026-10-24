# Development

## Prerequisites

- Python **3.11+**
- `git` and `pip`

## Install (editable, with dev tools)

```bash
pip install -e ".[dev]"
```

This installs **pytest**, **pytest-cov**, and **Ruff** alongside runtime dependencies declared in `pyproject.toml`.

## Lint and format

```bash
ruff check .
ruff format --check .
```

Auto-fix:

```bash
ruff check . --fix
ruff format .
```

## Tests

```bash
pytest -q
```

Coverage (mirrors CI on Python 3.12):

```bash
pytest --cov=py_scheduler --cov-report=term-missing
```

CI retains `coverage.xml` as an artifact for every tested pull request. Codecov upload runs separately with a short-lived OIDC credential for eligible pushes and internal pull requests; Dependabot and forks retain the XML without receiving an upload credential. PyPI publishing similarly grants OIDC only to a separate publication job on a published release. Manual dispatch builds reviewable distribution artifacts and does not publish to PyPI.

## Fast wiring check

Without starting the blocking scheduler:

```bash
python scripts/smoke_validate.py
# optional: path to another YAML file
python scripts/smoke_validate.py /path/to/config.yaml
```

## Run the scheduler locally

```bash
python main.py config.example.yaml
```

Press **Ctrl+C** to shut down gracefully (`shutdown(wait=True)`).

## Docker

Build and run with the bundled configuration:

```bash
docker build -t py-scheduler .
docker run --rm py-scheduler
```

The bundled configuration binds metrics to loopback **inside the container**. Publishing a Docker port does not make that listener reachable from the host. For a host-side scraper, explicitly set `metrics_host: 0.0.0.0` in a trusted custom configuration and publish only the host loopback address:

```bash
docker run --rm -p 127.0.0.1:9100:9100 \
  -v "$(pwd)/my-config.yaml:/app/config/config.yaml:ro" \
  -v py-scheduler-data:/data \
  -e PY_SCHEDULER_JOBS_MODULE=mycompany.scheduler_jobs \
  py-scheduler
```

Set `database_path: /data/scheduler.sqlite` in your YAML when using the `/data` volume.

The explicit external container bind is unauthenticated. Other containers on an accessible Docker network may still reach it; the host port's loopback binding does not isolate the container network. Use an appropriate private network/firewall or an authenticated TLS proxy for remote scraping.

For environment-based webhook credentials, let your deployment's secret manager inject the named variables. With locally exported values, Docker's `-e SCHEDULER_WEBHOOK_URL -e SCHEDULER_WEBHOOK_TOKEN` passes the variables by name without writing their values into the command. Refer to those names in YAML; do not put real secrets into committed examples or image layers. The process environment remains readable to sufficiently privileged operators.

## Adding jobs without forking core

See [ADDING_JOBS.md](ADDING_JOBS.md).

## pre-commit

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Hooks are defined in [`.pre-commit-config.yaml`](../.pre-commit-config.yaml).
