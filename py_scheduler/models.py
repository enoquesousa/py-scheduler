from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any
from urllib.parse import SplitResult, urlsplit


def webhook_url_has_allowed_scheme(url: str, *, allow_loopback_http: bool = False) -> bool:
    """Compatibilidade: aplica a politica completa, incluindo o opt-in HTTP local."""
    try:
        parse_webhook_url(url, allow_loopback_http=allow_loopback_http)
    except ValueError:
        return False
    return True


def parse_webhook_url(url: str, *, allow_loopback_http: bool = False) -> SplitResult:
    """Valida sem ecoar a capability URL, preservando seu path e query."""
    message = (
        "URL de webhook invalida: use HTTPS sem userinfo, fragmento ou espacos; "
        "HTTP exige allow_loopback_http e um IP loopback literal"
    )
    try:
        if not isinstance(url, str) or not url or not isinstance(allow_loopback_http, bool):
            raise ValueError
        if not url.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in url):
            raise ValueError
        if "\\" in url or "#" in url:
            raise ValueError
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        _validate_webhook_authority(parsed)
        if parsed.scheme == "http":
            if not allow_loopback_http or not ip_address(host).is_loopback:
                raise ValueError
        elif parsed.scheme != "https":
            raise ValueError
        return parsed
    except (ValueError, UnicodeError):
        raise ValueError(message) from None


def _validate_webhook_authority(parsed: SplitResult) -> None:
    host = parsed.hostname
    assert host is not None
    if "%" in host:
        raise ValueError
    if parsed.netloc.startswith("["):
        if ip_address(host).version != 6:
            raise ValueError
        suffix = parsed.netloc[parsed.netloc.index("]") + 1 :]
    else:
        _validate_webhook_host(host)
        _, separator, port = parsed.netloc.partition(":")
        suffix = separator + port
    if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
        raise ValueError
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError


def _validate_webhook_host(host: str) -> None:
    if len(host) > 253:
        raise ValueError
    for label in host.removesuffix(".").split("."):
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(char.isalnum() or char == "-" for char in label)
        ):
            raise ValueError


def _webhook_environment(name: str | None, option: str) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"{option} deve conter somente um nome de variavel de ambiente valido")
    value = os.getenv(name)
    if value is None or not value:
        raise ValueError(f"A variavel referenciada por {option} deve estar definida e nao vazia")
    return value


def _webhook_number(value: float, option: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{option} deve ser numerico")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{option} deve ser finito") from None
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(
            f"{option} deve ser finito e {'nao negativo' if allow_zero else 'positivo'}"
        )
    return number


