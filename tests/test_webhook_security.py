from __future__ import annotations

import http.client
import ssl

import pytest
from py_scheduler.loader import load_scheduler_config, webhook_from_mapping
from py_scheduler.models import SchedulerConfig, WebhookConfig
from py_scheduler.webhooks import WebhookNotifier, _post_http_https
from structlog.testing import capture_logs


def test_remote_http_rejected_without_opt_in():
    with pytest.raises(ValueError):
        WebhookConfig(url="http://remote.example.test/capability")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:synthetic-capability@example.test/events",
        "https://example.test/events#synthetic-capability",
        "https://[::1]synthetic-capability/events",
        "https://example.test:/synthetic-capability",
        "https://example.test:0/synthetic-capability",
        "https://example.test\n/synthetic-capability",
        "https://example.test\\synthetic-capability/events",
        "https://example.test/synthetic-capability with space",
    ],
)
def test_url_credentials_and_malformed_authority_rejected(url):
    with pytest.raises(ValueError) as caught:
        WebhookConfig(url=url)
    assert "synthetic-capability" not in str(caught.value)


def test_repr_does_not_expose_capability_url_without_bearer():
    url = "https://example.test/synthetic-capability?key=synthetic-query-secret"
    config = WebhookConfig(url=url)
    for value in (config, SchedulerConfig(jobs=(), webhook=config), WebhookNotifier(config)):
        assert url not in repr(value)
        assert "synthetic-capability" not in repr(value)
        assert "synthetic-query-secret" not in repr(value)


@pytest.mark.parametrize(
    "error_type",
    [ValueError, TimeoutError, OSError, http.client.BadStatusLine, http.client.InvalidURL],
)
def test_delivery_exception_text_not_logged(monkeypatch, error_type):
    marker = "SYNTHETIC_RAW_EXCEPTION_WITH_CAPABILITY"

    def fail(*args, **kwargs):
        raise error_type(marker)

    monkeypatch.setattr("py_scheduler.webhooks._post_http_https", fail)
    notifier = WebhookNotifier(WebhookConfig(url="https://example.test/capability"))
    with capture_logs() as logs:
        notifier.notify_job_succeeded("public-job-name")
    assert marker not in repr(logs)
    assert any(item["event"] == "webhook_delivery_failed" for item in logs)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_delivery_is_failure(monkeypatch, status):
    monkeypatch.setattr("py_scheduler.webhooks._post_http_https", lambda *a, **kw: status)
    notifier = WebhookNotifier(WebhookConfig(url="https://example.test/capability"))
    with capture_logs() as logs:
        notifier.notify_job_succeeded("job")
    assert not any(item["event"] == "webhook_sent" for item in logs)
    assert any(item.get("status") == status for item in logs)


class _UnreadResponse:
    status = 204
    closed = False

    def read(self, *args, **kwargs):
        raise AssertionError("Webhook response bodies are not consumed")

    def close(self):
        self.closed = True


class _RecordingConnection:
    def __init__(self):
        self.response = _UnreadResponse()
        self.requests = []
        self.closed = False

    def request(self, method, path, *, body, headers):
        self.requests.append((method, path, body, dict(headers)))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_transport_does_not_consume_response_body(monkeypatch):
    connection = _RecordingConnection()
    monkeypatch.setattr(http.client, "HTTPSConnection", lambda *a, **kw: connection)
    assert _post_http_https("https://example.test/events", b"{}", {}, 1) == 204
    assert connection.closed and connection.response.closed


@pytest.mark.parametrize("field", ["bearer_token", "bearer_token_en", "allow_insecure"])
def test_unknown_webhook_fields_rejected(field):
    with pytest.raises(ValueError) as caught:
        webhook_from_mapping({"url": "https://example.test/", field: "SYNTHETIC_SECRET"})
    assert field not in str(caught.value)
    assert "SYNTHETIC_SECRET" not in str(caught.value)


