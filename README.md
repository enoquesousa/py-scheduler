<div align="center">
<h1>py-scheduler</h1>

<p>Minimal Python job scheduler: YAML-driven intervals on <strong>APScheduler</strong>, optional <strong>SQLite</strong> execution history, <strong>Tenacity</strong> retries with exponential backoff, optional <strong>webhooks</strong> (with failure-alert muzzle), <strong>Prometheus</strong> metrics on <code>/metrics</code>, structured <strong>structlog</strong> output, and a <strong>Dockerfile</strong> for production-style runs.</p>

  <img src="assets/python.png" alt="py-scheduler banner" width="600px">

  <br>

[![CI](https://github.com/enoquesousa/py-scheduler/actions/workflows/ci.yml/badge.svg)](https://github.com/enoquesousa/py-scheduler/actions/workflows/ci.yml)
[![CodeQL](https://github.com/enoquesousa/py-scheduler/actions/workflows/codeql.yml/badge.svg)](https://github.com/enoquesousa/py-scheduler/actions/workflows/codeql.yml)
[![Dependency review](https://img.shields.io/badge/dependency%20review-in%20CI-0085CA?style=flat&logo=githubactions&logoColor=white)](https://github.com/enoquesousa/py-scheduler/actions/workflows/ci.yml)
[![Publish](https://img.shields.io/badge/Publish-release%20%7C%20manual-blue?style=flat&logo=githubactions&logoColor=white)](https://github.com/enoquesousa/py-scheduler/actions/workflows/publish.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat&logo=python&logoColor=white)](https://github.com/enoquesousa/py-scheduler/blob/main/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat)](LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/enoquesousa/py-scheduler?style=flat)](https://github.com/enoquesousa/py-scheduler/commits)
[![Ruff](https://img.shields.io/badge/Ruff-linting-261230?style=flat&logo=ruff&logoColor=white)](https://github.com/astral-sh/ruff)
[![pytest](https://img.shields.io/badge/tests-pytest-blue?style=flat&logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![CodeFactor](https://www.codefactor.io/repository/github/enoquesousa/py-scheduler/badge)](https://www.codefactor.io/repository/github/enoquesousa/py-scheduler)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?style=flat&logo=pre-commit&logoColor=white)](https://github.com/enoquesousa/py-scheduler/blob/main/.pre-commit-config.yaml)

</div>

---

**py-scheduler** is a small, production-minded **scheduler**: **declare** jobs in YAML (interval triggers), **bind** Python callables from a pluggable `register(registry)` module, **run** them under **APScheduler** with **per-job Tenacity** retries, optional **SQLite** audit rows, optional **HTTPS webhooks** after terminal failures (and optionally after successes), and an embedded **Prometheus** exporter. The CLI loads `config.example.yaml` (or a path you pass), imports `PY_SCHEDULER_JOBS_MODULE` (or `jobs_register_module` in YAML), and blocks until interrupted. Canonical repository: `github.com/enoquesousa/py-scheduler`.

## Demo (quick smoke test)

Create a virtual environment, install the package, and validate YAML + registry wiring without starting the long-running scheduler loop.

**Linux / macOS (bash)**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

python scripts/smoke_validate.py
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

python scripts/smoke_validate.py
```

To exercise the full **CLI** and run jobs on a real interval, use `python main.py config.example.yaml` (or `py-scheduler path/to/config.yaml`). See [docs/development.md](docs/development.md) for tests, Docker, and formatting.

## Features

| Area | What you get |
| ---- | -------------- |
| Config | **YAML** job list with `interval` (`seconds` / `minutes` / `hours`) and optional `retry` blocks. |
| Jobs | **JobRegistry** maps logical `name` keys to callables; implementations live in a user module with `register(registry)`. |
| Execution | **APScheduler** `BlockingScheduler` + `ThreadPoolExecutor` (`coalesce`, `max_instances=1`). |
| Resilience | **Tenacity** exponential backoff per job (`attempts`, `wait_*`) wrapping each execution. |
| Persistence | Optional **SQLite** `job_executions` table via `database_path` (empty string disables). |
| Notifications | Optional **webhook** POST JSON; **failure_alert_silence_minutes** reduces duplicate failure noise. |
| Observability | **structlog** JSON logs; **prometheus_client** `/metrics` (toggle with `metrics_enabled`). |
| Packaging | **Hatchling** `pyproject.toml`, `py-scheduler` console script, bundled `config.example.yaml`. |
| Container | **`Dockerfile`** — slim Python image, config volume, `/data` for SQLite. |

## Tech stack

| Component | Role |
| --------- | ---- |
| Python 3.11+ | Language and runtime |
| APScheduler 3 | Blocking scheduler, interval triggers |
| PyYAML | Config load and validation |
| tenacity | Retry/backoff around job bodies |
| structlog | Structured logging |
| prometheus_client | Metrics HTTP server |
| pytest / pytest-cov | Tests and coverage |
| Ruff | Lint + format |

## Prerequisites

- Python **3.11+** and `pip`.
- No database server is required; **SQLite** is optional for execution history.

## Installation and usage

### From source (recommended)

```bash
git clone https://github.com/enoquesousa/py-scheduler.git
cd py-scheduler
pip install -r requirements.txt
pip install -e ".[dev]"
# start from config.example.yaml or copy it and edit paths / webhooks
```

### Development install (editable)

```bash
pip install -e ".[dev]"
```

### PyPI

There are **no PyPI install badges** in this README yet because uploads to PyPI run **only on a published GitHub Release** (see `.github/workflows/publish.yml`). The same workflow supports **Run workflow** (manual dispatch): it builds wheels/sdists and uploads **artifacts** for inspection, without publishing. Configure **PyPI trusted publishing** for this repository and workflow before a release publish; then `pip install py-scheduler` works after the first successful publish. No PyPI password or long-lived API token is configured in the workflow.

**Dependency review** runs as a **CI job on every pull request** (not on plain pushes to `main`), using `actions/dependency-review-action` alongside lint and tests.

## Quick Start

### Run the scheduler (foreground)

```bash
python main.py config.example.yaml
```

`PY_SCHEDULER_JOBS_MODULE` defaults to **`py_scheduler.example_jobs`** when not set in YAML or the environment.

### CLI entrypoint

```bash
py-scheduler config.example.yaml
```

### Optional metrics probe

With `metrics_enabled: true` (default) and `metrics_port: 9100`:

```bash
curl -s http://127.0.0.1:9100/metrics | head
```

The metrics endpoint has no login or TLS. Keep the default loopback bind for a local scraper. An explicit external bind exposes job identifiers, execution timing, failures, and process metrics to reachable clients; protect it with network controls or an authenticated TLS proxy. Do not put credentials in job identifiers or names.

## Authentication and secrets

The CLI runs trusted Python jobs under its operating-system account. It does not provide user accounts, sessions, JWTs, refresh tokens, or a remote job-control API. Configuration and installed job modules must be controlled by trusted operators.

Outbound webhooks require HTTPS by default, validate the receiver's certificate and hostname, and do not follow redirects. Configure a secret URL through `webhook.url_env` and, if the receiver expects it, a Bearer token through `webhook.bearer_token_env`. YAML contains only environment-variable names. There is no automatic `.env` loading. See [configuration.md](docs/configuration.md) for examples and the explicit loopback-only HTTP development option.

Resolved URL and token values exist in process memory. Rotate credentials at the receiver, update the process environment, and restart the scheduler. The scheduler does not sign events or refresh tokens. Webhooks use the project's generic event schema; a receiver must understand that schema or adapt it for a service such as Slack or Discord. See [architecture.md](docs/architecture.md) for the request flow and storage boundaries.

## Resilience (automatic retries)

Each scheduled callable is wrapped with **Tenacity** (`stop_after_attempt`, `wait_exponential`) configured per job in YAML. Failed attempts are logged and persisted (when SQLite is enabled); the final failure increments **`py_scheduler_job_failures_total`** and may trigger the **webhook**. Tune behaviour via each job’s `retry` block in [`config.example.yaml`](config.example.yaml) and the wiring in [`py_scheduler/app.py`](py_scheduler/app.py).

## Documentation

| Document | Contents |
| -------- | -------- |
| [LICENSE](LICENSE) | MIT License |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guidelines |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community standards |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [`codecov.yml`](codecov.yml) | Codecov defaults |
| [`.pre-commit-config.yaml`](.pre-commit-config.yaml) | Local + CI-friendly Ruff hooks |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, persistence |
| [docs/configuration.md](docs/configuration.md) | YAML keys and environment variables |
| [docs/development.md](docs/development.md) | Setup, tests, Docker, scripts |
| [docs/ADDING_JOBS.md](docs/ADDING_JOBS.md) | Custom `register(registry)` modules |

## Project layout

| Path | Role |
| ---- | ---- |
| `py_scheduler/cli.py` | Argparse-style argv handling, logging setup, registry import |
| `py_scheduler/app.py` | `SchedulerApp`, Tenacity wrapper, scheduler construction |
| `py_scheduler/loader.py` | YAML → `SchedulerConfig` / `JobConfig` validation |
| `py_scheduler/models.py` | Dataclasses for config surfaces |
| `py_scheduler/registry.py` | `JobRegistry` name → callable map |
| `py_scheduler/persistence.py` | SQLite execution store |
| `py_scheduler/webhooks.py` | HTTP notifier + muzzle state |
| `py_scheduler/metrics.py` | Prometheus counters/histogram + HTTP server |
| `py_scheduler/example_jobs.py` | Sample `register` implementation |
| `main.py` | Thin `python -m` style entry re-exporting `cli.main` |
| `config.example.yaml` | Documented sample configuration |
| `scripts/smoke_validate.py` | Fast wiring check (no scheduler loop) |
| `tests/` | `pytest` suite |
| `.github/workflows/` | CI (incl. PR dependency review), CodeQL, PyPI publish |
| `Dockerfile` | Runnable image with config + data volumes |

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

### Coverage

Coverage is collected on Python **3.12** in CI and retained as an XML workflow artifact, including for fork and Dependabot pull requests. A separate job uploads it to **Codecov** using a short-lived GitHub OIDC credential on eligible pushes and internal pull requests. The workflow does not require `CODECOV_TOKEN`.

```bash
pip install -e ".[dev]"
pytest --cov=py_scheduler --cov-report=term-missing
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE).

<div align="center">

## Author

**Enoque Sousa**

[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=flat&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/enoque-sousa-bb89aa168/)
[![GitHub](https://img.shields.io/badge/GitHub-100000?style=flat&logo=github&logoColor=white)](https://github.com/enoquesousa)
[![Portfolio](https://img.shields.io/badge/Portfolio-FF5722?style=flat&logo=target&logoColor=white)](https://enoquesousa.vercel.app)

**[⬆ Back to Top](#py-scheduler)**

Made with ❤️ by [Enoque Sousa](https://github.com/enoquesousa)

**Project status:** Study project

</div>
