# Changelog

All notable changes to `isolation-check` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.2.0a1] - Unreleased (Development Milestone: Managed Runtime Lifecycle)

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
- Authoritative package version helper in `src/isolation_check/version.py` (`0.2.0a1`) decoupled from manifest schema version (`1`).

### Changed
- Compose override generation now unpublishes PostgreSQL (`ports: !reset []`) unless `publish_db: true` is explicitly enabled.
- Application port is published only to `127.0.0.1` using `ports: !override`.
- Documentation updated to replace planned `start` with implemented `up`, `status`, and `down`.

## [0.1.0a3] - Previous Development Milestone (Worktree Creation)

### Added
- Environment creation via `isolation-check create <environment>` (`--dry-run`).
- Dedicated PostgreSQL volumes and connection URL generation.
- Ownership manifest recording tool intent in worktree.

## [0.1.0a1] - Initial Proof-of-Concept

### Added
- Compose named volume collision detection (`VOL001`).
- Fixed IPv4 host port collision detection (`PORT001`).