def test_yaml_parse_error_does_not_echo_capability_line(tmp_path):
    config = tmp_path / "invalid.yaml"
    config.write_text(
        'webhook:\n  url: "https://example.test/SYNTHETIC_CAPABILITY" invalid:\njobs: []\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as caught:
        load_scheduler_config(config)
    assert "SYNTHETIC_CAPABILITY" not in str(caught.value)


@pytest.mark.parametrize("url", ["http://127.0.0.1/events", "http://[::1]:8000/events"])
def test_http_requires_explicit_literal_loopback_opt_in(url):
    with pytest.raises(ValueError):
        WebhookConfig(url=url)
    assert WebhookConfig(url=url, allow_loopback_http=True).url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/events",
        "http://127.1/events",
        "http://0x7f000001/events",
        "http://127.0.0.1.example.test/events",
        "http://[fe80::1%25lo]/events",
        "http://example.test/events",
    ],
)
def test_local_opt_in_never_uses_dns_to_infer_loopback(url):
    with pytest.raises(ValueError):
        WebhookConfig(url=url, allow_loopback_http=True)


def test_environment_snapshot_and_repr(monkeypatch):
    url = "https://example.test/synthetic-path?signature=synthetic-query"
    token = "syntheticBearer+/=="
    monkeypatch.setenv("TEST_WEBHOOK_URL", url)
    monkeypatch.setenv("TEST_WEBHOOK_TOKEN", token)
    config = webhook_from_mapping(
        {"url_env": "TEST_WEBHOOK_URL", "bearer_token_env": "TEST_WEBHOOK_TOKEN"}
    )
    monkeypatch.setenv("TEST_WEBHOOK_URL", "https://other.example.test/")
    monkeypatch.setenv("TEST_WEBHOOK_TOKEN", "replacement")
    assert config.url == url
    assert url not in repr(config) and token not in repr(config)
    assert "synthetic-path" not in repr(config)
    calls = []

    def capture(url, body, headers, timeout, **kwargs):
        calls.append((url, dict(headers)))
        return 204

    monkeypatch.setattr("py_scheduler.webhooks._post_http_https", capture)
    notifier = WebhookNotifier(config)
    with capture_logs() as logs:
        notifier.notify_job_succeeded("job")
    assert calls == [
        (
            url,
            {"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {token}"},
        )
    ]
    assert url not in repr(logs) and token not in repr(logs)


@pytest.mark.parametrize("name", ["", "1INVALID", "HAS-DASH", "HAS SPACE", "NAME=VALUE"])
@pytest.mark.parametrize("field", ["url_env", "bearer_token_env"])
def test_environment_names_are_strict_and_not_echoed(name, field):
    values = {field: name}
    if field == "bearer_token_env":
        values["url"] = "https://example.test/events"
    with pytest.raises(ValueError):
        webhook_from_mapping(values)


@pytest.mark.parametrize("value", [None, "", " "])
@pytest.mark.parametrize("field", ["url_env", "bearer_token_env"])
def test_explicit_environment_reference_must_be_present_and_nonempty(monkeypatch, field, value):
    name = "TEST_WEBHOOK_MISSING_OR_EMPTY"
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    values = {field: name}
    if field == "bearer_token_env":
        values["url"] = "https://example.test/events"
    with pytest.raises(ValueError):
        webhook_from_mapping(values)


def test_url_sources_are_mutually_exclusive(monkeypatch):
    monkeypatch.setenv("TEST_WEBHOOK_URL", "https://example.test/from-env")
    with pytest.raises(ValueError):
        WebhookConfig(url="https://example.test/explicit", url_env="TEST_WEBHOOK_URL")


