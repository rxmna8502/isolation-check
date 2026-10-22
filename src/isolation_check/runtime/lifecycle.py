"""Managed runtime lifecycle implementation: up, status, and down."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from isolation_check.collect.compose import _build_safe_env
from isolation_check.runtime.capabilities import ComposeCapabilityError, verify_compose_capabilities
from isolation_check.runtime.concurrency import (
    ConcurrencyLockError,
    acquire_environment_lock,
    atomic_write_text,
)
from isolation_check.runtime.port_allocator import (
    allocate_candidate_loopback_port,
    is_port_binding_conflict,
    release_port_reservation,
)
from isolation_check.runtime.validation import OwnershipError, locate_and_validate_environment
from isolation_check.version import get_package_version


@dataclass
class EnvironmentStatusResult:
    success: bool
    environment: str
    compose_project: str = ""
    worktree_path: str = ""
    status: str = "UNKNOWN"
    health: str = "UNKNOWN"
    app_target_port: int | None = None
    host_port: int | None = None
    url: str | None = None
    ownership_verified: bool = False
    stale_runtime_state: bool = False
    containers: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        if not self.success:
            return {
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "environment": self.environment,
                "error": self.error or "Status check failed",
            }
        return {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "environment": self.environment,
            "compose_project": self.compose_project,
            "worktree_path": self.worktree_path,
            "status": self.status,
            "health": self.health,
            "app_target_port": self.app_target_port,
            "host_port": self.host_port,
            "url": self.url,
            "ownership_verified": self.ownership_verified,
            "stale_runtime_state": self.stale_runtime_state,
            "containers": [
                {
                    "name": c.get("Name"),
                    "service": c.get("Service"),
                    "state": c.get("State"),
                    "health": _evaluate_container_health(c),
                }
                for c in self.containers
            ],
        }


@dataclass
class LifecycleActionResult:
    success: bool
    environment: str
    compose_project: str = ""
    worktree_path: str = ""
    status: str = ""
    health: str = ""
    host_port: int | None = None
    app_port: int | None = None
    url: str | None = None
    already_running: bool = False
    containers: list[dict[str, Any]] = field(default_factory=list)
    volume_preserved: str = ""
    preserved_worktree: str = ""
    message: str = ""
    error: str | None = None
    error_kind: str = ""  # "ownership", "capability", "concurrency", "security", "runtime"
    exit_code: int = 0
    app_service: str = ""
    db_service: str = ""
    publish_db: bool = False
    dedicated_volume: str = ""

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "success": self.success,
            "environment": self.environment,
        }
        if not self.success:
            res["error"] = self.error or "Operation failed"
            if self.compose_project:
                res["compose_project"] = self.compose_project
            if self.preserved_worktree:
                res["preserved_worktree"] = self.preserved_worktree
            return res

        if self.compose_project:
            res["compose_project"] = self.compose_project
        if self.worktree_path:
            res["worktree_path"] = self.worktree_path
        if self.status:
            res["status"] = self.status
        if self.health:
            res["health"] = self.health
        if self.app_port is not None:
            res["app_port"] = self.app_port
        if self.host_port is not None:
            res["host_port"] = self.host_port
        if self.url:
            res["url"] = self.url
        if self.already_running:
            res["already_running"] = True
        if self.volume_preserved:
            res["volume_preserved"] = self.volume_preserved
        if self.message:
            res["message"] = self.message
        if self.containers:
            res["containers"] = [
                {
                    "name": c.get("Name"),
                    "service": c.get("Service"),
                    "state": c.get("State"),
                    "health": _evaluate_container_health(c),
                }
                for c in self.containers
            ]
        return res

MAX_PORT_START_RETRIES = 3
DEFAULT_UP_TIMEOUT = 60.0
DEFAULT_DOWN_TIMEOUT = 60.0
DEFAULT_STATUS_TIMEOUT = 15.0


def _run_compose_cmd(
    cmd: list[str],
    cwd: Path,
    timeout_seconds: float,
    max_bytes: int = 1_048_576,
) -> tuple[int, str, str]:
    """Executes a Docker Compose command using subprocess argument arrays and bounded capture."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_build_safe_env(),
            cwd=str(cwd),
        )
    except FileNotFoundError as exc:
        return 1, "", "The 'docker' executable was not found on PATH. Ensure Docker is installed."
    except Exception as exc:
        return 1, "", f"Failed to spawn Docker Compose subprocess: {exc}"

    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return 1, "", f"Command timed out after {timeout_seconds} seconds."

    stdout = stdout_bytes[:max_bytes].decode("utf-8", errors="replace")
    stderr = stderr_bytes[:max_bytes].decode("utf-8", errors="replace")
    return proc.returncode, stdout, stderr


