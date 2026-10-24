from __future__ import annotations

import http.client
import socket
from unittest.mock import Mock

import pytest
from py_scheduler import metrics
from py_scheduler.models import SchedulerConfig


def call_guard(overrides: dict[str, str]):
    environ = {"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "localhost:9100"}
    environ.update(overrides)
    response = Mock()

    def exporter(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"synthetic_metric 1\n"]

    app = Mock(side_effect=exporter)
    body = b"".join(metrics._guard_loopback_metrics(app, 9100)(environ, response))
    return app, response.call_args.args[0], body


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"HTTP_ORIGIN": "http://localhost:9100", "HTTP_SEC_FETCH_SITE": "same-origin"},
        {"HTTP_HOST": "127.0.0.1:9100", "HTTP_SEC_FETCH_SITE": "none"},
        {"REMOTE_ADDR": "::1", "HTTP_HOST": "[::1]:9100", "HTTP_ORIGIN": "http://[::1]:9100"},
        {"REMOTE_ADDR": "::ffff:127.0.0.1", "HTTP_HOST": "[::ffff:127.0.0.1]:9100"},
    ],
)
def test_local_scrapers_and_same_origin_requests_keep_access(overrides):
    app, status, body = call_guard(overrides)
    assert status == "200 OK" and body == b"synthetic_metric 1\n"
    app.assert_called_once()


@pytest.mark.parametrize(
    "overrides",
    [
        {"REMOTE_ADDR": "192.0.2.1", "HTTP_X_FORWARDED_FOR": "127.0.0.1"},
        {"REMOTE_ADDR": "invalid"},
        {"HTTP_HOST": "rebind.example:9100", "HTTP_X_FORWARDED_HOST": "localhost:9100"},
        {"HTTP_HOST": "localhost.evil.example:9100"},
        {"HTTP_HOST": "localhost:9100,localhost:9100"},
        {"HTTP_HOST": "[::1]suffix:9100"},
        {"HTTP_HOST": "localhost:9100:9100"},
        {"HTTP_HOST": "localhost:09100"},
        {"HTTP_HOST": "localhost:9101"},
        {"HTTP_HOST": "localhost"},
        {"HTTP_HOST": ""},
        {"HTTP_HOST": "user@localhost:9100"},
        {"HTTP_HOST": "localhost:9100/path"},
        {"HTTP_HOST": "localhost:9100\n"},
        {"HTTP_ORIGIN": "https://localhost:9100", "HTTP_X_FORWARDED_PROTO": "https"},
        {"HTTP_ORIGIN": "http://localhost:9101"},
        {"HTTP_ORIGIN": "http://localhost:9100/"},
        {"HTTP_ORIGIN": "http://localhost:9100?token=synthetic-secret"},
        {"HTTP_ORIGIN": "http://rebind.example:9100"},
        {"HTTP_ORIGIN": "null"},
        {"HTTP_SEC_FETCH_SITE": "cross-site"},
        {"HTTP_SEC_FETCH_SITE": "same-site"},
    ],
)
def test_local_boundary_rejects_untrusted_requests_before_collecting_metrics(overrides):
    app, status, body = call_guard(overrides)
    assert status == "403 Forbidden" and body == b"Forbidden\n"
    assert "synthetic-secret" not in body.decode()
    app.assert_not_called()


@pytest.mark.parametrize("bound_address,guarded", [("127.0.0.1", True), ("0.0.0.0", False)])
def test_guard_uses_actual_bound_address_and_preserves_explicit_external_bind(
    bound_address, guarded, monkeypatch
):
    resolver = Mock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (bound_address, 9100))]
    )
    monkeypatch.setattr(metrics.socket, "getaddrinfo", resolver)
    server = Mock()
    server.socket.getsockname.return_value = (bound_address, 9100)
    monkeypatch.setattr(metrics, "make_server", Mock(return_value=server))
    thread = Mock()
    monkeypatch.setattr(metrics.threading, "Thread", Mock(return_value=thread))

    assert metrics._start_metrics_http_server("configured-name.example", 9100) == (server, thread)

    resolver.assert_called_once()
    assert server.set_app.call_count == int(guarded)
    thread.start.assert_called_once()


def test_metrics_disabled_never_binds(monkeypatch):
    starter = Mock()
    monkeypatch.setattr(metrics, "_start_metrics_http_server", starter)
    metrics.start_metrics_server(SchedulerConfig(jobs=(), metrics_enabled=False))
    starter.assert_not_called()


def get_metrics(port: int, headers: list[tuple[str, str]]):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.putrequest("GET", "/metrics", skip_host=True)
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_real_loopback_metrics_server_accepts_scrape_and_rejects_rebinding_and_duplicate_host():
    server, thread = metrics._start_metrics_http_server("127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    local_host = f"127.0.0.1:{port}"
    try:
        status, body = get_metrics(port, [("Host", local_host)])
        assert status == 200 and b"py_scheduler_job_execution_seconds" in body
        for headers in (
            [("Host", f"rebind.example:{port}")],
            [("Host", local_host), ("Host", local_host)],
            [("Host", local_host), ("Origin", "http://untrusted.example")],
        ):
            status, body = get_metrics(port, headers)
            assert status == 403 and body == b"Forbidden\n"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()
