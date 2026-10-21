# isolation-check

`isolation-check` creates and verifies supported isolation boundaries for local Git worktree environments.

---

## The Problem

Two separate Git worktrees can contain independent branch checkouts, but if they share Docker Compose configurations, they often accidentally share resources on the same host:

```
Worktree A (feature-payments)    ──┐
                                   ├──> Named volume: "app_pgdata" (data collision!)
Worktree B (feature-analytics)   ──┘    Host binding: "127.0.0.1:3000" (port conflict!)
```

Running tests or database migrations in Worktree A can silently overwrite or corrupt data used in Worktree B, or fail to start because host port `3000` is already in use. `isolation-check` detects these collisions before containers start and provides automated preparation of isolated worktrees with dedicated storage and dynamic loopback ports.

---

## Early Alpha Warning

> [!WARNING]
> **Project Status (v0.3.0a1 - Public Alpha Preview):**
> `isolation-check` is in early alpha development. It is intended for testing and evaluation in local development environments.
> - Current scope is strictly limited to tested configurations: local Linux, Docker Compose, PostgreSQL, and one explicitly configured application service.
> - Destructive resource cleanup (`destroy`) is intentionally not implemented in this release to prevent accidental data loss.
> - Exit code `0` indicates that all evaluated rules passed verification; it does not constitute a guarantee of universal system isolation.

---

## Supported Scope & Support Matrix

The table below reflects capabilities verified by tests and active code in this release:

| Capability / Component | Status | Details |
|---|---|---|
| **Linux (Host & Container Engine)** | **Supported / Tested** | Primary execution target tested with Docker Compose. |
| **Docker Compose** | **Supported** | Requires Compose v2.20+ with support for `!override` and `!reset` YAML tags. |
| **PostgreSQL** | **Supported** | Single PostgreSQL service per environment with dedicated volume mount. |
| **Application Service** | **Supported** | Exactly one application service per environment with target container port (`app_port`). |
| **Dynamic Host Port Allocation** | **Supported** | Loopback binding (`127.0.0.1`) assigned at runtime via Docker Compose override. |
| **Non-Destructive Teardown (`down`)** | **Supported** | Stops containers/network; strictly preserves PostgreSQL volumes and worktree. |
| **Multiple Databases in one project** | *Unsupported* | Not supported in this milestone. |
| **Redis / MySQL / MongoDB** | *Unsupported* | Only PostgreSQL volume segregation is currently automated. |
| **macOS** | *Experimental / Untested* | Untested in automated CI pipelines. |
| **Native Windows** | *Experimental / Untested* | CLI tools execute, but primary deployment target is Linux Docker engines. |
| **Destructive Teardown (`destroy`)** | *Not Implemented* | Data volumes are never automatically deleted in this release. |

---

## Installation

### From Source or Wheel
```bash
# Clone the repository
git clone https://github.com/rxmna8502/isolation-check.git
cd isolation-check

# Install using pip (Python 3.11+ required)
pip install .
```

### Verify Installation
```bash
isolation-check --version
# Expected output: isolation-check 0.3.0a1

isolation-check --help
```

---

## Five-Minute Quick Start

A minimal ready-to-test setup is provided in [examples/postgres-app/](examples/postgres-app/).

```bash
# 1. Preview environment creation without modifying disk or Git
isolation-check create feature-payments --dry-run

# 2. Safely create two isolated environments
isolation-check create feature-payments
isolation-check create feature-analytics

# 3. Check for resource collisions between both worktrees
isolation-check check ../feature-payments ../feature-analytics

# 4. Start the first environment
isolation-check up feature-payments

# 5. Query live status (read-only)
isolation-check status feature-payments

# 6. Stop containers while preserving database volume
isolation-check down feature-payments
```

---

## Configuration Reference (`isolation-check.toml`)

To avoid guessing repository configuration, place `isolation-check.toml` in your repository root:

```toml
[create]
# Compose files in declared merge order
compose_files = ["docker-compose.yaml"]

# Application service name as declared in Compose
app_service = "web"

# PostgreSQL database service name as declared in Compose
db_service = "db"

# Environment variable name where application expects the connection URL
db_env_var = "DATABASE_URL"

# Application container target port (e.g. 3000)
# If omitted, inferred only if exactly one container port is declared
app_port = 3000

# Publish PostgreSQL to host (default: false, keeps db on internal network only)
publish_db = false
```

