# Security policy

## Supported versions

Security updates are applied to the **latest minor release** on the default branch (`main`). Older tags may not receive backports unless explicitly agreed with maintainers.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for undisclosed security vulnerabilities.

Instead, report sensitive issues privately so they can be handled responsibly:

1. Use **GitHub Security Advisories** for this repository (**Security** tab → **Report a vulnerability**), if enabled; or
2. Contact the maintainer via the channels linked on their GitHub profile, with enough detail to reproduce and assess impact.

Include:

- A clear description of the issue and affected components
- Steps to reproduce (proof-of-concept if safe to share)
- Potential impact (confidentiality, integrity, availability)
- Suggested fix or mitigation (optional)

You should receive an initial acknowledgement within a reasonable timeframe. Maintainers may coordinate disclosure (for example CVE assignment or release notes) once a fix is available.

## Scope

In scope: the **py-scheduler** codebase as shipped in this repository (CLI, library, Docker image defaults). Out of scope: vulnerabilities in third-party dependencies unless they affect this project’s usage in a material way—those should also be reported upstream.

## Operational boundaries

- The scheduler executes trusted Python modules with the privileges and environment of its operating-system account. It is not a sandbox for untrusted jobs or configuration, and it has no remote job-management API or user authorization system.
- Use environment-variable references for webhook URLs that contain credentials and for Bearer tokens. Protect process environments and configuration files. TLS protects transport; environment variables and in-memory objects are not encrypted secret storage.
- Webhook receivers must validate the optional Bearer credential or their own capability URL. The scheduler does not implement a signed-event protocol, replay detection, or automatic credential rotation. Restart after changing environment credentials.
- Metrics are intended for a trusted scraper. The default is loopback; a deliberate external bind needs network access controls or an authenticated TLS proxy. Job names and identifiers are visible in metrics, logs, history, and notification payloads.
- SQLite history, its WAL/SHM files, backups, and existing logs are ordinary local data. Restrict their filesystem access. Diagnostic hardening affects new runtime output and does not erase sensitive data produced by older versions.
- Job implementations and separately installed logging handlers can produce their own output. Do not log credentials in custom job code or enable HTTP wire debugging with real credentials.