def _inspect_compose_containers(
    worktree_path: Path,
    project_name: str,
    base_compose_files: list[str],
    override_file: str,
    timeout_seconds: float = DEFAULT_STATUS_TIMEOUT,
) -> list[dict[str, Any]]:
    """Runs `docker compose ps --format json` and returns parsed container objects belonging to the project."""
    cmd = [
        "docker",
        "compose",
        "-p",
        project_name,
        "--project-directory",
        str(worktree_path),
    ]
    for bf in base_compose_files:
        cmd.extend(["-f", str(worktree_path / bf)])
    cmd.extend(["-f", str(worktree_path / override_file)])
    cmd.extend(["ps", "--format", "json"])

    code, stdout, stderr = _run_compose_cmd(cmd, worktree_path, timeout_seconds)
    if code != 0 or not stdout.strip():
        return []

    containers: list[dict[str, Any]] = []
    # Output can be a JSON array or newline-delimited JSON objects
    raw = stdout.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            containers = parsed
        elif isinstance(parsed, dict):
            containers = [parsed]
    except json.JSONDecodeError:
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    c = json.loads(line)
                    if isinstance(c, dict):
                        containers.append(c)
                except Exception:
                    pass

    # Strictly verify container ownership label
    verified: list[dict[str, Any]] = []
    for c in containers:
        labels = c.get("Labels") or {}
        # Labels may be a dict or a comma-separated string
        label_project = ""
        if isinstance(labels, dict):
            label_project = labels.get("com.docker.compose.project", "")
        elif isinstance(labels, str):
            for part in labels.split(","):
                if "com.docker.compose.project=" in part:
                    label_project = part.split("=", 1)[1].strip()
        if not label_project or label_project == project_name:
            verified.append(c)
        else:
            raise OwnershipError(
                f"Observed container '{c.get('Name')}' belongs to project '{label_project}', not expected project '{project_name}'."
            )

    return verified


def _extract_app_published_port(
    containers: list[dict[str, Any]],
    app_service: str,
    app_target_port: int,
) -> tuple[int | None, str | None]:
    """Extracts actual host loopback port and URL from Docker container state for the application service."""
    for c in containers:
        svc = c.get("Service") or ""
        if svc != app_service:
            continue

        # Look at Publishers or Ports
        publishers = c.get("Publishers") or []
        for pub in publishers:
            if isinstance(pub, dict):
                tgt = pub.get("TargetPort")
                pub_port = pub.get("PublishedPort")
                host_ip = pub.get("URL") or pub.get("HostIP") or ""
                if tgt == app_target_port and pub_port:
                    return int(pub_port), f"http://127.0.0.1:{pub_port}"

        # Check legacy / alternative Ports representation
        raw_ports = c.get("Ports") or ""
        if isinstance(raw_ports, str):
            # Example: "127.0.0.1:10001->3000/tcp" or "0.0.0.0:10001->3000/tcp"
            match = re.search(r"127\.0\.0\.1:(\d+)->" + str(app_target_port), raw_ports)
            if match:
                p = int(match.group(1))
                return p, f"http://127.0.0.1:{p}"

    return None, None


