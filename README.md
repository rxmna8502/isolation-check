# isolation-check

`isolation-check` is a local command-line tool that audits configuration between separate local development environments (e.g., Git worktrees) and flags accidental resource sharing—such as Docker volumes, databases, or host port bindings—before tests or migrations run. It also safely prepares isolated Git worktrees with generated Docker Compose environments and manages their local runtime lifecycle (`up`, `status`, `down`).

> **Project Status (v0.2.0a1 - Milestone: Managed Runtime Lifecycle & Loopback Port Allocation):**
> Design-stage open-source development tool (Package version: `0.2.0a1`, Manifest schema version: `1`).
> - **Implemented:**
>   - Configuration auditing for Docker Compose named volumes (`VOL001`) and fixed IPv4 host-port bindings (`PORT001`).
>   - Safe Git worktree creation (`create`) with generated Compose overrides, dedicated PostgreSQL volume storage, loopback port binding policy, and versioned ownership manifests.
>   - Managed runtime lifecycle (`up`, `status`, `down`) with strict path and ownership verification, Compose capability detection, dynamic loopback port assignment (`127.0.0.1`), health inspection, stale state detection, and volume-preserving teardown.
> - **Planned (Future Milestones):** Automated worktree discovery (`isolation-check discover`), database migrations, database seeding, cloning, and destructive environment teardown (`isolation-check destroy`).
>
> **Safety Notice:** `isolation-check` operates on local Docker Compose projects and Git worktrees. It verifies declared named volume segregation, fixed IPv4 port bindings, and loopback application binding. Resources outside this coverage—such as arbitrary host bind mounts, external databases, shared host operating system files, and networks outside Compose—are not evaluated. Exit 0 indicates that evaluated resources passed verification, but does not guarantee absolute system isolation.

---

## Command Reference: Implemented vs. Planned

| Command | Status | Purpose |
|---|---|---|
| `isolation-check check <left> <right>` | **Implemented** | Audit two Compose environments for volume (`VOL001`) and host-port (`PORT001`) collisions. |
| `isolation-check create <name> --dry-run` | **Implemented** | Validate configuration, show proposed branch, worktree path, project name, volume, and configuration plan without making any disk or Git changes. |
| `isolation-check create <name>` | **Implemented** | Safely create Git worktree, dedicated Compose project, dedicated PostgreSQL volume, loopback port policy, and versioned ownership manifest. |
| `isolation-check up <name>` | **Implemented** | Verify ownership/capabilities, assign dynamic host loopback port, start managed Compose project with unexposed database, and record runtime state. |
| `isolation-check status <name>` | **Implemented** | Read-only query of container states, health status (RUNNING vs HEALTHY), actual loopback URL, and stale runtime state detection. |
| `isolation-check down <name>` | **Implemented** | Safely stop managed containers and network while strictly preserving PostgreSQL data volumes, worktrees, manifests, and private environment files. |
| `isolation-check discover` | *Planned* | Machine-readable listing of local worktrees and Compose environments. |
| `isolation-check destroy <name>` | *Planned* | Teardown and cleanup of managed resources including data volumes (not implemented in this milestone). |

---

## Environment Configuration & Inputs

Scope: **Local Linux, Docker Compose, PostgreSQL, and one explicitly configured application service.**

### Explicit Inputs Requirement
To avoid guessing or inspecting arbitrary repository files, developers must explicitly identify:
1. Source repository (`--repo`, defaults to current working directory) and base revision (`--base-ref`, defaults to `HEAD`).
2. Compose files in declared merge order (`-f` / `--compose-file`).
3. Application service name (`--app-service`).
4. PostgreSQL service name (`--db-service`).
5. Application database connection environment variable name (`--db-env-var`).
6. Application container target port (`--app-port` or `app_port` in config). If omitted, inferred only when the effective Compose application service has exactly one unambiguous container port.

These mappings can be supplied via CLI flags or a project `isolation-check.toml` configuration file:

```toml
# isolation-check.toml in repository root
[create]
compose_files = ["docker-compose.yaml"]
app_service = "web"
db_service = "db"
db_env_var = "DATABASE_URL"
app_port = 3000
publish_db = false
```

### Application Port & Host Binding Semantics
- `app_port` designates the application container's target port (e.g. 3000), not the host port.
- Host ports are **not permanently allocated at create time**. A host port that is free during environment creation may be occupied when the environment is started.
- At `up` time, `isolation-check` dynamically assigns a host loopback port on `127.0.0.1` and verifies the actual binding through Docker/Compose state.
- PostgreSQL remains completely unpublished on the host (`ports: !reset []`) unless `publish_db = true` was explicitly configured.

---

## How to Run

### 1. Dry Run
Validate inputs, check for unsupported configurations, and display the proposed plan without making any changes:
```bash
isolation-check create feature-payments --dry-run
```

### 2. Create Environment
Create a new Git worktree with dedicated PostgreSQL storage and loopback port override:
```bash
isolation-check create feature-payments
```

Generated structure in the new worktree:
```
../feature-payments/
├── .isolation-check/
│   ├── compose.override.yaml   # Overrides project name, mounts dedicated volume, sets loopback port
│   ├── isolation.env           # Private environment file (mode 0600)
│   ├── manifest.json           # Versioned ownership manifest (schema version 1)
│   └── runtime-state.json      # Transient runtime state written during 'up' (mode 0600)
├── docker-compose.yaml         # Original tracked Compose file (untouched)
└── ... (repository source files)
```
*(The `.isolation-check/` folder is automatically excluded in `.git/info/exclude` so it is never committed).*

