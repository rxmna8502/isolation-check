# 03 — Architecture & Repository Setup

## Recommended implementation

Use **Python 3.11+**, a small synchronous CLI, and standard-library modules initially: `argparse`, `dataclasses`, `json`, `pathlib`, `subprocess`, `urllib.parse`, `ipaddress`, `tomllib`, and `unittest`.

Why: fast iteration, straightforward testing, and no need to hand-parse Compose YAML. A Go/Rust port can be considered later; do not maintain two implementations during the first release.

## End-to-end flow
```txt
CLI + explicit inputs
        |
        v
Input validation / environment selection
        |
        v
Collectors: Git + Compose canonical JSON
        |
        v
Normalize identities + provenance + uncertainties
        |
        v
Pure pairwise rule engine
        |
        v
Policy / explicit exceptions
        |
        v
Redacted text or versioned JSON report
```

Collection performs controlled I/O. Rules operate on normalized in-memory data. Rendering receives only allowlisted, redacted evidence.

## Repository layout
```txt
isolation-check/
  pyproject.toml
  README.md
  LICENSE
  CONTRIBUTING.md
  SECURITY.md
  CHANGELOG.md
  src/isolation_check/
    __init__.py
    cli.py
    models.py
    collect/git.py
    collect/compose.py
    normalize/compose.py
    normalize/postgres.py
    rules/project.py
    rules/ports.py
    rules/volumes.py
    rules/postgres.py
    policy.py
    redact.py
    report.py
  tests/
    unit/
    fixtures/
    integration/
  docs/
  examples/
```

Keep normalization out of the CLI and subprocess execution out of rule functions.

## Local setup

These commands create a new implementation workspace; they do not download or install an existing released product.
```bash
mkdir isolation-check
cd isolation-check
git init
python3 -m venv .venv
. .venv/bin/activate
mkdir -p src/isolation_check tests/unit tests/fixtures docs examples
printf '' > src/isolation_check/__init__.py
```

On the initial supported platform, verify prerequisites:
```bash
python3 --version
git --version
docker compose version
```

Record the exact tested Compose version in release notes. Do not promise every Compose v2 version works without testing it.

## Packaging skeleton
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "isolation-check"
version = "0.1.0a1"
description = "Inspect configured resource sharing between local development environments"
requires-python = ">=3.11"

[project.scripts]
isolation-check = "isolation_check.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

The distribution name is provisional; check registry availability first. The entry point will not work until `cli.py` contains `main()`. Setuptools installation may need package-network access in a real development environment.

After implementing the entry point:
```bash
python -m pip install -e .
python -m unittest discover -s tests/unit -v
isolation-check --help
```

## Collector contracts

**Git collector:** list worktrees with `git -C <repo> worktree list --porcelain -z`. Parse record boundaries, bare/detached entries, and unusual paths explicitly.[\[1\]](https://git-scm.com/docs/git-worktree)

**Compose collector:** use `docker compose ... config --format json` with explicit file lists and project directory. Compose renders a canonical data model, merging selected files and resolving variables; do not reproduce that logic with a home-grown YAML parser.[\[2\]](https://docs.docker.com/reference/cli/docker/compose/config/)

The collector must check command exit status, time out, bound output size, validate JSON shape, and keep raw output out of logs. A simple `capture_output=True` plus an after-the-fact length check does not prevent memory exhaustion; use bounded streaming/temp storage or terminate once the limit is exceeded.

## Controlled subprocess execution

- Pass an argument array; never use `shell=True`.
- Prefer verified executable paths; do not execute a `docker` or `git` binary found inside the repository.
- Supply explicit working directories and Compose options.
- Do not inherit arbitrary `COMPOSE_*`, `DOCKER_HOST`, or project environment variables silently.
- Preserve necessary platform variables through a documented allowlist.
- Resolve requested variables through explicit per-environment input files/overrides.
- Preserve diagnostic meaning without echoing sensitive stderr or interpolated values.

## Core normalized records

Each environment needs: label, canonical root, declared engine scope and basis, effective Compose project name, selected profiles/services, resource list, and collection diagnostics.

Each resource needs: kind, identity components, access mode, owner service, provenance reference, and unresolved fields. Provenance should be a safe pointer such as `services.api.environment.DATABASE_URL`, not the secret value.

## Study exercise

Implement a fake collector that returns a fixed normalized environment. Run all rules against that object before connecting the CLI to Docker. This keeps early development deterministic and independent of a running engine.