def _evaluate_container_health(c: dict[str, Any]) -> str:
    """Evaluates health string distinguishing RUNNING from HEALTHY. If no health check, returns UNKNOWN."""
    # Docker ps JSON often includes Health field
    health = c.get("Health") or ""
    if health:
        return str(health).upper()

    # Sometimes status includes "(healthy)" or "(unhealthy)"
    status_str = str(c.get("Status") or "").lower()
    if "(healthy)" in status_str:
        return "HEALTHY"
    if "(unhealthy)" in status_str:
        return "UNHEALTHY"
    if "(health: starting)" in status_str:
        return "STARTING"

    # No health check configured
    return "UNKNOWN"


def _evaluate_overall_status(containers: list[dict[str, Any]]) -> tuple[str, str]:
    """Returns (overall_state, overall_health).

    States: RUNNING, PARTIAL, STOPPED, UNKNOWN.
    Health: HEALTHY, UNHEALTHY, UNKNOWN.
    """
    if not containers:
        return "STOPPED", "UNKNOWN"

    running_count = 0
    health_states: list[str] = []

    for c in containers:
        state = str(c.get("State") or "").lower()
        if state == "running":
            running_count += 1
        h = _evaluate_container_health(c)
        health_states.append(h)

    if running_count == len(containers):
        overall_state = "RUNNING"
    elif running_count > 0:
        overall_state = "PARTIAL"
    else:
        overall_state = "STOPPED"

    if all(h == "HEALTHY" for h in health_states):
        overall_health = "HEALTHY"
    elif any(h == "UNHEALTHY" for h in health_states):
        overall_health = "UNHEALTHY"
    else:
        overall_health = "UNKNOWN"

    return overall_state, overall_health


def _render_and_write_override(
    worktree_path: Path,
    override_rel_path: str,
    project_name: str,
    db_service: str,
    dedicated_volume: str,
    publish_db: bool,
    app_service: str,
    app_port: int,
    host_port: int,
    db_env_var: str,
    database_url: str,
) -> Path:
    """Re-renders the effective compose.override.yaml with the loopback publication binding."""
    override_file = worktree_path / override_rel_path
    lines: list[str] = [
        "# Generated by isolation-check -- DO NOT COMMIT TO VERSION CONTROL",
        f"name: {project_name}",
        "",
        "services:",
        f"  {db_service}:",
        "    volumes:",
        f"      - {dedicated_volume}:/var/lib/postgresql/data",
    ]
    if not publish_db:
        lines.append("    ports: !reset []")

    lines.extend([
        f"  {app_service}:",
        "    environment:",
        f"      {db_env_var}: {database_url}",
        "    ports: !override",
        f"      - \"127.0.0.1:{host_port}:{app_port}\"",
        "",
        "volumes:",
        f"  {dedicated_volume}:",
        f"    name: {dedicated_volume}",
        "",
    ])

    override_file = worktree_path / override_rel_path
    atomic_write_text(override_file, "\n".join(lines), mode=0o600)
    return override_file


def _write_runtime_state(
    worktree_path: Path,
    data: dict[str, Any],
) -> Path:
    """Writes transient runtime state with restrictive file permissions."""
    state_file = worktree_path / ".isolation-check" / "runtime-state.json"
    atomic_write_text(state_file, json.dumps(data, indent=2), mode=0o600)
    return state_file