### 3. Start Environment (`up`)
Verify ownership and start the managed Compose project:
```bash
isolation-check up feature-payments
# Or output machine-readable JSON:
isolation-check up feature-payments --json
```
- Binds application to a dynamic host port on `127.0.0.1`.
- Outputs loopback URL (e.g. `http://127.0.0.1:10001`).
- Distinguishes running from healthy (reports `UNKNOWN` health if no container healthcheck is defined).
- Idempotent: if already active with matching configuration, reports current state without duplicating containers.

### 4. Query Status (`status`)
Read-only inspection of live container and health states:
```bash
isolation-check status feature-payments
# Or output machine-readable JSON:
isolation-check status feature-payments --json
```
- Reports states: `RUNNING`, `HEALTHY`, `UNHEALTHY`, `STOPPED`, `PARTIAL`, `UNKNOWN`.
- Verifies container labels match the expected Compose project.
- Detects stale `runtime-state.json` (e.g., if containers stopped externally, suppresses stale URLs and flags stale state).

### 5. Stop Environment (`down`)
Safely stop containers while preserving database storage:
```bash
isolation-check down feature-payments
```
- Stops containers and removes the project network.
- **Never removes volumes:** Dedicated PostgreSQL volume, worktree, branch, manifests, and credentials are preserved.
- Destructive flags (`-v`, `--volumes`) are refused.
- Safely repeatable when already stopped.

### 6. Compare Environments (`check`)
Audit configuration sharing between two worktrees or Compose projects:
```bash
isolation-check check ../feature-a ../feature-b
```
Outputs findings for:
- `VOL001`: Shared writable named volume collisions (`FAIL`, exit 1) or read-only sharing (`WARN`, exit 0).
- `PORT001`: Overlapping fixed IPv4 host port bindings (`FAIL`, exit 1).
- `COVERAGE`: Disclaimer noting unevaluated runtime state.

---

## Verified Resources vs. Out of Scope

| Resource Type | Status | Details |
|---|---|---|
| Named Docker Volumes | **Verified** | Evaluated by `VOL001` and isolated via dedicated volume per environment during `create`. |
| Host Port Bindings | **Verified** | Fixed IPv4 host port collisions evaluated by `PORT001`. Managed environments bound to loopback `127.0.0.1`. |
| Database Storage | **Verified** | PostgreSQL data volume segregated per environment; database unpublished from host by default. |
| Bind Mounts | *Outside Coverage* | Host directory bind mounts are not checked for overlapping filesystem paths. |
| Database Schemas | *Outside Coverage* | Internal database schema state and migrations are not managed or evaluated. |
| Host OS Files & Sockets | *Outside Coverage* | Filesystem paths outside the worktree boundary are not scanned for shared locks or sockets. |

---

## Exit Codes

| Exit Code | Meaning |
|---|---|
| `0` | Scan or lifecycle operation completed successfully without failure. |
| `1` | A configured sharing collision was found (`check` found at least one `FAIL`) or runtime startup failed. |
| `2` | Invalid input, ownership validation failure, unsupported Compose capability, or strict-mode `UNKNOWN`. |

---

## Architecture & Code Structure

```
src/isolation_check/
├── cli.py              # CLI argument parser and command router (check, create, up, status, down)
├── models.py           # Dataclasses: EngineScope, HostScope, VolumeMount, PortBinding, Finding, Report
├── version.py          # Authoritative package version helper and manifest schema version
├── collect/
│   └── compose.py      # Subprocess execution of docker compose config with bounded I/O and sanitized env
├── normalize/
│   └── compose.py      # Pure mapping from Compose JSON to NormalizedEnvironment
├── rules/
│   ├── volumes.py      # Pairwise rule VOL001 evaluation
│   └── ports.py        # Pairwise rule PORT001 evaluation (fixed IPv4, wildcards, protocols)
├── report.py           # Text and versioned JSON report renderers
├── create/
│   ├── spec.py         # Explicit CreateSpec definition, app_port parsing, and TOML loader
│   ├── validate.py     # Pre-flight validation, app_port inference, and unsupported arrangement refusal
│   ├── worktree.py     # Safe Git worktree operations with disabled hooks (core.hooksPath=/dev/null)
│   ├── generator.py    # Generates override Compose YAML, private env file, and manifest
│   ├── verify.py       # Post-generation Compose verification of merged model
│   └── manager.py      # Create workflow orchestrator with rollback tracking and work preservation
└── runtime/
    ├── capabilities.py # Probes Docker Compose version for !override and !reset support
    ├── validation.py   # Strict ownership, worktree registration, and path boundary verification
    ├── port_allocator.py# Dynamic candidate loopback port allocation with conflict detection
    └── lifecycle.py    # Subcommand implementations for up, status, and non-destructive down
```

---

## Test Suite

Run all unit and integration tests:
```bash
python -m unittest discover -s tests -v
```

The test suite cleanly separates:
- **Unit tests with mocks:** CLI parsing, app_port inference/validation, compose override generation, rules, reports, ownership validation, and runtime lifecycle states (up, status, down).
- **Git integration tests:** Real disposable Git repository worktree creation, manifest tracking, and Compose rendering verification.
- **Docker lifecycle smoke test:** Disposable two-environment simultaneous run verifying loopback ports, unexposed PostgreSQL, and volume preservation (executes when local Docker daemon is reachable; cleanly skips when daemon is offline).