@pytest.mark.parametrize(
    "token", [" ", "\nsecret", "a\x00b", "non-ascii-é", "abc=def", "=" * 2, "x" * 4097]
)
def test_bearer_token_has_safe_bounded_syntax(monkeypatch, token):
    # Real OS environment values cannot contain NUL; simulate that invalid input
    # at the lookup boundary so configuration still fails closed if reused.
    if "\x00" in token:
        monkeypatch.setattr("py_scheduler.models._webhook_environment", lambda name, option: token)
    else:
        monkeypatch.setenv("TEST_WEBHOOK_TOKEN", token)
    with pytest.raises(ValueError) as caught:
        WebhookConfig(url="https://example.test/events", bearer_token_env="TEST_WEBHOOK_TOKEN")
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("token", ["x", "a.b_c~d+e/f==", "x" * 4096])
def test_bearer_token_allows_provider_length_and_rfc6750_alphabet(monkeypatch, token):
    monkeypatch.setenv("TEST_WEBHOOK_TOKEN", token)
    assert WebhookConfig(
        url="https://example.test/events", bearer_token_env="TEST_WEBHOOK_TOKEN"
    ).url


def test_bearer_is_never_allowed_over_http_even_locally(monkeypatch):
    monkeypatch.setenv("TEST_WEBHOOK_TOKEN", "synthetic-token")
    with pytest.raises(ValueError):
        WebhookConfig(
            url="http://127.0.0.1/events",
            allow_loopback_http=True,
            bearer_token_env="TEST_WEBHOOK_TOKEN",
        )
    with pytest.raises(ValueError):
        WebhookConfig(bearer_token_env="TEST_WEBHOOK_TOKEN")

    def forbidden_connection(*args, **kwargs):
        pytest.fail("No connection may carry a Bearer token over HTTP")

    monkeypatch.setattr(http.client, "HTTPConnection", forbidden_connection)
    with pytest.raises(ValueError):
        _post_http_https(
            "http://127.0.0.1/events",
            b"{}",
            {"Authorization": "Bearer synthetic"},
            1,
            allow_loopback_http=True,
        )


def test_https_uses_default_verification_and_preserves_capability_target(monkeypatch):
    connection = _RecordingConnection()

    def create(host, port, *, timeout, context):
        assert host == "example.test" and port == 443 and timeout == 2
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        return connection

    monkeypatch.setattr(http.client, "HTTPSConnection", create)
    url = "https://example.test/capability/a%2Fb;params?key=first%2Bvalue&key=second"
    assert _post_http_https(url, b"{}", {}, 2) == 204
    assert connection.requests[0][1] == "/capability/a%2Fb;params?key=first%2Bvalue&key=second"
    assert connection.closed and connection.response.closed


@pytest.mark.parametrize(
    "field,value",
    [
        ("allow_loopback_http", "true"),
        ("allow_loopback_http", 1),
        ("timeout_seconds", True),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("failure_alert_silence_minutes", float("nan")),
    ],
)
def test_webhook_options_fail_closed_for_invalid_types_or_numbers(field, value):
    with pytest.raises(ValueError):
        webhook_from_mapping({"url": "https://example.test/events", field: value})


def test_empty_explicit_url_remains_disabled_and_dotenv_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TEST_WEBHOOK_DOTENV", raising=False)
    (tmp_path / ".env").write_text("TEST_WEBHOOK_DOTENV=https://example.test/secret\n")
    assert webhook_from_mapping({"url": "   "}).url is None
    assert not WebhookNotifier(WebhookConfig()).enabled
    with pytest.raises(ValueError):
        WebhookConfig(url_env="TEST_WEBHOOK_DOTENV")


def test_partial_retry_yaml_preserves_default_values(tmp_path):
    path = tmp_path / "partial-retry.yaml"
    path.write_text(
        "jobs:\n  - id: one\n    name: example\n    interval: {seconds: 1}\n"
        "    retry: {attempts: 1}\n",
        encoding="utf-8",
    )
    retry = load_scheduler_config(path).jobs[0].retry
    assert retry.attempts == 1
    assert retry.wait_multiplier_seconds == 1.0
    assert retry.wait_min_seconds == 1.0
    assert retry.wait_max_seconds == 60.0