def start_environment(
    env_name: str,
    repo_hint: str | Path | None = None,
    timeout: float = DEFAULT_UP_TIMEOUT,
) -> LifecycleActionResult:
    """Starts a managed environment's Docker Compose project and returns a structured result."""
    # 1. Ownership and path validation
    try:
        rec_file, rec_data, manifest_file, manifest_data = locate_and_validate_environment(
            repo_hint, env_name
        )
    except OwnershipError as exc:
        return LifecycleActionResult(
            success=False,
            environment=env_name,
            error=str(exc),
            error_kind="ownership",
            exit_code=2,
        )

    # 2. Check Docker Compose capabilities
    try:
        verify_compose_capabilities()
    except ComposeCapabilityError as exc:
        return LifecycleActionResult(
            success=False,
            environment=env_name,
            error=str(exc),
            error_kind="capability",
            exit_code=2,
        )

    worktree_path = Path(rec_data["worktree_path"]).resolve()
    repo_root = rec_file.parent.parent.parent
    project_name = rec_data["compose_project_name"]
    base_compose_files = manifest_data["compose"]["base_compose_files"]
    override_rel_path = manifest_data["compose"]["override_file"]
    env_rel_path = manifest_data["compose"]["env_file"]

    db_info = manifest_data.get("database", {})
    db_service = db_info.get("service_name", "")
    dedicated_volume = db_info.get("volume_name", "")
    publish_db = bool(db_info.get("host_ports_published", False))

    app_info = manifest_data.get("application", {})
    app_service = app_info.get("service_name", "")
    app_port = int(app_info.get("app_port"))
    db_env_var = app_info.get("database_env_var", "")

    # Read unredacted database URL from private env file
    env_file = worktree_path / env_rel_path
    database_url = ""
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{db_env_var}="):
                database_url = line.split("=", 1)[1]
                break

    lock_path = worktree_path / ".isolation-check" / "lifecycle.lock"
    try:
        with acquire_environment_lock(lock_path, env_name, "up"):
            # 3. Idempotency check: see if project is already running
            try:
                existing_containers = _inspect_compose_containers(
                    worktree_path, project_name, base_compose_files, override_rel_path
                )
            except OwnershipError as exc:
                return LifecycleActionResult(
                    success=False,
                    environment=env_name,
                    error=str(exc),
                    error_kind="security",
                    exit_code=2,
                )

            overall_state, overall_health = _evaluate_overall_status(existing_containers)
            if overall_state == "RUNNING":
                pub_port, pub_url = _extract_app_published_port(existing_containers, app_service, app_port)
                return LifecycleActionResult(
                    success=True,
                    environment=env_name,
                    compose_project=project_name,
                    worktree_path=str(worktree_path),
                    status="RUNNING",
                    health=overall_health,
                    app_port=app_port,
                    host_port=pub_port,
                    url=pub_url,
                    already_running=True,
                    containers=existing_containers,
                    app_service=app_service,
                    db_service=db_service,
                    publish_db=publish_db,
                    dedicated_volume=dedicated_volume,
                    exit_code=0,
                )

            # 4. Port allocation with bounded retry for check-to-bind race conflicts
            attempt = 0
            excluded_candidates: list[int] = []
            last_error = ""
            candidate_host_port: int | None = None
            port_lock_path = repo_root / ".isolation-check" / "port-allocation.lock"

            while attempt < MAX_PORT_START_RETRIES:
                attempt += 1
                try:
                    with acquire_environment_lock(
                        port_lock_path,
                        env_name,
                        "port-allocation",
                        timeout=60.0,
                    ):
                        candidate_host_port = allocate_candidate_loopback_port(
                            repo_root,
                            env_name=env_name,
                            exclude_ports=excluded_candidates,
                        )
                        excluded_candidates.append(candidate_host_port)

                        # Re-render effective Compose override before startup
                        _render_and_write_override(
                            worktree_path=worktree_path,
                            override_rel_path=override_rel_path,
                            project_name=project_name,
                            db_service=db_service,
                            dedicated_volume=dedicated_volume,
                            publish_db=publish_db,
                            app_service=app_service,
                            app_port=app_port,
                            host_port=candidate_host_port,
                            db_env_var=db_env_var,
                            database_url=database_url,
                        )

                        # Build Compose up command
                        cmd = [
                            "docker",
                            "compose",
                            "-p",
                            project_name,
                            "--project-directory",
                            str(worktree_path),
                        ]
                        for bf in base_compose_files:
                            cmd.extend(["-f", str(worktree_path / bf)])
                        cmd.extend(["-f", str(worktree_path / override_rel_path)])
                        cmd.extend(["--env-file", str(worktree_path / env_rel_path)])
                        cmd.extend(["up", "-d"])

                        code, stdout, stderr = _run_compose_cmd(cmd, worktree_path, timeout)
                except ConcurrencyLockError as exc:
                    last_error = str(exc)
                    code = 1
                    stdout = ""
                    stderr = str(exc)

                if code == 0:
                    break

                last_error = stderr.strip() or stdout.strip() or f"Exited with code {code}"
                if candidate_host_port is not None:
                    release_port_reservation(repo_root, env_name, candidate_host_port)

                if is_port_binding_conflict(last_error) and attempt < MAX_PORT_START_RETRIES:
                    continue
                else:
                    release_port_reservation(repo_root, env_name)
                    return LifecycleActionResult(
                        success=False,
                        environment=env_name,
                        compose_project=project_name,
                        error=last_error,
                        preserved_worktree=str(worktree_path),
                        error_kind="runtime",
                        exit_code=1,
                    )

            # 5. Inspect Docker state post-startup
            post_containers = _inspect_compose_containers(
                worktree_path, project_name, base_compose_files, override_rel_path
            )
            overall_state, overall_health = _evaluate_overall_status(post_containers)
            actual_port, app_url = _extract_app_published_port(post_containers, app_service, app_port)
            if not actual_port:
                actual_port = candidate_host_port
                app_url = f"http://127.0.0.1:{actual_port}"

            # 6. Write transient runtime state with restrictive permissions
            runtime_data = {
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "environment": env_name,
                "compose_project": project_name,
                "status": overall_state,
                "health": overall_health,
                "app_service": app_service,
                "app_port": app_port,
                "host_port": actual_port,
                "url": app_url,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "containers": [
                    {
                        "name": c.get("Name"),
                        "service": c.get("Service"),
                        "state": c.get("State"),
                        "health": _evaluate_container_health(c),
                    }
                    for c in post_containers
                ],
            }
            _write_runtime_state(worktree_path, runtime_data)

            return LifecycleActionResult(
                success=True,
                environment=env_name,
                compose_project=project_name,
                worktree_path=str(worktree_path),
                status=overall_state,
                health=overall_health,
                app_port=app_port,
                host_port=actual_port,
                url=app_url,
                containers=post_containers,
                app_service=app_service,
                db_service=db_service,
                publish_db=publish_db,
                dedicated_volume=dedicated_volume,
                exit_code=0,
            )

    except ConcurrencyLockError as exc:
        return LifecycleActionResult(
            success=False,
            environment=env_name,
            error=str(exc),
            error_kind="concurrency",
            exit_code=2,
        )