@dataclass(frozen=True, slots=True)
class IntervalConfig:
    """Intervalo para trigger do tipo 'interval' do APScheduler."""

    seconds: int | None = None
    minutes: int | None = None
    hours: int | None = None

    def to_apscheduler_kwargs(self) -> dict[str, int]:
        """Converte para kwargs aceitos por add_job(..., 'interval', ...)."""
        kwargs: dict[str, int] = {}
        if self.seconds is not None:
            kwargs["seconds"] = self.seconds
        if self.minutes is not None:
            kwargs["minutes"] = self.minutes
        if self.hours is not None:
            kwargs["hours"] = self.hours
        if not kwargs:
            raise ValueError(
                "IntervalConfig precisa de pelo menos um entre: seconds, minutes, hours"
            )
        return kwargs


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Política de retry aplicada à execução de um job."""

    attempts: int = 3
    wait_multiplier_seconds: float = 1.0
    wait_min_seconds: float = 1.0
    wait_max_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryConfig.attempts deve ser maior ou igual a 1")
        if self.wait_multiplier_seconds <= 0:
            raise ValueError("RetryConfig.wait_multiplier_seconds deve ser maior que zero")
        if self.wait_min_seconds < 0:
            raise ValueError("RetryConfig.wait_min_seconds deve ser maior ou igual a zero")
        if self.wait_max_seconds < self.wait_min_seconds:
            raise ValueError("RetryConfig.wait_max_seconds deve ser maior ou igual ao minimo")


@dataclass(frozen=True, slots=True)
class JobConfig:
    """Especificação de um job carregada do YAML."""

    id: str
    name: str
    interval: IntervalConfig
    retry: RetryConfig = RetryConfig()
    # Se True e webhook configurado, envia POST após sucesso (tarefas críticas).
    notify_on_success: bool = False


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    """Endpoint opcional para o payload JSON; segredos resolvidos uma vez do ambiente."""

    url: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0
    # Muzzle: após enviar um alerta de falha para um job, não reenvia por N minutos
    # (evita spam no canal se a tarefa continua a falhar em ciclo). 0 = desligado.
    failure_alert_silence_minutes: float = 0.0
    url_env: str | None = None
    bearer_token_env: str | None = None
    allow_loopback_http: bool = False
    _bearer_token: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timeout_seconds",
            _webhook_number(
                self.timeout_seconds,
                "webhook.timeout_seconds",
            ),
        )
        object.__setattr__(
            self,
            "failure_alert_silence_minutes",
            _webhook_number(
                self.failure_alert_silence_minutes,
                "webhook.failure_alert_silence_minutes",
                allow_zero=True,
            ),
        )
        if not isinstance(self.allow_loopback_http, bool):
            raise ValueError("webhook.allow_loopback_http deve ser booleano")
        if self.url is not None and self.url_env is not None:
            raise ValueError("Configure somente webhook.url ou webhook.url_env")
        url = (
            _webhook_environment(self.url_env, "webhook.url_env")
            if self.url_env is not None
            else self.url
        )
        if url is not None:
            parse_webhook_url(url, allow_loopback_http=self.allow_loopback_http)
        token = _webhook_environment(self.bearer_token_env, "webhook.bearer_token_env")
        if token is not None:
            if len(token) > 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/\-]+=*", token):
                raise ValueError("Token Bearer invalido: use de 1 a 4096 caracteres RFC6750 ASCII")
            if url is None or urlsplit(url).scheme != "https":
                raise ValueError("Token Bearer de webhook exige uma URL HTTPS")
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "_bearer_token", token)

    @property
    def bearer_token(self) -> str | None:
        """Credencial apenas em memoria; nao incluir em logs ou serializacoes."""
        return self._bearer_token


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Configuração raiz do agendador."""

    jobs: tuple[JobConfig, ...]
    webhook: WebhookConfig = WebhookConfig()
    # Caminho do SQLite para histórico de execuções (string vazia desativa persistência).
    database_path: str = "scheduler.sqlite"
    # Módulo Python com função register(registry) — ver docs/ADDING_JOBS.md.
    jobs_register_module: str | None = None
    # Servidor HTTP do prometheus_client (expondo /metrics).
    metrics_enabled: bool = True
    metrics_host: str = "127.0.0.1"
    metrics_port: int = 9100


def interval_from_mapping(data: dict[str, Any]) -> IntervalConfig:
    raw_seconds = data.get("seconds")
    raw_minutes = data.get("minutes")
    raw_hours = data.get("hours")
    seconds = int(raw_seconds) if raw_seconds is not None else None
    minutes = int(raw_minutes) if raw_minutes is not None else None
    hours = int(raw_hours) if raw_hours is not None else None
    return IntervalConfig(seconds=seconds, minutes=minutes, hours=hours)


def retry_from_mapping(data: dict[str, Any] | None) -> RetryConfig:
    if data is None:
        return RetryConfig()
    defaults = RetryConfig()
    raw_attempts = data.get("attempts", defaults.attempts)
    raw_multiplier = data.get(
        "wait_multiplier_seconds",
        defaults.wait_multiplier_seconds,
    )
    raw_min = data.get("wait_min_seconds", defaults.wait_min_seconds)
    raw_max = data.get("wait_max_seconds", defaults.wait_max_seconds)
    return RetryConfig(
        attempts=int(raw_attempts),
        wait_multiplier_seconds=float(raw_multiplier),
        wait_min_seconds=float(raw_min),
        wait_max_seconds=float(raw_max),
    )
