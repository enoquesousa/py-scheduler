from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterable
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import structlog
from prometheus_client import Counter, Histogram, make_wsgi_app
from prometheus_client.exposition import ThreadingWSGIServer

from py_scheduler.models import SchedulerConfig

logger = structlog.get_logger(__name__)

# Métricas globais (registro único do prometheus_client).
JOB_FAILURES_TOTAL = Counter(
    "py_scheduler_job_failures_total",
    "Total de falhas finais por job (após esgotar retentativas).",
    ("job_id", "job_name"),
)

JOB_EXECUTION_SECONDS = Histogram(
    "py_scheduler_job_execution_seconds",
    "Duração de cada tentativa de execução, em segundos.",
    ("job_id", "job_name"),
    buckets=(
        0.001,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        30.0,
        60.0,
    ),
)


def observe_attempt_duration_ms(*, job_id: str, job_name: str, duration_ms: int) -> None:
    seconds = max(duration_ms, 0) / 1000.0
    JOB_EXECUTION_SECONDS.labels(job_id, job_name).observe(seconds)


def inc_terminal_failure(*, job_id: str, job_name: str) -> None:
    JOB_FAILURES_TOTAL.labels(job_id, job_name).inc()


def _literal_loopback(value: str) -> bool:
    try:
        address = ip_address(value)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


def _local_authority(value: str, port: int) -> tuple[str, int] | None:
    if not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if any(char in value for char in "/\\@?#,%") or value.endswith(":"):
        return None
    try:
        parsed = urlsplit("http://" + value)
        host = parsed.hostname
        parsed_port = parsed.port if parsed.port is not None else 80
    except ValueError:
        return None
    if host is None or parsed_port != port:
        return None
    expected = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        expected += f":{parsed.port}"
    if value.lower() != expected.lower():
        return None
    if host.lower() != "localhost" and not _literal_loopback(host):
        return None
    return host.lower(), parsed_port


def _local_metrics_request(environ: dict[str, Any], port: int) -> bool:
    if not _literal_loopback(environ.get("REMOTE_ADDR", "")):
        return False
    authority = _local_authority(environ.get("HTTP_HOST", ""), port)
    if authority is None:
        return False
    site = environ.get("HTTP_SEC_FETCH_SITE")
    if site is not None and site not in {"same-origin", "none"}:
        return False
    origin = environ.get("HTTP_ORIGIN")
    if origin is None:
        return True
    if not origin.startswith("http://"):
        return False
    # Origin contains only a scheme and authority; no path/query/fragment is accepted.
    return _local_authority(origin[len("http://") :], port) == authority


def _guard_loopback_metrics(
    app: Callable[..., Iterable[bytes]], port: int
) -> Callable[..., Iterable[bytes]]:
    def guarded(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        if not _local_metrics_request(environ, port):
            body = b"Forbidden\n"
            start_response(
                "403 Forbidden",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ],
            )
            return [body]
        return app(environ, start_response)

    return guarded


class _QuietMetricsHandler(WSGIRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        pass


def _start_metrics_http_server(host: str, port: int) -> tuple[WSGIServer, threading.Thread]:
    family, _, _, _, address = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]

    class MetricsServer(ThreadingWSGIServer):
        address_family = family

    server = make_server(
        address[0],
        port,
        make_wsgi_app(),
        server_class=MetricsServer,
        handler_class=_QuietMetricsHandler,
    )
    bound_address, bound_port = server.socket.getsockname()[:2]
    if _literal_loopback(bound_address):
        server.set_app(_guard_loopback_metrics(server.get_app(), bound_port))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def start_metrics_server(cfg: SchedulerConfig) -> None:
    if not cfg.metrics_enabled:
        logger.info("metrics_disabled")
        return
    _start_metrics_http_server(cfg.metrics_host, cfg.metrics_port)
    logger.info(
        "metrics_server_started",
        host=cfg.metrics_host,
        port=cfg.metrics_port,
    )
