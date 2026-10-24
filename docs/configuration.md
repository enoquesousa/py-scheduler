# Configuration

Configuration is a **single YAML file** passed as the first CLI argument, or defaults to the bundled `config.example.yaml` when no argument is provided.

## Environment variables

| Variable | Purpose |
| -------- | ------- |
| `PY_SCHEDULER_JOBS_MODULE` | Dotted import path for the module that defines `register(registry)`. Ignored when `jobs_register_module` is set in YAML. |

Webhook credentials use operator-chosen environment-variable names through `url_env` and `bearer_token_env` below. The scheduler reads the process environment; it does **not** automatically search for or load a `.env` file. A referenced variable must exist and be non-empty. Values are resolved when configuration is loaded; rotate the receiver credential, replace the process environment, and restart the scheduler.

## Top-level YAML keys

| Key | Type | Default | Description |
| --- | ---- | ------- | ----------- |
| `jobs` | list | _(required for a useful run)_ | Job definitions (see below). |
| `webhook` | object | omitted | Optional HTTPS notifications (see below). |
| `database_path` | string | `scheduler.sqlite` | SQLite path for execution history and webhook muzzle state. Use `""` to disable persistence. |
| `jobs_register_module` | string | `null` | Overrides `PY_SCHEDULER_JOBS_MODULE` when set. |
| `metrics_enabled` | bool | `true` | When `false`, the `/metrics` HTTP server is not started. |
| `metrics_host` | string | `127.0.0.1` | Bind address for metrics. An explicit external bind such as `0.0.0.0` exposes unauthenticated metrics to reachable clients; use network controls or an authenticated TLS proxy. |
| `metrics_port` | int | `9100` | TCP port for metrics. |

## `jobs[]` entries

| Key | Type | Required | Description |
| --- | ---- | -------- | ----------- |
| `id` | string | yes | Stable APScheduler job id (`replace_existing=True`). |
| `name` | string | yes | Registry key passed to `registry.register(...)`. |
| `interval` | object | yes | At least one of `seconds`, `minutes`, `hours` (integers). |
| `retry` | object | no | Tenacity policy: `attempts`, `wait_multiplier_seconds`, `wait_min_seconds`, `wait_max_seconds`. |
| `notify_on_success` | bool | `false` | When `true` and a webhook URL is configured, POST after each successful run. |

## `webhook` object

| Key | Type | Default | Description |
| --- | ---- | ------- | ----------- |
| `url` | string | `null` | Explicit HTTPS endpoint accepting the scheduler's JSON event format. Use `url_env` instead when its path/query contains a credential. |
| `url_env` | string | `null` | Name of an environment variable containing the complete URL; mutually exclusive with `url`. |
| `bearer_token_env` | string | `null` | Name of an environment variable containing the token for `Authorization: Bearer …`. Optional; the receiver must support and validate it. HTTPS is always required when set. |
| `allow_loopback_http` | bool | `false` | Development-only permission for HTTP to a literal loopback IP such as `127.0.0.1` or `::1`. Does not permit HTTP to hostnames or non-loopback addresses, or HTTP with a Bearer token. |
| `timeout_seconds` | number | `10` | Positive, finite timeout for blocking HTTP operations; not a total delivery deadline. |
| `failure_alert_silence_minutes` | number | `0` | After a failure alert for a job id, suppress duplicate failure webhooks for N minutes (requires SQLite for cross-restart behaviour). |

### HTTPS receiver with environment credentials

Have the service manager or secret manager inject `SCHEDULER_WEBHOOK_URL` and, if needed, `SCHEDULER_WEBHOOK_TOKEN`. The YAML stores only their names:

```yaml
webhook:
  url_env: SCHEDULER_WEBHOOK_URL
  bearer_token_env: SCHEDULER_WEBHOOK_TOKEN
  timeout_seconds: 10
  failure_alert_silence_minutes: 30
```

Omit `bearer_token_env` if the receiver does not expect a Bearer header. A capability URL may authenticate through a secret in its path or query; that value remains part of the HTTP request target and may be logged by the receiver or an intervening TLS terminator. Treat the complete URL as a secret. The sender omits the URL and token from its configuration representation and delivery logs.

URLs must use an ASCII URL representation, with non-ASCII path/query characters percent-encoded and international hostnames in their ASCII form. User information (`user:password@host`), fragments, control characters, and malformed ports are rejected. Existing HTTPS path/query values are preserved for capability endpoints. There is no special `$VAR` interpolation in a literal `url`.

Bearer values must match the HTTP Bearer token syntax: 1–4096 ASCII characters, without whitespace or control characters. Supply only the token value, without the `Bearer ` prefix. A missing, empty, or invalid referenced value is a configuration error, not a fallback to an unauthenticated request.

Only HTTP 2xx is successful. Redirects are failures and are never followed; the sender does not read or log response bodies. TLS certificate and hostname verification remain enabled. There is no `verify=false` option or automatic HTTP fallback. The timeout applies to blocking operations, so it does not impose a strict wall-clock deadline against a receiver that sends headers very slowly.

### Local development receiver

Use synthetic payloads and a local receiver you control:

```yaml
webhook:
  url: http://127.0.0.1:9000/events
  allow_loopback_http: true
```

`localhost`, a hostname that resolves to loopback, and a remote HTTP URL are not accepted by this exception. Do not configure a Bearer token in HTTP mode. Existing remote HTTP configurations must migrate to HTTPS. Unknown keys in the `webhook` object fail validation so misspelled credential options are not silently ignored.

### Event payload and delivery limits

The receiver gets generic JSON with `event`, `job_name`, and `timestamp`; failure events also contain `job_id` and sanitized error metadata. This is not a provider-specific Slack or Discord adapter. Use a receiver that accepts this schema or converts it to the provider's message format.

The failure-alert silence window records an attempted notification before delivery. A failed delivery can therefore suppress further alerts during the configured window. With SQLite enabled that state persists across restarts; without SQLite it stays in memory. A successful job clears its failure-alert silence state. This behavior is unchanged; there is no durable notification queue, guaranteed delivery, signature, or replay-prevention protocol.
