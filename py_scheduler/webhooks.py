from __future__ import annotations

import http.client
import json
import ssl
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from py_scheduler.models import WebhookConfig, parse_webhook_url
from py_scheduler.persistence import JobExecutionStore

structured_logger = structlog.get_logger(__name__)


def _utc_iso_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _post_http_https(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
    *,
    allow_loopback_http: bool = False,
) -> int:
    """POST sem redirects, sem ler corpo e sem credenciais no HTTP local."""
    parsed = parse_webhook_url(url, allow_loopback_http=allow_loopback_http)
    scheme = parsed.scheme
    if scheme == "http" and any(name.lower() == "authorization" for name in headers):
        raise ValueError("Authorization exige HTTPS")
    host = parsed.hostname
    assert host is not None
    port = parsed.port
    path = parsed.path or "/"
    if "?" in url:
        path = f"{path}?{parsed.query}"

    if scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host,
            port if port is not None else 443,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        conn = http.client.HTTPConnection(
            host,
            port if port is not None else 80,
            timeout=timeout,
        )
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        try:
            return resp.status
        finally:
            resp.close()
    finally:
        conn.close()


@dataclass(slots=True)
class WebhookNotifier:
    """POST HTTP com JSON; não faz nada se `url` estiver vazia ou ausente."""

    _config: WebhookConfig = field(repr=False)
    # Se definido e `failure_alert_silence_minutes` > 0, o muzzle persiste no SQLite.
    _execution_store: JobExecutionStore | None = None
    _failure_alert_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _last_failure_alert_at: dict[str, datetime] = field(
        default_factory=dict, init=False, repr=False
    )

    @classmethod
    def from_config(
        cls,
        config: WebhookConfig,
        *,
        execution_store: JobExecutionStore | None = None,
    ) -> WebhookNotifier:
        return cls(_config=config, _execution_store=execution_store)

    @property
    def enabled(self) -> bool:
        u = self._config.url
        return u is not None and bool(u.strip())

    def clear_failure_alert_silence(self, job_id: str) -> None:
        """Chamar após sucesso: o próximo `job_failed` volta a poder alertar de imediato."""
        with self._failure_alert_lock:
            self._last_failure_alert_at.pop(job_id, None)
            if self._execution_store is not None:
                self._execution_store.clear_failure_webhook_muzzle(job_id)

    def notify_job_failed(self, job_id: str, job_name: str, error: str | None) -> None:
        if not self.enabled:
            return
        silence_minutes = self._config.failure_alert_silence_minutes
        now = datetime.now(UTC)
        with self._failure_alert_lock:
            if silence_minutes > 0:
                last = (
                    self._execution_store.get_last_failure_webhook_alert_at(job_id)
                    if self._execution_store is not None
                    else self._last_failure_alert_at.get(job_id)
                )
                if last is not None:
                    elapsed_minutes = (now - last).total_seconds() / 60.0
                    if elapsed_minutes < silence_minutes:
                        structured_logger.info(
                            "failure_webhook_muzzled",
                            job_id=job_id,
                            job_name=job_name,
                            silence_minutes=silence_minutes,
                            minutes_since_last_alert=round(elapsed_minutes, 4),
                        )
                        return
                if self._execution_store is not None:
                    self._execution_store.set_last_failure_webhook_alert_at(job_id, now)
                else:
                    self._last_failure_alert_at[job_id] = now
        payload: dict[str, Any] = {
            "event": "job_failed",
            "job_id": job_id,
            "job_name": job_name,
            "error": error if error is not None else "",
            "timestamp": _utc_iso_timestamp(),
        }
        self._send(payload)

    def notify_job_succeeded(self, job_name: str) -> None:
        if not self.enabled:
            return
        payload: dict[str, Any] = {
            "event": "job_succeeded",
            "job_name": job_name,
            "timestamp": _utc_iso_timestamp(),
        }
        self._send(payload)

    def _send(self, payload: dict[str, Any]) -> None:
        url = self._config.url
        assert url is not None
        metadata = {
            "job_name": payload.get("job_name"),
            "webhook_event": payload.get("event"),
        }
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json; charset=utf-8"}
            if self._config.bearer_token is not None:
                headers["Authorization"] = f"Bearer {self._config.bearer_token}"
            code = _post_http_https(
                url,
                body,
                headers,
                self._config.timeout_seconds,
                allow_loopback_http=self._config.allow_loopback_http,
            )
        except (ValueError, TypeError, OverflowError, OSError, http.client.HTTPException) as exc:
            structured_logger.warning(
                "webhook_delivery_failed",
                reason=_delivery_failure_reason(exc),
                **metadata,
            )
            return
        _log_delivery_status(code, metadata)


def _delivery_failure_reason(error: Exception) -> str:
    if isinstance(error, ssl.SSLError):
        return "tls_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, http.client.HTTPException):
        return "http_protocol_error"
    if isinstance(error, OSError):
        return "connection_error"
    return "invalid_request"


def _log_delivery_status(code: int, metadata: dict[str, Any]) -> None:
    if type(code) is not int or not 100 <= code <= 599:
        structured_logger.warning("webhook_delivery_failed", reason="invalid_status", **metadata)
    elif 200 <= code < 300:
        structured_logger.debug("webhook_sent", status_code=code, **metadata)
    else:
        structured_logger.warning(
            "webhook_delivery_failed",
            reason="redirect_refused" if 300 <= code < 400 else "http_error",
            status=code,
            **metadata,
        )
