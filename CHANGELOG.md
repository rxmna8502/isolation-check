# Changelog

All notable changes to `isolation-check` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Keyboard-driven interactive Terminal User Interface (TUI) powered by Textual as the default experience when running `isolation-check` with no arguments in an interactive terminal.
- Home screen navigation with support for 8 top-level workflows: Get started, Create an environment, Manage environments, Check for collisions, Check prerequisites, Check for updates, Help, and Exit.
- Interactive "Get started" 5-step sequential wizard guiding users through:
  1. Selecting and verifying a local Git repository.
  2. Diagnosing runtime prerequisites with clear distinction between warnings and blocking failures.
  3. Inspecting Compose configurations and explicitly confirming distinct application and PostgreSQL service mappings.
  4. Configuring database connection environment variables and application container target ports (kept distinct from dynamic runtime host ports).
  5. Reviewing the proposed configuration, rendering unified overwrite diffs against existing `isolation-check.toml`, and requiring explicit modal confirmation before writing.
- Dynamic wizard navigation featuring Back and Next actions, active step progress indicators, full backward input preservation, dependent downstream validation invalidation upon upstream input changes, and plain-language error summaries with technical output tucked behind expandable "Show details" toggles.
- Interactive "Create an environment" form with dry-run planning, pre-flight validation error display, password redaction, and explicit confirmation before creating Git worktrees and Compose environments.
- Interactive "Manage environments" dashboard displaying registered environments, observed container state, loopback application URLs, and health statuses, with capabilities to inspect details, start environments, safely stop environments (with volume preservation confirmation), and refresh live state.
- Interactive "Check for collisions" screen to audit two environment configurations using `VOL001` and `PORT001` rules with clear separation of PASS, WARN, FAIL, and UNKNOWN findings and coverage disclaimers.
- Interactive "Check prerequisites" screen providing read-only diagnosis of Python version, Git availability, Docker CLI, Docker daemon reachability, Docker Compose version, and sequence override tag support, with actionable repair instructions and runtime scope explanations.
- Interactive "Check for updates" screen with on-demand PyPI metadata querying, PEP 440 version comparison, stable vs prerelease categorization, offline mode tolerance, and safe copyable upgrade commands for pip, pipx, and uv.
- Interactive "Help" screen documenting core workflows, safety boundaries, and direct CLI command examples.
- Headless TUI test suite testing interactive launches, non-interactive CLI fallback, menu navigation, lifecycle operations, prereq diagnosis, update checks, and safety guardrails.

### Changed
- Promoted Textual to a required runtime dependency in `pyproject.toml`.
- Added `packaging` runtime dependency in `pyproject.toml` for PEP 440 version parsing.
- Refactored lifecycle, create, and status routines to expose typed structured data objects without altering existing CLI contracts, JSON output schemas, or exit codes.

### Fixed
- Enforced strict exact OCI image repository matching (`extract_image_repository()`) for PostgreSQL candidates in `validate_postgres_service_definition()` and TUI adapters, supporting only the official `postgres` distribution repository (`docker.io/library/postgres`, `postgres`, `library/postgres`).
- Classified database variants (PostGIS, TimescaleDB, Crunchy Data, and custom forks) as unverified and unsupported with tailored explanations, displaying them in the dedicated unsupported datastores callout rather than loose substring acceptance.
- Restored test discovery and regression coverage for all 8 Get Started wizard tests in `tests/unit/test_tui.py` (`TestGetStartedWizard`), bringing discovery from 160 to 168 tests (167 passing, 1 platform-specific skip).
- Fixed service-selection UX in Get Started wizard to classify services from resolved Compose models using shared backend validation rules rather than raw service names, ensuring unrelated services (Kafka, Mailpit, Redis, web-portal) are never offered as PostgreSQL candidates.
- Added explicit evidence display (image tags) for qualifying PostgreSQL candidates, actionable explanations blocking progression when no PostgreSQL service qualifies, and separate callout boxes showing unrecognized or custom database services with their unsupported reasons.
- Enhanced application selector to display declared target ports, source/image details, and infrastructure labels (`[Infrastructure: ...]`), requiring explicit user confirmation via "Confirm Service Selection" instead of auto-confirming the first alphabetic candidate.
- Added strict connection configuration validation for selected application-database pairs before allowing progression or saving, automatically invalidating confirmation and downstream configuration upon selection changes.
- Bounded Select overlay dropdown heights with scrolling and added accessible keyboard navigation instructions, keeping navigation reachable across standard 80x24 and 100x34 terminal dimensions.
- Fixed return contract mismatch in `save_config_toml()` which previously returned `None` instead of `tuple[bool, str | None]`, causing a `TypeError: cannot unpack non-iterable NoneType object` crash when saving or confirming an overwrite in the Get Started wizard.
- Added explicit return contract `tuple[bool, str | None]` and safe atomic write validation to `save_config_toml()`, guaranteeing existing configuration files are preserved on write errors and presenting sanitized inline error messages in the TUI without crashing.

## [0.3.0a2] - 2026-10-21 (Public Alpha Release Hardening)

Supersedes `v0.3.0a1` with critical concurrency, process-identity, port allocation, and permission hardening:

### Fixed & Hardened
- **Active-Lock Safety**: Lock files are never reclaimed based on elapsed time while verified owner processes remain alive. Separated caller wait timeout from lock abandonment criteria.
- **Lock-Owner Tokens & PID-Reuse Protection**: Lock records now store unique owner tokens, host identity, command name, and process start-time identity (`/proc/[pid]/stat` on Linux, `GetProcessTimes` on Windows) to detect PID reuse and prevent one process from deleting another's lock.
- **Cross-Environment Port Synchronization**: Resolved port allocation races between concurrent environments using atomic reservation files (`.isolation-check/ports/<port>.json`) and a repository-scoped port-allocation lock during Docker Compose startup.
- **Atomic State Writes**: All generated configuration, environment records, manifest files, and runtime-state files use temporary files with `os.fsync` and `os.replace` to prevent partial or corrupted reads.
- **Private-File Permission Preservation**: Private files such as `isolation.env` and runtime state are created with restrictive permissions (`0600`) at initial file creation time on POSIX, eliminating any broad-permission window.
- **Exit-Code Contract Enforcement**: Strictly enforces exit codes across text and JSON output: `0` for no failures (warnings/unknown permitted in normal mode), `1` for one or more failures (`VOL001`, `PORT001`, etc.), and `2` for invalid inputs, arguments, or strict-mode unresolved results.
- **Removal of Destructive Down Flags**: Completely removed `-v` and `--volumes` from `down` argument parser; passing either is rejected with exit code 2 to guarantee database volumes are never accidentally removed.

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
