"""Validate YAML config + jobs module wiring without starting the scheduler loop."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from py_scheduler import JobRegistry
from py_scheduler.app import SchedulerApp
from py_scheduler.loader import load_scheduler_config


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_config() -> Path:
    return _repo_root() / "config.example.yaml"


def _populate_registry(module_name: str) -> JobRegistry:
    mod = importlib.import_module(module_name)
    register = getattr(mod, "register", None)
    if register is None:
        raise RuntimeError(
            f"Module {module_name!r} must define register(registry: JobRegistry) -> None"
        )
    registry = JobRegistry()
    register(registry)
    return registry


def main() -> int:
    try:
        config_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _default_config()
        if not config_path.is_file():
            print("config not found", file=sys.stderr)
            return 1
        cfg = load_scheduler_config(config_path)
    except Exception as exc:
        print(f"config validation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    module_name = (
        cfg.jobs_register_module
        or os.environ.get("PY_SCHEDULER_JOBS_MODULE")
        or "py_scheduler.example_jobs"
    )
    try:
        registry = _populate_registry(module_name)
    except Exception as exc:
        print(f"jobs register failed ({type(exc).__name__})", file=sys.stderr)
        return 1

    app = SchedulerApp(registry, config_path)
    try:
        scheduler = app.build_scheduler(cfg, execution_store=None)
    except Exception as exc:
        print(f"scheduler validation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    n = len(scheduler.get_jobs())
    print(f"ok: loaded {len(cfg.jobs)} job(s) from config, {n} APScheduler job(s) registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