All TOML settings can be overridden at the command line using flags (e.g. `--app-port 8080`, `--app-service api`).

---

## Command Reference & Examples

### 1. `create` (Environment Preparation)
Creates a new Git worktree, branch, dedicated PostgreSQL volume, and loopback port override:
```bash
isolation-check create <environment-name> [--dry-run] [--app-port <port>]
```
- Creates untracked directory `.isolation-check/` in the new worktree (automatically added to `.git/info/exclude`).
- Writes `.isolation-check/compose.override.yaml` (mode `0600`).
- Writes `.isolation-check/manifest.json` (ownership manifest with schema version 1).
- Writes `.isolation-check/isolation.env` containing isolated database connection credentials.
- **Does not pre-allocate a host port:** Port allocation is deferred to `up` time.

### 2. `check` (Configuration Audit)
Compares two environments for declared resource collisions before starting containers:
```bash
isolation-check check <path1> <path2> [--format {text,json}] [--strict]
```
- `VOL001`: Identifies shared mounted named volume collisions (`FAIL`, exit 1) and read-only sharing (`WARN`, exit 0).
- `PORT001`: Identifies overlapping fixed IPv4 host port bindings (`FAIL`, exit 1).
- `COVERAGE`: Flags unevaluated runtime state (`UNKNOWN`).

### 3. `up` (Start Managed Environment)
Starts the Docker Compose project with loopback-only bindings:
```bash
isolation-check up <environment-name> [--json]
```
- Validates repository record, worktree ownership manifest, and Git registration.
- Probes Docker Compose for required `!override` and `!reset` capability support.
- Dynamically assigns an available loopback port on `127.0.0.1` (retries on port bind collision).
- Unpublishes PostgreSQL from the host (`ports: !reset []`) unless `publish_db = true`.
- Writes transient runtime state to `.isolation-check/runtime-state.json` (mode `0600`).
- Reports live loopback URL (e.g. `http://127.0.0.1:10001`).
- Idempotent: If already running with matching configuration, reports state without duplicate starts.

### 4. `status` (Query Environment State)
Read-only inspection of live container and health states:
```bash
isolation-check status <environment-name> [--json]
```
- Reports states: `RUNNING`, `HEALTHY`, `UNHEALTHY`, `STOPPED`, `PARTIAL`, `UNKNOWN`.
- Distinguishes running from healthy (reports `UNKNOWN` health if no healthcheck is configured).
- Validates container labels (`com.docker.compose.project`) and refuses foreign containers.
- Detects stale `runtime-state.json` if containers stopped externally.

### 5. `down` (Safe Teardown)
Stops containers and removes the project network while preserving data:
```bash
isolation-check down <environment-name> [--json]
```
- Stops project containers via `docker compose down` strictly without `-v` or `--volumes`.
- Preserves PostgreSQL data volume, worktree, branch, manifests, and private environment files.
- Destructive flags (`-v`, `--volumes`) are refused.
- Safely repeatable when already stopped.

---

## Safety Model & Guardrails

`isolation-check` is built around conservative execution rules:

1. **Subprocess Argument Arrays:** Commands are executed as argument arrays (`["docker", "compose", ...]`), never via shell interpolation (`shell=True`).
2. **Sanitized Environment:** Subprocesses run with a filtered, sanitized environment (`_build_safe_env()`) preventing accidental credential propagation.
3. **Bounded Capture & Timeouts:** All subprocess calls enforce bounded stdout/stderr capture (default 1MB) and explicit execution timeouts.
4. **Git Hook Isolation:** All tool-managed Git operations pass `-c core.hooksPath=/dev/null` to prevent repository hooks from running unexpectedly.
5. **No Tracked File Modification:** Tracked repository files, base Compose files, and `.env` files are never edited or overwritten.
6. **Path Boundary Validation:** Manifest paths, override paths, and base Compose files are checked against canonical worktree boundaries. Path traversal (`..`) and symlink escapes outside the worktree are strictly rejected.
7. **Secret Redaction:** Passwords and tokens never appear in terminal output, error messages, or JSON reports.
8. **Restrictive File Modes:** Credentials and runtime state are written with restrictive permissions (`mode 0600`).

---

## What the Tool Never Does Automatically

To avoid dangerous side effects, `isolation-check` will **never**:
- Execute destructive volume deletions or pass `--volumes` to Docker Compose.
- Run database migrations, seeding scripts, or arbitrary repository setup scripts.
- Execute arbitrary commands inside user containers.
- Connect to or probe external network databases.
- Bind host sockets permanently at environment creation time.
- Modify global Git configuration or bypass Git worktree verification.

