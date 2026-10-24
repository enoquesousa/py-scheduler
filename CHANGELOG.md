# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Require verified HTTPS for outbound webhooks by default. Permit HTTP only for an explicitly enabled literal-loopback development receiver, without a Bearer credential.
- Add environment-variable references for secret webhook URLs and optional Bearer tokens; reject missing credentials, conflicting URL sources, and unknown webhook options before delivery.
- Omit credential-bearing URLs, tokens, response bodies, and raw transport exceptions from webhook diagnostics. Treat redirects as failed delivery and close responses without reading their bodies.
- Keep job exception classes and execution metadata while removing raw exception values from built-in logs, new history rows, and failure notifications. Preserve retries, original exceptions, and APScheduler error events for in-process consumers.
- Restrict browser access to the loopback metrics exporter using actual-peer, Host, and Origin checks. Explicit external binds retain their documented unauthenticated scrape behavior.
- Limit workflow credentials to their jobs, disable checkout credential persistence, and pin actions to reviewed commits. Retain coverage artifacts for pull requests and isolate eligible Codecov uploads and release-only PyPI publishing behind short-lived OIDC credentials.

### Fixed

- Include the bundled example configuration before the Docker image's Hatchling package build.
- Load default retry values from a `RetryConfig` instance so partial YAML retry blocks do not attempt to convert dataclass slot descriptors into numbers.
- Document webhook payload compatibility, credential rotation, existing SQLite/log data, and the network boundary of Docker metrics publishing.

### Added

- Documentation set aligned with sibling templates (architecture, configuration, development).
- `scripts/smoke_validate.py` for fast config + registry validation.
- MIT `LICENSE`, community docs, Codecov config, pre-commit Ruff hooks, CodeQL workflow.
- CI: dependency review on pull requests; coverage upload on Python 3.12.

## [0.1.0] - 2026-05-10

### Added

- Initial release: YAML-driven APScheduler jobs, SQLite execution history, webhooks, Prometheus metrics, Tenacity retries.

[Unreleased]: https://github.com/enoquesousa/py-scheduler/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/enoquesousa/py-scheduler/releases/tag/v0.1.0
