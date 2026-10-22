"""Validation rules and unsupported arrangement detection for environment creation."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any, NamedTuple

from isolation_check.collect.compose import _build_safe_env
from isolation_check.create.spec import CreateSpec
from isolation_check.create.worktree import (
    branch_exists,
    is_git_repository,
    resolve_commit_hash,
)
from isolation_check.runtime.capabilities import ComposeCapabilityError, verify_compose_capabilities


class ValidationError(Exception):
    """Raised when environment creation validation fails."""


SUPPORTED_POSTGRES_REPOSITORIES: tuple[str, ...] = ("postgres",)


def extract_image_repository(image: str | None) -> str:
    """Extracts the normalized OCI image repository identity (excluding tags and digests).

    Strips registry host/port (if docker.io/library), tags, and digests.
    Examples:
        'postgres:15-alpine' -> 'postgres'
        'docker.io/library/postgres:16' -> 'postgres'
        'library/postgres:latest' -> 'postgres'
        'postgis/postgis:15-3.3' -> 'postgis/postgis'
        'timescale/timescaledb:latest-pg15' -> 'timescale/timescaledb'
        'crunchydata/crunchy-postgres:ubi8-15.2-0' -> 'crunchydata/crunchy-postgres'
        'postgres-custom:latest' -> 'postgres-custom'
    """
    if not image:
        return ""
    img = image.strip().split("@")[0]
    segments = img.split("/")
    last_seg = segments[-1]
    if ":" in last_seg:
        segments[-1] = last_seg.split(":", 1)[0]
    repo = "/".join(segments)

    # Normalize standard Docker Hub library prefixes
    if repo.startswith("docker.io/library/"):
        repo = repo[len("docker.io/library/"):]
    elif repo.startswith("docker.io/"):
        repo = repo[len("docker.io/"):]
    elif repo.startswith("library/"):
        repo = repo[len("library/"):]
    return repo.lower()


def is_supported_postgres_image(image: str | None) -> bool:
    """Checks whether an image corresponds to an explicitly tested and supported official PostgreSQL repository."""
    if not image:
        return False
    repo = extract_image_repository(image)
    return repo in SUPPORTED_POSTGRES_REPOSITORIES


def validate_postgres_service_definition(service_name: str, service_cfg: dict[str, Any]) -> str | None:
    """Validates that a service definition satisfies backend PostgreSQL requirements.

    Returns:
        None if valid, or a descriptive error string if unsupported.
    """
    if not isinstance(service_cfg, dict):
        return f"PostgreSQL service '{service_name}' configuration is invalid (expected a mapping)."

    # Check fixed container name (Rule 12)
    c_name = service_cfg.get("container_name")
    if c_name:
        return (
            f"Unsupported configuration: Service '{service_name}' specifies fixed container_name '{c_name}'. "
            "Docker container names are globally unique per host and prevent parallel environments from running."
        )

    # Check custom entrypoint (Rule 9)
    entrypoint = service_cfg.get("entrypoint")
    if entrypoint:
        return (
            f"Unsupported configuration: PostgreSQL service '{service_name}' defines a custom entrypoint "
            f"({entrypoint!r}). Custom database initialization scripts cannot be safely verified or transformed."
        )

    # Verify PostgreSQL service image (Rule 10)
    image = str(service_cfg.get("image") or "").strip()
    if not image:
        if "build" in service_cfg:
            return (
                f"Unsupported configuration: Service '{service_name}' is built from source (build: {service_cfg.get('build')}). "
                "Only pre-built official PostgreSQL distribution images ('postgres') are supported."
            )
        return f"Unsupported configuration: Service '{service_name}' specifies neither an image nor a build."

    repo = extract_image_repository(image)
    if repo not in SUPPORTED_POSTGRES_REPOSITORIES:
        if "postgis" in repo:
            return (
                f"Unsupported configuration: Service '{service_name}' uses PostGIS image '{image}'. "
                "PostGIS variants are unverified and unsupported. Only official 'postgres' distribution images are supported."
            )
        if "timescale" in repo:
            return (
                f"Unsupported configuration: Service '{service_name}' uses TimescaleDB image '{image}'. "
                "TimescaleDB variants are unverified and unsupported. Only official 'postgres' distribution images are supported."
            )
        if "crunchy" in repo:
            return (
                f"Unsupported configuration: Service '{service_name}' uses Crunchy Data image '{image}'. "
                "Crunchy Data variants are unverified and unsupported. Only official 'postgres' distribution images are supported."
            )
        if "postgres" in repo:
            return (
                f"Unsupported configuration: Service '{service_name}' uses custom or unofficial PostgreSQL image '{image}'. "
                "Only official 'postgres' distribution images are supported."
            )
        return (
            f"Unsupported configuration: Service '{service_name}' uses image '{image}', which is not "
            "recognized as the official PostgreSQL distribution image ('postgres')."
        )

    return None


class ValidatedConfig(NamedTuple):
    raw_compose: dict[str, Any]
    app_port: int


def _get_managed_environments_dir(repo: Path) -> Path:
    return repo / ".isolation-check" / "environments"


def is_managed_environment_recorded(repo: Path, name: str) -> bool:
    env_dir = _get_managed_environments_dir(repo)
    rec_file = env_dir / f"{name}.json"
    return rec_file.is_file()


def _resolve_compose_file_path(repo: Path, compose_ref: str) -> Path:
    p = Path(compose_ref)
    if p.is_absolute():
        resolved = p
    else:
        resolved = (repo / p).resolve()

    if not resolved.is_file():
        raise ValidationError(f"Compose file does not exist: {compose_ref} (resolved: {resolved})")
    return resolved


def collect_raw_compose_config(repo: Path, compose_files: list[str]) -> dict[str, Any]:
    """Runs docker compose config on the specified Compose files to collect canonical JSON."""
    resolved_paths = [_resolve_compose_file_path(repo, f) for f in compose_files]

    cmd = ["docker", "compose"]
    for path in resolved_paths:
        cmd.extend(["-f", str(path)])
    cmd.extend(["config", "--format", "json"])

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            env=_build_safe_env(),
            timeout=30.0,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValidationError("Docker CLI ('docker') was not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("Compose configuration collection timed out after 30 seconds.") from exc

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"Exited with code {proc.returncode}"
        raise ValidationError(f"Docker Compose failed to validate configuration files: {err}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Failed to parse Compose JSON output: {exc}") from exc

    if not isinstance(data, dict):
        raise ValidationError(f"Unexpected Compose output format: expected dictionary, got {type(data).__name__}")
    return data


def extract_candidate_container_ports(service_cfg: dict[str, Any]) -> set[int]:
    """Extracts all unique published or exposed container target ports from a service configuration."""
    candidates: set[int] = set()
    raw_ports = service_cfg.get("ports") or []
    if isinstance(raw_ports, list):
        for p in raw_ports:
            if isinstance(p, dict):
                tgt = p.get("target")
                if tgt is not None:
                    try:
                        tgt_int = int(tgt)
                        if 1 <= tgt_int <= 65535:
                            candidates.add(tgt_int)
                    except (ValueError, TypeError):
                        pass
            elif isinstance(p, (str, int)):
                s = str(p).strip()
                if "/" in s:
                    s = s.split("/", 1)[0]
                parts = s.split(":")
                tgt_str = parts[-1].strip()
                try:
                    tgt_int = int(tgt_str)
                    if 1 <= tgt_int <= 65535:
                        candidates.add(tgt_int)
                except (ValueError, TypeError):
                    pass

    raw_expose = service_cfg.get("expose") or []
    if isinstance(raw_expose, list):
        for e in raw_expose:
            s = str(e).strip()
            if "/" in s:
                s = s.split("/", 1)[0]
            try:
                e_int = int(s.strip())
                if 1 <= e_int <= 65535:
                    candidates.add(e_int)
            except (ValueError, TypeError):
                pass

    return candidates


def infer_or_validate_app_port(
    app_cfg: dict[str, Any],
    app_service_name: str,
    explicit_port: int | None,
) -> int:
    """Validates explicit app_port or infers it if exactly one unambiguous port is published/exposed."""
    if explicit_port is not None:
        if not (1 <= explicit_port <= 65535):
            raise ValidationError(
                f"Application container port (app_port) must be an integer between 1 and 65535, got {explicit_port}."
            )
        return explicit_port

    candidates = extract_candidate_container_ports(app_cfg)
    if len(candidates) == 0:
        raise ValidationError(
            f"Application service '{app_service_name}' does not publish or expose any container ports. "
            "Please explicitly configure 'app_port' in isolation-check.toml or with --app-port."
        )
    if len(candidates) > 1:
        sorted_ports = sorted(candidates)
        raise ValidationError(
            f"Application service '{app_service_name}' exposes multiple candidate container ports ({sorted_ports}). "
            "Cannot safely determine the target port without developer intent. "
            "Please explicitly configure 'app_port' in isolation-check.toml or with --app-port."
        )

    return next(iter(candidates))


def validate_spec_and_config(spec: CreateSpec) -> ValidatedConfig:
    """Validates the create specification, git state, and Compose configuration.

    Refuses existing paths, branches, or managed environments.
    Detects and refuses unsupported arrangements:
    - Custom database entrypoints
    - External storage drivers or external volumes
    - Fixed container names
    - Shared network arrangements
    - Unrecognized database service images
    - Ambiguous or missing application ports

    Returns ValidatedConfig(raw_compose, app_port) if valid.
    """
    # 0. Verify Docker Compose capabilities
    try:
        verify_compose_capabilities()
    except ComposeCapabilityError as exc:
        raise ValidationError(str(exc)) from exc

    # 1. Validate Git repository
    if not is_git_repository(spec.repo):
        raise ValidationError(f"Source path is not a valid Git repository: {spec.repo}")

    # 2. Check base revision
    resolve_commit_hash(spec.repo, spec.base_ref)

    # 3. Refuse existing destination worktree path
    if spec.worktree_path.exists():
        raise ValidationError(
            f"Destination worktree path already exists: '{spec.worktree_path}'. "
            "Cannot create worktree in an existing directory."
        )

    # 4. Refuse existing branch
    if branch_exists(spec.repo, spec.branch):
        raise ValidationError(
            f"Branch '{spec.branch}' already exists in repository {spec.repo}. "
            "Choose a different environment name or remove the conflicting branch."
        )

    # 5. Refuse existing managed environment record
    if is_managed_environment_recorded(spec.repo, spec.name):
        rec_file = _get_managed_environments_dir(spec.repo) / f"{spec.name}.json"
        raise ValidationError(
            f"A managed environment record for '{spec.name}' already exists at {rec_file}. "
            "Remove or clean up the existing environment record before recreating."
        )

    # 6. Validate Compose configuration files and collect canonical model
    raw_compose = collect_raw_compose_config(spec.repo, spec.compose_files)

    services = raw_compose.get("services") or {}
    if not isinstance(services, dict):
        raise ValidationError("Compose configuration contains no valid 'services' section.")

    # 7. Check application service
    if spec.app_service not in services:
        avail = ", ".join(sorted(services.keys())) or "none"
        raise ValidationError(
            f"Configured application service '{spec.app_service}' was not found in Compose services. "
            f"Available services: {avail}."
        )

    # 8. Check PostgreSQL service
    if spec.db_service not in services:
        avail = ", ".join(sorted(services.keys())) or "none"
        raise ValidationError(
            f"Configured PostgreSQL service '{spec.db_service}' was not found in Compose services. "
            f"Available services: {avail}."
        )

    db_cfg = services[spec.db_service]
    db_err = validate_postgres_service_definition(spec.db_service, db_cfg)
    if db_err:
        raise ValidationError(db_err)

    # 11. Refuse external storage drivers / external volumes
    top_volumes = raw_compose.get("volumes") or {}
    if isinstance(top_volumes, dict):
        for vol_key, vol_meta in top_volumes.items():
            if isinstance(vol_meta, dict):
                if vol_meta.get("external"):
                    raise ValidationError(
                        f"Unsupported configuration: Volume '{vol_key}' is declared as external. "
                        "External volumes cannot be safely isolated between environments."
                    )
                driver = vol_meta.get("driver")
                if driver and driver.lower() not in ("local", "default"):
                    raise ValidationError(
                        f"Unsupported configuration: Volume '{vol_key}' uses custom storage driver '{driver}'. "
                        "Only local volumes are supported for environment isolation."
                    )

    # 12. Refuse fixed container names on any service
    for svc_name, svc_cfg in services.items():
        if isinstance(svc_cfg, dict):
            c_name = svc_cfg.get("container_name")
            if c_name:
                raise ValidationError(
                    f"Unsupported configuration: Service '{svc_name}' specifies fixed container_name '{c_name}'. "
                    "Docker container names are globally unique per host and prevent parallel environments from running."
                )

    # 13. Refuse shared network arrangements
    for svc_name, svc_cfg in services.items():
        if isinstance(svc_cfg, dict):
            net_mode = str(svc_cfg.get("network_mode") or "").strip()
            if net_mode == "host":
                raise ValidationError(
                    f"Unsupported configuration: Service '{svc_name}' uses 'network_mode: host'. "
                    "Host network mode bypasses container isolation."
                )
            if net_mode.startswith("service:") or net_mode.startswith("container:"):
                raise ValidationError(
                    f"Unsupported configuration: Service '{svc_name}' uses shared network mode '{net_mode}'."
                )

    top_networks = raw_compose.get("networks") or {}
    if isinstance(top_networks, dict):
        for net_name, net_meta in top_networks.items():
            if isinstance(net_meta, dict) and net_meta.get("external"):
                raise ValidationError(
                    f"Unsupported configuration: Network '{net_name}' is declared as external. "
                    "Shared external networks cannot guarantee network isolation."
                )

    # 14. Validate db_env_var syntax
    if not re.match(r"^[A-Z_][A-Z0-9_]*$", spec.db_env_var, re.IGNORECASE):
        raise ValidationError(
            f"Invalid database environment variable name: '{spec.db_env_var}'. Must be a valid identifier."
        )
    # 15. Infer or validate application container port (app_port)
    app_cfg = services[spec.app_service]
    resolved_app_port = infer_or_validate_app_port(app_cfg, spec.app_service, spec.app_port)

    return ValidatedConfig(raw_compose=raw_compose, app_port=resolved_app_port)