def execute_up(
    env_name: str,
    repo_hint: str | Path | None = None,
    output_json: bool = False,
    timeout: float = DEFAULT_UP_TIMEOUT,
) -> int:
    """Implements `isolation-check up <environment>`."""
    result = start_environment(env_name, repo_hint, timeout)
    if not result.success:
        if output_json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            if result.error_kind == "ownership":
                sys.stderr.write(f"Ownership / Validation Error: {result.error}\n")
            elif result.error_kind == "capability":
                sys.stderr.write(f"Docker Compose Capability Error: {result.error}\n")
            elif result.error_kind == "concurrency":
                sys.stderr.write(f"Concurrency Error: {result.error}\n")
            elif result.error_kind == "security":
                sys.stderr.write(f"Security Error: {result.error}\n")
            else:
                sys.stderr.write(f"\nError: Failed to start environment '{env_name}': {result.error}\n")
                if result.preserved_worktree:
                    sys.stderr.write(
                        f"The environment and generated files at {result.preserved_worktree} have been preserved.\n"
                        f"To inspect or diagnose: cd {result.preserved_worktree} && docker compose ps\n"
                    )
        return result.exit_code

    if output_json:
        out = {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "success": True,
            "environment": env_name,
            "compose_project": result.compose_project,
            "worktree_path": result.worktree_path,
            "status": result.status,
            "health": result.health,
            "app_port": result.app_port,
            "host_port": result.host_port,
            "url": result.url,
            "containers": [
                {
                    "name": c.get("Name"),
                    "service": c.get("Service"),
                    "state": c.get("State"),
                    "health": _evaluate_container_health(c),
                }
                for c in result.containers
            ],
        }
        if result.already_running:
            out["already_running"] = True
        print(json.dumps(out, indent=2))
    else:
        if result.already_running:
            print("========================================================================")
            print("isolation-check up -- ENVIRONMENT RUNNING (ALREADY ACTIVE)")
            print(f"Environment: {env_name}")
            print(f"Project:     {result.compose_project}")
            print(f"Worktree:    {result.worktree_path}")
            if result.url:
                print(f"Application URL: {result.url} (Loopback 127.0.0.1:{result.host_port} -> container :{result.app_port})")
            print(f"State:       RUNNING")
            print(f"Health:      {result.health}")
            print("========================================================================")
        else:
            print("========================================================================")
            print("isolation-check up -- ENVIRONMENT STARTED")
            print(f"Environment: {env_name}")
            print(f"Project:     {result.compose_project}")
            print(f"Worktree:    {result.worktree_path}")
            print()
            print(f"Application: {result.app_service} running at {result.url}")
            print(f"             (Loopback 127.0.0.1:{result.host_port} -> container :{result.app_port})")
            if result.publish_db:
                print(f"Database:    {result.db_service} (Host publishing ENABLED)")
            else:
                print(f"Database:    {result.db_service} (Internal container network only, host unpublished)")
            print(f"Volume:      {result.dedicated_volume}")
            print()
            print(f"State:       {result.status}")
            print(f"Health:      {result.health}")
            print("========================================================================")

    return 0


