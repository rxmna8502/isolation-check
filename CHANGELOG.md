# Changelog

All notable changes to `isolation-check` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.3.0a1] - Unreleased (Public Alpha Release Readiness)

### Added
- Standard `--version` (`-V`) CLI flag displaying authoritative package version.
- Python packaging infrastructure in `pyproject.toml` adhering to PEP 621 / PEP 517 / PEP 440 with build backend setuptools.
- `MANIFEST.in` and `py.typed` marker for PEP 561 compliance.
- Complete open-source project documents:
  - `LICENSE` (Apache-2.0)
  - `CONTRIBUTING.md` with development setup, coding guidelines, and PR workflow
  - `SECURITY.md` with private vulnerability reporting and secret-redaction requirements
  - `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1)
  - `SUPPORT.md` with issue guidance and links
- Verified minimal working example under `examples/postgres-app/`.
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) testing Linux builds, matrix tests, and clean wheel installation.
- GitHub Actions release workflow (`.github/workflows/release.yml`) with PyPI Trusted Publishing preparation.
- Issue and PR templates (`.github/ISSUE_TEMPLATE/bug_report.yml`, `feature_request.yml`, `pull_request_template.md`).
- Coding agent integration guide (`docs/agent-integration.md`).
- Comprehensive support matrix and troubleshooting guide in `README.md`.

## [0.2.0a1] - Previous Development Milestone (Managed Runtime Lifecycle)

### Added
- Managed runtime lifecycle CLI subcommands:
  - `isolation-check up <environment>` (`--json`, `--repo`)
  - `isolation-check status <environment>` (`--json`, `--repo`)
  - `isolation-check down <environment>` (`--json`, `--repo`, strictly non-destructive)
- Explicit application container target port contract (`app_port` / `--app-port`):
  - Inferred when exactly one unambiguous container port is published or exposed.
  - Refuses zero candidate container ports with requirement for explicit configuration.
  - Refuses multiple candidate container ports with requirement for explicit configuration.
  - Validates integer range between 1 and 65535.
- Dynamic loopback host-port allocation on `127.0.0.1` at `up` time without permanent pre-allocation during `create`.
- Docker Compose override tag capability detection (`!override`, `!reset`).
- Strict ownership, worktree registration, and path boundary validation on all runtime commands.
- Foreign Compose project container detection and refusal via container labels.
- Read-only status inspection distinguishing `RUNNING` from `HEALTHY` (`UNKNOWN` health when no healthcheck exists).
- Detection of stale `runtime-state.json` when containers are stopped externally.
- Volume-preserving teardown in `down` (refusal of `-v`/`--volumes` flags).
- Authoritative package version helper in `src/isolation_check/version.py` decoupled from manifest schema version (`1`).

### Changed
- Compose override generation unpublishes PostgreSQL (`ports: !reset []`) unless `publish_db: true` is explicitly enabled.
- Application port is published only to `127.0.0.1` using `ports: !override`.

## [0.1.0a3] - Previous Development Milestone (Worktree Creation)

### Added
- Environment creation via `isolation-check create <environment>` (`--dry-run`).
- Dedicated PostgreSQL volumes and connection URL generation.
- Ownership manifest recording tool intent in worktree.

## [0.1.0a1] - Initial Proof-of-Concept

### Added
- Compose named volume collision detection (`VOL001`).
- Fixed IPv4 host port collision detection (`PORT001`).
