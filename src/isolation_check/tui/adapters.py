"""Adapters connecting the TUI to existing isolation-check services."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.request

from packaging.version import InvalidVersion, Version, parse as parse_version

from isolation_check.collect.compose import (
    ComposeCollectorError,
    _build_safe_env,
    collect_compose_config,
    resolve_compose_target,
)
from isolation_check.create.generator import GeneratedEnvironmentPlan
from isolation_check.create.manager import (
    CreateResult,
    DryRunResult,
    perform_create,
    perform_dry_run,
)
from isolation_check.create.spec import CreateSpec, SpecError
from isolation_check.create.validate import (
    SUPPORTED_POSTGRES_REPOSITORIES,
    extract_candidate_container_ports,
    is_supported_postgres_image,
    validate_postgres_service_definition,
)
from isolation_check.models import EngineScope, Finding, HostScope, Report
from isolation_check.normalize.compose import normalize_compose_model
from isolation_check.report import build_report
from isolation_check.rules.ports import check_ports
from isolation_check.rules.volumes import check_volumes
from isolation_check.runtime.capabilities import ComposeCapabilityError, verify_compose_capabilities
from isolation_check.runtime.concurrency import atomic_write_text
from isolation_check.runtime.lifecycle import (
    EnvironmentStatusResult,
    LifecycleActionResult,
    query_environment_status,
    start_environment,
    stop_environment,
)
from isolation_check.runtime.validation import OwnershipError, _resolve_repo_root
from isolation_check.version import get_package_version


# ---------------------------------------------------------------------------
# Prerequisites Check Adapter
# ---------------------------------------------------------------------------

@dataclass
class PrerequisiteResult:
    key: str
    title: str
    status: str  # "PASS", "WARN", "FAIL", "INFO"
    current: str
    required: str
    guidance: str
    details: str = ""


def check_all_prerequisites() -> list[PrerequisiteResult]:
    """Runs read-only, bounded prerequisite checks across the environment."""
    results: list[PrerequisiteResult] = []

    # 1. Python version check
    py_ver = sys.version_info
    py_str = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    if py_ver >= (3, 11):
        results.append(
            PrerequisiteResult(
                key="python",
                title="Python Runtime",
                status="PASS",
                current=f"Python {py_str}",
                required=">= 3.11",
                guidance="Python runtime meets project requirements.",
            )
        )
    else:
        results.append(
            PrerequisiteResult(
                key="python",
                title="Python Runtime",
                status="FAIL",
                current=f"Python {py_str}",
                required=">= 3.11",
                guidance="Upgrade Python to 3.11 or newer to run isolation-check.",
            )
        )

    # 2. Git CLI availability
    git_path = shutil.which("git")
    if not git_path:
        results.append(
            PrerequisiteResult(
                key="git",
                title="Git CLI",
                status="FAIL",
                current="Not found on PATH",
                required="Git 2.20+",
                guidance="Install Git (https://git-scm.com/) and ensure it is available on your PATH.",
            )
        )
    else:
        try:
            proc = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                env=_build_safe_env(),
                timeout=5.0,
                check=False,
            )
            git_ver = proc.stdout.strip() if proc.returncode == 0 else "git (unknown version)"
            results.append(
                PrerequisiteResult(
                    key="git",
                    title="Git CLI",
                    status="PASS",
                    current=git_ver,
                    required="Git available on PATH",
                    guidance="Git CLI is installed and responsive.",
                )
            )
        except Exception as exc:
            results.append(
                PrerequisiteResult(
                    key="git",
                    title="Git CLI",
                    status="FAIL",
                    current="Execution failed",
                    required="Git available on PATH",
                    guidance=f"Git failed to execute: {exc}",
                )
            )

    # 3. Docker CLI availability
    docker_path = shutil.which("docker")
    if not docker_path:
        results.append(
            PrerequisiteResult(
                key="docker_cli",
                title="Docker CLI",
                status="FAIL",
                current="Not found on PATH",
                required="Docker CLI available",
                guidance="Install Docker Desktop or Docker Engine (https://docs.docker.com/engine/install/) and add to PATH.",
            )
        )
    else:
        try:
            proc = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                env=_build_safe_env(),
                timeout=5.0,
                check=False,
            )
            dock_ver = proc.stdout.strip() if proc.returncode == 0 else "Docker (unknown version)"
            results.append(
                PrerequisiteResult(
                    key="docker_cli",
                    title="Docker CLI",
                    status="PASS",
                    current=dock_ver,
                    required="Docker CLI on PATH",
                    guidance="Docker CLI is available.",
                )
            )
        except Exception as exc:
            results.append(
                PrerequisiteResult(
                    key="docker_cli",
                    title="Docker CLI",
                    status="FAIL",
                    current="Execution failed",
                    required="Docker CLI on PATH",
                    guidance=f"Docker CLI failed to execute: {exc}",
                )
            )

    # 4. Docker Daemon Reachability
    if docker_path:
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                env=_build_safe_env(),
                timeout=6.0,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                daemon_ver = proc.stdout.strip()
                results.append(
                    PrerequisiteResult(
                        key="docker_daemon",
                        title="Docker Daemon",
                        status="PASS",
                        current=f"Engine v{daemon_ver}",
                        required="Docker engine running",
                        guidance="Docker daemon is active and responding to commands.",
                    )
                )
            else:
                err_text = proc.stderr.strip() or "Daemon not responding"
                results.append(
                    PrerequisiteResult(
                        key="docker_daemon",
                        title="Docker Daemon",
                        status="FAIL",
                        current="Not reachable",
                        required="Docker engine running",
                        guidance="Ensure Docker Desktop or the dockerd service is started and your user has docker permissions.",
                        details=err_text,
                    )
                )
        except subprocess.TimeoutExpired:
            results.append(
                PrerequisiteResult(
                    key="docker_daemon",
                    title="Docker Daemon",
                    status="FAIL",
                    current="Timed out",
                    required="Docker engine running",
                    guidance="Docker daemon probe timed out. Verify your Docker service is running.",
                )
            )
        except Exception as exc:
            results.append(
                PrerequisiteResult(
                    key="docker_daemon",
                    title="Docker Daemon",
                    status="FAIL",
                    current="Error probing daemon",
                    required="Docker engine running",
                    guidance=f"Probe failed: {exc}",
                )
            )
    else:
        results.append(
            PrerequisiteResult(
                key="docker_daemon",
                title="Docker Daemon",
                status="FAIL",
                current="Skipped (no CLI)",
                required="Docker engine running",
                guidance="Install Docker CLI first.",
            )
        )

    # 5. Docker Compose Version
    if docker_path:
        try:
            proc = subprocess.run(
                ["docker", "compose", "version", "--short"],
                capture_output=True,
                text=True,
                env=_build_safe_env(),
                timeout=5.0,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                comp_ver_str = proc.stdout.strip().lstrip("v")
                try:
                    comp_ver = parse_version(comp_ver_str)
                    if comp_ver >= parse_version("2.20.0"):
                        results.append(
                            PrerequisiteResult(
                                key="compose_version",
                                title="Docker Compose Version",
                                status="PASS",
                                current=f"v{comp_ver_str}",
                                required=">= v2.20.0",
                                guidance="Docker Compose version meets requirement.",
                            )
                        )
                    else:
                        results.append(
                            PrerequisiteResult(
                                key="compose_version",
                                title="Docker Compose Version",
                                status="FAIL",
                                current=f"v{comp_ver_str}",
                                required=">= v2.20.0",
                                guidance="Upgrade Docker Compose to v2.20 or newer to support sequence override tags (!override/!reset).",
                            )
                        )
                except InvalidVersion:
                    results.append(
                        PrerequisiteResult(
                            key="compose_version",
                            title="Docker Compose Version",
                            status="WARN",
                            current=comp_ver_str,
                            required=">= v2.20.0",
                            guidance="Could not parse Compose version string with PEP 440; verify Compose is v2.20+.",
                        )
                    )
            else:
                results.append(
                    PrerequisiteResult(
                        key="compose_version",
                        title="Docker Compose Version",
                        status="FAIL",
                        current="docker compose failed",
                        required="Docker Compose v2.20+",
                        guidance="Ensure Docker Compose plugin is installed (e.g. docker-compose-plugin).",
                    )
                )
        except Exception as exc:
            results.append(
                PrerequisiteResult(
                    key="compose_version",
                    title="Docker Compose Version",
                    status="FAIL",
                    current="Execution failed",
                    required="Docker Compose v2.20+",
                    guidance=f"Failed to query Docker Compose version: {exc}",
                )
            )
    else:
        results.append(
            PrerequisiteResult(
                key="compose_version",
                title="Docker Compose Version",
                status="FAIL",
                current="Skipped (no CLI)",
                required="Docker Compose v2.20+",
                guidance="Install Docker CLI first.",
            )
        )

    # 6. Compose Override/Reset Capability Check
    try:
        verify_compose_capabilities()
        results.append(
            PrerequisiteResult(
                key="compose_capabilities",
                title="Compose Sequence Tags",
                status="PASS",
                current="Verified (!override / !reset supported)",
                required="YAML sequence overrides supported",
                guidance="Compose syntax capability verified.",
            )
        )
    except ComposeCapabilityError as exc:
        results.append(
            PrerequisiteResult(
                key="compose_capabilities",
                title="Compose Sequence Tags",
                status="FAIL",
                current="Unsupported or probe failed",
                required="!override / !reset YAML tag support",
                guidance=str(exc),
            )
        )

    # 7. Supported Host Scope Note
    os_name = platform.system()
    if os_name == "Linux":
        results.append(
            PrerequisiteResult(
                key="platform",
                title="Target Operating System",
                status="PASS",
                current=f"Linux ({platform.release()})",
                required="Linux host/engine",
                guidance="Running on primary supported target platform.",
            )
        )
    else:
        results.append(
            PrerequisiteResult(
                key="platform",
                title="Target Operating System",
                status="INFO",
                current=f"{os_name} ({platform.release()})",
                required="Linux Docker engine",
                guidance=(
                    f"isolation-check TUI runs on {os_name}, but the primary verified target "
                    "for container lifecycle execution in this release is Linux Docker Compose. "
                    "Execution on native Windows/macOS is experimental."
                ),
            )
        )

    return results


# ---------------------------------------------------------------------------
# Updates Check Adapter
# ---------------------------------------------------------------------------

@dataclass
class UpdateCheckResult:
    current_version: str
    latest_version: str | None = None
    latest_stable: str | None = None
    is_outdated: bool = False
    is_prerelease: bool = False
    is_offline: bool = False
    error: str | None = None
    project_url: str = "https://pypi.org/project/isolation-check/"
    upgrade_commands: dict[str, str] = field(default_factory=dict)


def fetch_pypi_update(timeout: float = 8.0) -> UpdateCheckResult:
    """Queries official PyPI metadata over HTTPS with bounded read and strict timeout."""
    cur_ver_str = get_package_version()
    try:
        cur_ver = parse_version(cur_ver_str)
    except InvalidVersion:
        cur_ver = None

    url = "https://pypi.org/pypi/isolation-check/json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"isolation-check-tui/{cur_ver_str}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Bounded read: at most 256KB
            raw_bytes = resp.read(262144)
            data = json.loads(raw_bytes.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return UpdateCheckResult(
                current_version=cur_ver_str,
                error="Package 'isolation-check' was not found on PyPI (it may not be published yet).",
                project_url="https://github.com/rxmna8502/isolation-check",
            )
        return UpdateCheckResult(
            current_version=cur_ver_str,
            error=f"PyPI returned HTTP error: {exc.code} {exc.reason}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return UpdateCheckResult(
            current_version=cur_ver_str,
            is_offline=True,
            error=f"Network unreachable or request timed out: {exc}",
        )
    except Exception as exc:
        return UpdateCheckResult(
            current_version=cur_ver_str,
            error=f"Failed to parse PyPI response: {exc}",
        )

    info = data.get("info", {})
    pypi_latest_str = info.get("version", "")
    releases = data.get("releases", {})

    parsed_versions: list[Version] = []
    for ver_key in releases.keys():
        try:
            parsed_versions.append(parse_version(ver_key))
        except InvalidVersion:
            continue

    if not parsed_versions and pypi_latest_str:
        try:
            parsed_versions.append(parse_version(pypi_latest_str))
        except InvalidVersion:
            pass

    if not parsed_versions:
        return UpdateCheckResult(
            current_version=cur_ver_str,
            error="No valid versions found in PyPI release metadata.",
        )

    parsed_versions.sort()
    latest_any = parsed_versions[-1]
    stables = [v for v in parsed_versions if not v.is_prerelease and not v.is_devrelease]
    latest_stable = stables[-1] if stables else None

    is_outdated = False
    if cur_ver:
        if cur_ver.is_prerelease and latest_any > cur_ver:
            is_outdated = True
        elif not cur_ver.is_prerelease and latest_stable and latest_stable > cur_ver:
            is_outdated = True
        elif latest_any > cur_ver:
            is_outdated = True

    upgrade_cmds = {
        "pip": f'pip install --upgrade "isolation-check=={latest_any}"' if is_outdated else 'pip install --upgrade "isolation-check"',
        "pipx": 'pipx upgrade "isolation-check"',
        "uv": 'uv tool upgrade "isolation-check"',
    }

    return UpdateCheckResult(
        current_version=cur_ver_str,
        latest_version=str(latest_any),
        latest_stable=str(latest_stable) if latest_stable else None,
        is_outdated=is_outdated,
        is_prerelease=cur_ver.is_prerelease if cur_ver else False,
        project_url=info.get("project_url") or "https://pypi.org/project/isolation-check/",
        upgrade_commands=upgrade_cmds,
    )


# ---------------------------------------------------------------------------
# Repository & Compose Inspection Adapters
# ---------------------------------------------------------------------------

def resolve_local_repo(hint: str | Path | None = None) -> tuple[Path | None, str | None]:
    """Finds the main Git repository root."""
    try:
        repo_root = _resolve_repo_root(Path(hint) if hint else None)
        return repo_root, None
    except OwnershipError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"Failed to resolve Git repository: {exc}"


def find_compose_candidates(repo_path: Path) -> list[str]:
    """Finds standard Docker Compose files in the repository."""
    candidates = [
        "docker-compose.yaml",
        "docker-compose.yml",
        "compose.yaml",
        "compose.yml",
    ]
    found: list[str] = []
    for c in candidates:
        if (repo_path / c).is_file():
            found.append(c)
    return found


@dataclass
class ServiceSummary:
    name: str
    image: str = ""
    build_source: str = ""
    ports: list[int] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    container_name: str | None = None
    entrypoint: Any = None
    is_postgres_candidate: bool = False
    postgres_evidence: str = ""
    postgres_unsupported_reason: str | None = None
    is_infrastructure: bool = False
    infra_type: str = ""
    app_option_label: str = ""
    db_option_label: str = ""
    detected_db_env_vars: list[str] = field(default_factory=list)


def classify_service_summary(name: str, svc_def: dict[str, Any]) -> ServiceSummary:
    """Classifies a Compose service using shared backend validation rules and infrastructure heuristics."""
    image = str(svc_def.get("image") or "").strip()
    build = svc_def.get("build")
    build_str = ""
    if isinstance(build, str):
        build_str = f"build: {build}"
    elif isinstance(build, dict):
        ctx = build.get("context") or "."
        build_str = f"build: {ctx}"

    ports = sorted(extract_candidate_container_ports(svc_def))

    # Extract environment variables
    env_map: dict[str, str] = {}
    raw_env = svc_def.get("environment")
    if isinstance(raw_env, dict):
        for k, v in raw_env.items():
            env_map[str(k)] = str(v)
    elif isinstance(raw_env, list):
        for item in raw_env:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                env_map[k.strip()] = v.strip()

    container_name = svc_def.get("container_name")
    entrypoint = svc_def.get("entrypoint")

    # Validate against backend PostgreSQL rules
    pg_err = validate_postgres_service_definition(name, svc_def)
    is_pg = (pg_err is None)

    name_lower = name.lower()
    img_lower = image.lower()

    is_infra = False
    infra_type = ""
    pg_unsupported_reason: str | None = None

    if is_pg:
        is_infra = True
        infra_type = f"PostgreSQL: {image}"
    else:
        # Classify infrastructure/datastore kinds and record clear unsupported reasons
        if "postgis" in img_lower or "postgis" in name_lower:
            pg_unsupported_reason = pg_err or f"PostGIS image '{image}' is an unverified PostgreSQL variant and is not supported."
            is_infra = True
            infra_type = f"Database (PostGIS): {image or name}"
        elif "timescale" in img_lower or "timescale" in name_lower:
            pg_unsupported_reason = pg_err or f"TimescaleDB image '{image}' is an unverified PostgreSQL variant and is not supported."
            is_infra = True
            infra_type = f"Database (TimescaleDB): {image or name}"
        elif "crunchy" in img_lower or "crunchy" in name_lower:
            pg_unsupported_reason = pg_err or f"Crunchy Data image '{image}' is an unverified PostgreSQL variant and is not supported."
            is_infra = True
            infra_type = f"Database (Crunchy): {image or name}"
        elif "postgres" in img_lower or "postgres" in name_lower:
            pg_unsupported_reason = pg_err or "Image or configuration is not the supported official PostgreSQL distribution ('postgres')."
            is_infra = True
            infra_type = f"Invalid/Custom PostgreSQL: {image or name}"
        elif any(k in img_lower or k in name_lower for k in ("mysql", "mariadb")):
            is_infra = True
            infra_type = f"Database: {image or 'mysql'}"
            pg_unsupported_reason = f"MySQL/MariaDB is not supported (only PostgreSQL is supported)."
        elif any(k in img_lower or k in name_lower for k in ("mongo", "mongodb")):
            is_infra = True
            infra_type = f"Database: {image or 'mongo'}"
            pg_unsupported_reason = f"MongoDB is a document store and is not supported."
        elif any(k in img_lower or k in name_lower for k in ("redis", "memcached", "dragonfly", "valkey")):
            is_infra = True
            infra_type = f"Cache: {image or 'redis'}"
            pg_unsupported_reason = f"In-memory key-value cache, not a relational PostgreSQL database."
        elif any(k in img_lower or k in name_lower for k in ("kafka", "zookeeper")):
            is_infra = True
            infra_type = f"Message Broker: {image or 'kafka'}"
        elif any(k in img_lower or k in name_lower for k in ("rabbitmq", "nats", "activemq", "pulsar")):
            is_infra = True
            infra_type = f"Message Queue: {image or 'queue'}"
        elif any(k in img_lower or k in name_lower for k in ("mailpit", "mailhog", "mailcatcher", "smtp")):
            is_infra = True
            infra_type = f"Mail Utility: {image or 'mailpit'}"
        elif any(k in img_lower or k in name_lower for k in ("elasticsearch", "opensearch", "solr", "kibana", "grafana", "prometheus", "jaeger", "zipkin")):
            is_infra = True
            infra_type = f"Observability/Search: {image or 'tool'}"
        elif any(k in img_lower or k in name_lower for k in ("nginx", "traefik", "caddy", "haproxy", "envoy")):
            is_infra = True
            infra_type = f"Reverse Proxy: {image or 'proxy'}"
        elif any(k in img_lower or k in name_lower for k in ("localstack", "minio", "azurite", "dynamodb")):
            is_infra = True
            infra_type = f"Emulator: {image or 'emulator'}"
        elif any(k in img_lower or k in name_lower for k in ("db", "database", "datastore", "sql")):
            is_infra = True
            infra_type = f"Unrecognized Database: {image or name}"
            pg_unsupported_reason = pg_err or f"Image '{image}' is not recognized as a supported PostgreSQL distribution (requires postgres, postgis, timescaledb, or crunchy)."
        elif pg_err and ("entrypoint" in pg_err or "container_name" in pg_err):
            is_infra = True
            infra_type = f"Invalid Service: {image or name}"
            pg_unsupported_reason = pg_err

    if not is_pg and not pg_unsupported_reason:
        pg_unsupported_reason = pg_err

    # Look for declared DB environment variables in the service
    detected_db_vars: list[str] = []
    for var_name in env_map.keys():
        var_upper = var_name.upper()
        if any(token in var_upper for token in ("DATABASE", "POSTGRES", "PGHOST", "PGDATABASE", "DB_URL", "DB_HOST", "DATASOURCE")):
            detected_db_vars.append(var_name)

    # Ports display string
    if len(ports) == 1:
        ports_desc = f"target port: {ports[0]}"
    elif ports:
        ports_desc = f"ports: {', '.join(map(str, ports))}"
    else:
        ports_desc = "no declared ports"

    # Application selector display label
    if is_infra:
        app_option_label = f"{name} [Infrastructure: {image or infra_type} | {ports_desc}]"
    else:
        source_label = build_str if build_str else (f"image: {image}" if image else "source")
        app_option_label = f"{name} ({source_label} | {ports_desc})"

    # PostgreSQL selector display label
    db_option_label = f"{name} ({image})" if image else name
    postgres_evidence = f"Supported PostgreSQL distribution: {image}" if is_pg else ""

    return ServiceSummary(
        name=name,
        image=image,
        build_source=build_str,
        ports=ports,
        environment=env_map,
        container_name=container_name,
        entrypoint=entrypoint,
        is_postgres_candidate=is_pg,
        postgres_evidence=postgres_evidence,
        postgres_unsupported_reason=pg_unsupported_reason,
        is_infrastructure=is_infra,
        infra_type=infra_type,
        app_option_label=app_option_label,
        db_option_label=db_option_label,
        detected_db_env_vars=detected_db_vars,
    )


def inspect_compose_definition(
    repo_path: Path,
    compose_files: list[str],
) -> tuple[list[str], dict[str, list[int]], list[ServiceSummary], str | None]:
    """Inspects declared Compose services and candidate ports.

    Returns:
        (services_list, ports_by_service_dict, service_summaries, error_message)
    """
    if not compose_files:
        return [], {}, [], "No compose files specified."

    primary = repo_path / compose_files[0]
    if not primary.is_file():
        return [], {}, [], f"Primary Compose file does not exist: {primary}"

    raw_config: dict[str, Any] | None = None
    collect_err: str | None = None

    try:
        raw_config = collect_compose_config(repo_path, primary)
    except ComposeCollectorError as exc:
        collect_err = str(exc)
    except Exception as exc:
        collect_err = f"Failed to collect compose config: {exc}"

    if raw_config is None or not isinstance(raw_config.get("services"), dict):
        # Fallback to local YAML parsing if Docker CLI/daemon failed
        try:
            import yaml
            parsed = yaml.safe_load(primary.read_text(encoding="utf-8"))
            if isinstance(parsed, dict) and isinstance(parsed.get("services"), dict):
                raw_config = parsed
        except Exception:
            pass

    if raw_config is None or not isinstance(raw_config.get("services"), dict):
        return [], {}, [], collect_err or "Invalid Compose config: 'services' must be a mapping."

    services_dict = raw_config.get("services", {})
    services = list(services_dict.keys())
    ports_map: dict[str, list[int]] = {}
    summaries: list[ServiceSummary] = []

    for svc_name, svc_def in services_dict.items():
        if not isinstance(svc_def, dict):
            continue
        summary = classify_service_summary(svc_name, svc_def)
        summaries.append(summary)
        ports_map[svc_name] = summary.ports

    return services, ports_map, summaries, None


def validate_selected_service_pair(
    app_service: str,
    db_service: str,
    summaries: list[ServiceSummary],
) -> tuple[bool, str, str | None]:
    """Validates the selected application and PostgreSQL database service pair.

    Returns:
        (is_valid, message, proposed_db_env_var)
    """
    if not app_service or not db_service:
        return False, "Both Application service and PostgreSQL service must be selected.", None

    if app_service == db_service:
        return False, "Application service and Database service cannot be the same container.", None

    by_name = {s.name: s for s in summaries}
    if db_service not in by_name:
        return False, f"Selected PostgreSQL service '{db_service}' was not found in Compose services.", None
    if app_service not in by_name:
        return False, f"Selected Application service '{app_service}' was not found in Compose services.", None

    db_summary = by_name[db_service]
    app_summary = by_name[app_service]

    # Validate db_service with backend rules
    if not db_summary.is_postgres_candidate:
        reason = db_summary.postgres_unsupported_reason or "Not a recognized PostgreSQL service."
        return False, f"Service '{db_service}' is not a valid PostgreSQL service: {reason}", None

    # Check if app_service specifies fixed container_name (Rule 12)
    if app_summary.container_name:
        return (
            False,
            f"Application service '{app_service}' specifies fixed container_name '{app_summary.container_name}', "
            "which prevents parallel environment isolation.",
            None,
        )

    # Validate connection configuration on application service
    if app_summary.is_infrastructure and not app_summary.detected_db_env_vars:
        return (
            False,
            f"Selected service '{app_service}' is an infrastructure component ({app_summary.infra_type}) "
            "and does not declare any supported PostgreSQL connection configuration. "
            "Please select an application service (web, api, worker) that connects to PostgreSQL.",
            None,
        )

    if app_summary.detected_db_env_vars:
        db_env_var = app_summary.detected_db_env_vars[0]
        msg = (
            f"[bold green]✓ Validated Service Mapping:[/bold green] Application '{app_service}' "
            f"mapped to PostgreSQL '{db_service}' ({db_summary.image}). "
            f"Detected connection variable: '{db_env_var}'."
        )
    else:
        db_env_var = "DATABASE_URL"
        msg = (
            f"[bold green]✓ Validated Service Mapping:[/bold green] Application '{app_service}' "
            f"mapped to PostgreSQL '{db_service}' ({db_summary.image}). "
            f"Default connection variable: '{db_env_var}'."
        )

    return True, msg, db_env_var


def load_existing_config_toml(repo_path: Path) -> tuple[str | None, dict[str, Any] | None]:
    """Reads existing isolation-check.toml if present."""
    cfg_file = repo_path / "isolation-check.toml"
    if not cfg_file.is_file():
        return None, None

    try:
        text = cfg_file.read_text(encoding="utf-8")
        # Python 3.11+ tomllib
        import tomllib
        parsed = tomllib.loads(text)
        return text, parsed
    except Exception:
        try:
            return cfg_file.read_text(encoding="utf-8"), None
        except Exception:
            return None, None


def render_config_toml(
    app_service: str,
    db_service: str,
    db_env_var: str,
    app_port: int | None,
    compose_files: list[str],
    publish_db: bool = False,
) -> str:
    """Renders a proposed isolation-check.toml."""
    lines = [
        "# isolation-check configuration",
        "[create]",
    ]
    files_repr = json.dumps(compose_files)
    lines.append(f"compose_files = {files_repr}")
    lines.append(f'app_service = "{app_service}"')
    lines.append(f'db_service = "{db_service}"')
    lines.append(f'db_env_var = "{db_env_var}"')
    if app_port is not None:
        lines.append(f"app_port = {app_port}")
    lines.append(f"publish_db = {'true' if publish_db else 'false'}")
    lines.append("")
    return "\n".join(lines)


def save_config_toml(repo_path: Path, content: str) -> tuple[bool, str | None]:
    """Writes isolation-check.toml atomically.

    Guarantees that readers never observe partially written or corrupted files,
    and preserves any existing file if writing fails.

    Args:
        repo_path: Directory path to the repository root.
        content: Serialized TOML configuration text.

    Returns:
        (True, None) on success.
        (False, error_message) on failure.
    """
    if not content or not content.strip():
        return False, "Configuration content cannot be empty."

    try:
        repo_dir = Path(repo_path).resolve()
        if not repo_dir.is_dir():
            return False, f"Target directory does not exist or is not a directory: {repo_dir}"
        target = repo_dir / "isolation-check.toml"
        atomic_write_text(target, content)
        return True, None
    except PermissionError as exc:
        return False, f"Permission denied writing 'isolation-check.toml': {exc.strerror or exc}"
    except OSError as exc:
        return False, f"Filesystem error writing 'isolation-check.toml': {exc.strerror or exc}"
    except Exception as exc:
        return False, f"Failed to save 'isolation-check.toml': {exc}"


# ---------------------------------------------------------------------------
# Managed Environment Dashboard Adapter
# ---------------------------------------------------------------------------

def list_registered_environments(repo_path: Path) -> list[dict[str, Any]]:
    """Lists all environments registered with the repository."""
    envs_dir = repo_path / ".isolation-check" / "environments"
    if not envs_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for p in sorted(envs_dir.glob("*.json")):
        if p.name.startswith("."):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "name" in data:
                results.append(data)
        except Exception:
            pass
    return results


def inspect_environment_live(
    env_name: str,
    repo_path: Path | None = None,
) -> EnvironmentStatusResult:
    """Queries live runtime state and Docker health for an environment."""
    return query_environment_status(env_name, repo_hint=repo_path)


def start_env(
    env_name: str,
    repo_path: Path | None = None,
) -> LifecycleActionResult:
    """Starts the managed environment."""
    return start_environment(env_name, repo_hint=repo_path)


def stop_env(
    env_name: str,
    repo_path: Path | None = None,
) -> LifecycleActionResult:
    """Stops the managed environment non-destructively."""
    return stop_environment(env_name, repo_hint=repo_path, destructive=False)


# ---------------------------------------------------------------------------
# Environment Creation Adapter
# ---------------------------------------------------------------------------

def dry_run_create(spec: CreateSpec) -> DryRunResult:
    """Executes dry run validation and returns the proposed plan."""
    return perform_dry_run(spec)


def do_create(spec: CreateSpec) -> CreateResult:
    """Executes creation of the environment."""
    return perform_create(spec)


# ---------------------------------------------------------------------------
# Collision Checking Adapter
# ---------------------------------------------------------------------------

def run_collision_check(
    left_path: str,
    right_path: str,
    strict: bool = False,
) -> tuple[Report | None, list[Finding], str | None]:
    """Runs volume and port collision audits on two environment paths."""
    from isolation_check.cli import _load_env_config

    try:
        left_raw, left_label, left_root = _load_env_config(left_path)
        right_raw, right_label, right_root = _load_env_config(right_path)
    except ComposeCollectorError as exc:
        return None, [], str(exc)
    except Exception as exc:
        return None, [], f"Unexpected error loading compose configurations: {exc}"

    engine_scope = EngineScope(scope_id="local-docker-engine", basis="shared engine assumption")
    host_scope = HostScope(scope_id="local-host", basis="shared host assumption")

    left_env = normalize_compose_model(
        left_raw, left_label, left_root, engine_scope, host_scope
    )
    right_env = normalize_compose_model(
        right_raw, right_label, right_root, engine_scope, host_scope
    )

    findings: list[Finding] = []
    findings.extend(check_volumes(left_env, right_env, include_coverage=False))
    findings.extend(check_ports(left_env, right_env))
    findings.append(
        Finding(
            rule_id="COVERAGE",
            status="unknown",
            severity="info",
            confidence="high",
            basis="partial evaluation",
            message=(
                "Bind mounts, databases, project namespaces, and runtime state were not evaluated. "
                "Exit 0 does not prove complete environment isolation."
            ),
            environments=[left_env.label, right_env.label],
        )
    )

    report = build_report(
        findings=findings,
        environments=[left_env.label, right_env.label],
        engine_scope=engine_scope,
        host_scope=host_scope,
    )

    return report, findings, None