def query_environment_status(
    env_name: str,
    repo_hint: str | Path | None = None,
    timeout: float = DEFAULT_STATUS_TIMEOUT,
) -> EnvironmentStatusResult:
    """Queries runtime state and health of a managed environment (read-only)."""
    try:
        rec_file, rec_data, manifest_file, manifest_data = locate_and_validate_environment(
            repo_hint, env_name
        )
    except OwnershipError as exc:
        return EnvironmentStatusResult(
            success=False,
            environment=env_name,
            error=str(exc),
            exit_code=2,
        )

    worktree_path = Path(rec_data["worktree_path"]).resolve()
    project_name = rec_data["compose_project_name"]
    base_compose_files = manifest_data["compose"]["base_compose_files"]
    override_rel_path = manifest_data["compose"]["override_file"]

    app_info = manifest_data.get("application", {})
    app_service = app_info.get("service_name", "")
    app_port = int(app_info.get("app_port", 0))

    # Load stored runtime state if present
    stored_state_file = worktree_path / ".isolation-check" / "runtime-state.json"
    stored_state: dict[str, Any] = {}
    if stored_state_file.is_file():
        try:
            stored_state = json.loads(stored_state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Inspect live Docker state
    try:
        containers = _inspect_compose_containers(
            worktree_path, project_name, base_compose_files, override_rel_path, timeout
        )
    except OwnershipError as exc:
        return EnvironmentStatusResult(
            success=False,
            environment=env_name,
            compose_project=project_name,
            worktree_path=str(worktree_path),
            error=str(exc),
            exit_code=2,
        )

    overall_state, overall_health = _evaluate_overall_status(containers)
    actual_port, app_url = _extract_app_published_port(containers, app_service, app_port)

    # Detect stale stored state: if stored state claimed running but Docker shows stopped
    is_stale = False
    if stored_state.get("status") == "RUNNING" and overall_state != "RUNNING":
        is_stale = True

    if overall_state != "RUNNING":
        app_url = None
        actual_port = None

    return EnvironmentStatusResult(
        success=True,
        environment=env_name,
        compose_project=project_name,
        worktree_path=str(worktree_path),
        status=overall_state,
        health=overall_health,
        app_target_port=app_port,
        host_port=actual_port,
        url=app_url,
        ownership_verified=True,
        stale_runtime_state=is_stale,
        containers=containers,
        exit_code=0,
    )


def execute_status(
    env_name: str,
    repo_hint: str | Path | None = None,
    output_json: bool = False,
    timeout: float = DEFAULT_STATUS_TIMEOUT,
) -> int:
    """Implements `isolation-check status <environment>` (read-only)."""
    result = query_environment_status(env_name, repo_hint, timeout)
    if not result.success:
        if output_json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            if "Ownership" in (result.error or "") or "Validation" in (result.error or ""):
                sys.stderr.write(f"Ownership / Validation Error: {result.error}\n")
            elif "Security" in (result.error or ""):
                sys.stderr.write(f"Security Error: {result.error}\n")
            else:
                sys.stderr.write(f"Status Error: {result.error}\n")
        return result.exit_code

    if output_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print("========================================================================")
        print("isolation-check status -- ENVIRONMENT STATUS")
        print(f"Environment: {env_name}")
        print(f"Project:     {result.compose_project}")
        print(f"Worktree:    {result.worktree_path}")
        print("Ownership:   VERIFIED (managed by isolation-check)")
        print()
        print(f"Status:      {result.status}")
        print(f"Health:      {result.health}")
        if result.stale_runtime_state:
            print("Notice:      Stored runtime state is STALE (containers have stopped).")
        if result.url:
            print(f"URL:         {result.url} (Loopback 127.0.0.1:{result.host_port} -> container :{result.app_target_port})")
        else:
            print(f"URL:         Not available (environment is {result.status})")
        print()
        if result.containers:
            print("Services:")
            for c in result.containers:
                svc = c.get("Service", "unknown")
                st = c.get("State", "unknown")
                hl = _evaluate_container_health(c)
                print(f"  - {svc:12} State: {st:8} Health: {hl}")
        else:
            print("No active containers found for this project.")
        print("========================================================================")

    return 0


def stop_environment(
    env_name: str,
    repo_hint: str | Path | None = None,
    timeout: float = DEFAULT_DOWN_TIMEOUT,
    destructive: bool = False,
) -> LifecycleActionResult:
    """Stops a managed environment's Docker Compose project while preserving data."""
    if destructive:
        return LifecycleActionResult(
            success=False,
            environment=env_name,
            error="Destructive flags are not supported in isolation-check down. Data volumes are strictly preserved.",
            error_kind="validation",
            exit_code=2,
        )

    try:
        rec_file, rec_data, manifest_file, manifest_data = locate_and_validate_environment(
            repo_hint, env_name
        )
    except OwnershipError as exc:
        return LifecycleActionResult(
            success=False,
            environment=env_name,
            error=str(exc),
            error_kind="ownership",
            exit_code=2,
        )

    worktree_path = Path(rec_data["worktree_path"]).resolve()
    project_name = rec_data["compose_project_name"]
    base_compose_files = manifest_data["compose"]["base_compose_files"]
    override_rel_path = manifest_data["compose"]["override_file"]
    env_rel_path = manifest_data["compose"]["env_file"]
    dedicated_volume = manifest_data.get("database", {}).get("volume_name", "")

    lock_path = worktree_path / ".isolation-check" / "lifecycle.lock"
    try:
        with acquire_environment_lock(lock_path, env_name, "down"):
            # Check if already stopped (safe repeatability)
            try:
                containers = _inspect_compose_containers(
                    worktree_path, project_name, base_compose_files, override_rel_path
                )
            except OwnershipError as exc:
                return LifecycleActionResult(
                    success=False,
                    environment=env_name,
                    error=str(exc),
                    error_kind="security",
                    exit_code=2,
                )

            if not containers:
                release_port_reservation(rec_file.parent.parent.parent, env_name)
                state_file = worktree_path / ".isolation-check" / "runtime-state.json"
                if state_file.is_file():
                    try:
                        s_data = json.loads(state_file.read_text(encoding="utf-8"))
                        s_data["status"] = "STOPPED"
                        s_data["stopped_at"] = datetime.now(timezone.utc).isoformat()
                        atomic_write_text(state_file, json.dumps(s_data, indent=2), mode=0o600)
                    except Exception:
                        pass

                return LifecycleActionResult(
                    success=True,
                    environment=env_name,
                    compose_project=project_name,
                    worktree_path=str(worktree_path),
                    status="STOPPED",
                    message="Environment is already stopped.",
                    volume_preserved=dedicated_volume,
                    exit_code=0,
                )

            # Execute docker compose down WITHOUT -v / --volumes
            cmd = [
                "docker",
                "compose",
                "-p",
                project_name,
                "--project-directory",
                str(worktree_path),
            ]
            for bf in base_compose_files:
                cmd.extend(["-f", str(worktree_path / bf)])
            cmd.extend(["-f", str(worktree_path / override_rel_path)])
            cmd.extend(["--env-file", str(worktree_path / env_rel_path)])
            cmd.extend(["down"])

            code, stdout, stderr = _run_compose_cmd(cmd, worktree_path, timeout)
            if code != 0:
                err = stderr.strip() or stdout.strip() or f"Exited with code {code}"
                return LifecycleActionResult(
                    success=False,
                    environment=env_name,
                    compose_project=project_name,
                    error=err,
                    error_kind="runtime",
                    exit_code=1,
                )

            release_port_reservation(rec_file.parent.parent.parent, env_name)

            state_file = worktree_path / ".isolation-check" / "runtime-state.json"
            if state_file.is_file():
                try:
                    s_data = json.loads(state_file.read_text(encoding="utf-8"))
                    s_data["status"] = "STOPPED"
                    s_data["stopped_at"] = datetime.now(timezone.utc).isoformat()
                    atomic_write_text(state_file, json.dumps(s_data, indent=2), mode=0o600)
                except Exception:
                    pass

            return LifecycleActionResult(
                success=True,
                environment=env_name,
                compose_project=project_name,
                worktree_path=str(worktree_path),
                status="STOPPED",
                volume_preserved=dedicated_volume,
                exit_code=0,
            )
    except ConcurrencyLockError as exc:
        return LifecycleActionResult(
            success=False,
            environment=env_name,
            error=str(exc),
            error_kind="concurrency",
            exit_code=2,
        )


def execute_down(
    env_name: str,
    repo_hint: str | Path | None = None,
    output_json: bool = False,
    timeout: float = DEFAULT_DOWN_TIMEOUT,
    destructive: bool = False,
) -> int:
    """Implements non-destructive `isolation-check down <environment>`."""
    if destructive:
        sys.stderr.write("Error: Destructive flags are not supported in isolation-check down.\n")
        sys.stderr.write("Data volumes are strictly preserved. Teardown/destroy is planned for a future milestone.\n")
        return 2

    result = stop_environment(env_name, repo_hint, timeout, destructive)
    if not result.success:
        if output_json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            if result.error_kind == "ownership":
                sys.stderr.write(f"Ownership / Validation Error: {result.error}\n")
            elif result.error_kind == "security":
                sys.stderr.write(f"Security Error: {result.error}\n")
            elif result.error_kind == "concurrency":
                sys.stderr.write(f"Concurrency Error: {result.error}\n")
            else:
                sys.stderr.write(f"Error: Failed to stop environment '{env_name}': {result.error}\n")
        return result.exit_code

    if output_json:
        out = {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "success": True,
            "environment": env_name,
            "compose_project": result.compose_project,
            "status": "STOPPED",
            "volume_preserved": result.volume_preserved,
        }
        if result.message:
            out["message"] = result.message
        print(json.dumps(out, indent=2))
    else:
        if result.message:
            print(f"Environment '{env_name}' (project '{result.compose_project}') is already stopped.")
            print(f"Dedicated volume '{result.volume_preserved}' and worktree were preserved.")
        else:
            print("========================================================================")
            print("isolation-check down -- ENVIRONMENT STOPPED")
            print(f"Environment: {env_name}")
            print(f"Project:     {result.compose_project}")
            print(f"Status:      STOPPED")
            print(f"Storage:     Dedicated volume '{result.volume_preserved}' was PRESERVED.")
            print(f"Worktree:    {result.worktree_path} was PRESERVED.")
            print("========================================================================")

    return 0