---

## Finding Statuses & Exit Codes

### Rule Findings
- `PASS`: No supported resource collision was detected for the evaluated rule.
- `WARN`: Review recommended (e.g. read-only volume sharing).
- `FAIL`: A configured resource collision was detected (e.g. shared writable volume or port conflict).
- `UNKNOWN`: The condition could not be evaluated (e.g. unevaluated bind mounts or runtime state).

### Exit Codes
- `0`: Scan or lifecycle operation completed successfully without failure.
- `1`: Configured collision detected (`FAIL`) or runtime startup failure.
- `2`: Validation failure, missing inputs, unsupported Compose capability, or strict-mode `UNKNOWN`.

---

## Troubleshooting Guide

### 1. Docker Unavailable
- **Message:** `The 'docker' executable was not found on PATH.`
- **Cause:** Docker CLI is not installed or not available in the current environment PATH.
- **Fix:** Install Docker and ensure `docker` and `docker compose` can be executed from your terminal.

### 2. Unsupported Compose Version
- **Message:** `Docker Compose Capability Error: Installed Docker Compose version does not support required YAML tags (!override, !reset).`
- **Cause:** Docker Compose version is older than v2.20.
- **Fix:** Upgrade Docker Compose to v2.20 or newer (`docker compose version`).

### 3. Existing Branch or Worktree Destination
- **Message:** `Destination worktree path already exists` or `Branch already exists in repository`.
- **Cause:** An environment with the same name was already created or the Git branch exists.
- **Fix:** Choose a different environment name or manually clean up the previous worktree (`git worktree remove <path>` and `git branch -d <branch>`).

### 4. Multiple Candidate Container Ports
- **Message:** `Application service 'web' exposes multiple candidate container ports (...). Please explicitly configure 'app_port'.`
- **Cause:** The application service defines more than one port and `isolation-check` cannot safely guess developer intent.
- **Fix:** Add `app_port = <port>` in `isolation-check.toml` or pass `--app-port <port>`.

### 5. Fixed Container Names
- **Message:** `Service 'web' specifies fixed container_name '...'. Fixed container names cause collisions.`
- **Cause:** Base Compose file hardcodes `container_name`, which prevents running parallel environments.
- **Fix:** Remove `container_name` from your base Compose file to allow Docker Compose to generate project-scoped container names.

### 6. External Networks or Volumes
- **Message:** `Volume '...' uses custom storage driver` or `Network is declared as external`.
- **Cause:** External or custom network/storage drivers cannot be safely replicated across worktrees.
- **Fix:** Use standard Docker Compose named volumes and project-scoped networks.

### 7. Stale Runtime State
- **Message:** `Notice: Stored runtime state is STALE (containers have stopped).`
- **Cause:** Containers were stopped externally (e.g. via `docker stop` or system reboot).
- **Fix:** Run `isolation-check up <environment>` to start containers fresh, or `isolation-check down <environment>` to clean up the stopped state.

### 8. Environment Already Running
- **Message:** `isolation-check up -- ENVIRONMENT RUNNING (ALREADY ACTIVE)`
- **Behavior:** `up` is idempotent. It verifies container ownership and reports the existing active URL without duplicate startup.

### 9. Startup Failure & Preservation
- **Message:** `Failed to start environment: ... The environment and generated files have been preserved.`
- **Behavior:** Generated files and worktrees are preserved so you can diagnose the issue (`cd <worktree> && docker compose logs`).

---

## Known Limitations

- **Single Application Service:** Multi-service architectures where multiple distinct services require host port mapping are not yet supported.
- **PostgreSQL Only:** Automated dedicated volume segregation is currently implemented for PostgreSQL.
- **Bind Mounts:** Host filesystem directory mounts (`./src:/app`) are outside collision detection coverage.
- **Destructive Destroy:** Automated cleanup of dedicated database volumes (`destroy`) is planned for a future milestone.

---

## Development Setup

See [CONTRIBUTING.md](CONTRIBUTING.md) for full instructions on setting up a local development environment, running tests, and submitting changes.

---

## Community & Security

- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Security Reporting:** [SECURITY.md](SECURITY.md)
- **Support & Help:** [SUPPORT.md](SUPPORT.md)
- **Code of Conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

---

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
