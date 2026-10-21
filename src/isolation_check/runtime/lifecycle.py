"""Managed runtime lifecycle implementation: up, status, and down."""
from __future__ import annotations

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
from isolation_check.runtime.port_allocator import (
    allocate_candidate_loopback_port,
    is_port_binding_conflict,
)
from isolation_check.runtime.validation import OwnershipError, locate_and_validate_environment
from isolation_check.version import get_package_version

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

    override_file.write_text("\n".join(lines), encoding="utf-8")
    try:
        os.chmod(override_file, 0o600)
    except OSError:
        pass
    return override_file


def _write_runtime_state(
    worktree_path: Path,
    data: dict[str, Any],
) -> Path:
    """Writes transient runtime state with restrictive file permissions."""
    state_file = worktree_path / ".isolation-check" / "runtime-state.json"
    state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(state_file, 0o600)
    except OSError:
        pass
    return state_file


def execute_up(
    env_name: str,
    repo_hint: str | Path | None = None,
    output_json: bool = False,
    timeout: float = DEFAULT_UP_TIMEOUT,
) -> int:
    """Implements `isolation-check up <environment>`."""
    # 1. Ownership and path validation
    try:
        rec_file, rec_data, manifest_file, manifest_data = locate_and_validate_environment(
            repo_hint, env_name
        )
    except OwnershipError as exc:
        if output_json:
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "environment": env_name,
                "error": str(exc),
            }, indent=2))
        else:
            sys.stderr.write(f"Ownership / Validation Error: {exc}\n")
        return 2

    # 2. Check Docker Compose capabilities
    try:
        verify_compose_capabilities()
    except ComposeCapabilityError as exc:
        if output_json:
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "environment": env_name,
                "error": str(exc),
            }, indent=2))
        else:
            sys.stderr.write(f"Docker Compose Capability Error: {exc}\n")
        return 2

    worktree_path = Path(rec_data["worktree_path"]).resolve()
    repo_root = rec_file.parent.parent.parent
    project_name = rec_data["compose_project_name"]
    base_compose_files = manifest_data["compose"]["base_compose_files"]
    override_rel_path = manifest_data["compose"]["override_file"]
    env_rel_path = manifest_data["compose"]["env_file"]

    db_info = manifest_data.get("database", {})
    db_service = db_info.get("service_name")
    dedicated_volume = db_info.get("volume_name")
    publish_db = bool(db_info.get("host_ports_published", False))

    app_info = manifest_data.get("application", {})
    app_service = app_info.get("service_name")
    app_port = int(app_info.get("app_port"))
    db_env_var = app_info.get("database_env_var")

    # Read unredacted database URL from private env file
    env_file = worktree_path / env_rel_path
    database_url = ""
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{db_env_var}="):
                database_url = line.split("=", 1)[1]
                break

    # 3. Idempotency check: see if project is already running
    try:
        existing_containers = _inspect_compose_containers(
            worktree_path, project_name, base_compose_files, override_rel_path
        )
    except OwnershipError as exc:
        if output_json:
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            sys.stderr.write(f"Security Error: {exc}\n")
        return 2

    overall_state, overall_health = _evaluate_overall_status(existing_containers)
    if overall_state == "RUNNING":
        pub_port, pub_url = _extract_app_published_port(existing_containers, app_service, app_port)
        if output_json:
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": True,
                "environment": env_name,
                "compose_project": project_name,
                "worktree_path": str(worktree_path),
                "status": "RUNNING",
                "health": overall_health,
                "app_port": app_port,
                "host_port": pub_port,
                "url": pub_url,
                "already_running": True,
                "containers": [
                    {
                        "name": c.get("Name"),
                        "service": c.get("Service"),
                        "state": c.get("State"),
                        "health": _evaluate_container_health(c),
                    }
                    for c in existing_containers
                ],
            }, indent=2))
        else:
            print("========================================================================")
            print("isolation-check up -- ENVIRONMENT RUNNING (ALREADY ACTIVE)")
            print(f"Environment: {env_name}")
            print(f"Project:     {project_name}")
            print(f"Worktree:    {worktree_path}")
            if pub_url:
                print(f"Application URL: {pub_url} (Loopback 127.0.0.1:{pub_port} -> container :{app_port})")
            print(f"State:       RUNNING")
            print(f"Health:      {overall_health}")
            print("========================================================================")
        return 0

    # 4. Port allocation with bounded retry for check-to-bind race conflicts
    attempt = 0
    excluded_candidates: list[int] = []
    last_error = ""

    while attempt < MAX_PORT_START_RETRIES:
        attempt += 1
        candidate_host_port = allocate_candidate_loopback_port(
            repo_root, exclude_ports=excluded_candidates
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
        if code == 0:
            # Startup succeeded!
            break

        last_error = stderr.strip() or stdout.strip() or f"Exited with code {code}"
        # Retry only if error is a host-port binding conflict
        if is_port_binding_conflict(last_error) and attempt < MAX_PORT_START_RETRIES:
            continue
        else:
            # Non-port error or exceeded retries: preserve environment and report failure
            if output_json:
                print(json.dumps({
                    "schema_version": "1.0",
                    "tool_version": get_package_version(),
                    "success": False,
                    "environment": env_name,
                    "compose_project": project_name,
                    "error": last_error,
                    "preserved_worktree": str(worktree_path),
                }, indent=2))
            else:
                sys.stderr.write(f"\nError: Failed to start environment '{env_name}': {last_error}\n")
                sys.stderr.write(
                    f"The environment and generated files at {worktree_path} have been preserved.\n"
                    f"To inspect or diagnose: cd {worktree_path} && docker compose ps\n"
                )
            return 1

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

    # 7. Format output
    if output_json:
        out = {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "success": True,
            "environment": env_name,
            "compose_project": project_name,
            "worktree_path": str(worktree_path),
            "status": overall_state,
            "health": overall_health,
            "app_port": app_port,
            "host_port": actual_port,
            "url": app_url,
            "containers": runtime_data["containers"],
        }
        print(json.dumps(out, indent=2))
    else:
        print("========================================================================")
        print("isolation-check up -- ENVIRONMENT STARTED")
        print(f"Environment: {env_name}")
        print(f"Project:     {project_name}")
        print(f"Worktree:    {worktree_path}")
        print()
        print(f"Application: {app_service} running at {app_url}")
        print(f"             (Loopback 127.0.0.1:{actual_port} -> container :{app_port})")
        if publish_db:
            print(f"Database:    {db_service} (Host publishing ENABLED)")
        else:
            print(f"Database:    {db_service} (Internal container network only, host unpublished)")
        print(f"Volume:      {dedicated_volume}")
        print()
        print(f"State:       {overall_state}")
        print(f"Health:      {overall_health}")
        print("========================================================================")

    return 0


def execute_status(
    env_name: str,
    repo_hint: str | Path | None = None,
    output_json: bool = False,
    timeout: float = DEFAULT_STATUS_TIMEOUT,
) -> int:
    """Implements `isolation-check status <environment>` (read-only)."""
    try:
        rec_file, rec_data, manifest_file, manifest_data = locate_and_validate_environment(
            repo_hint, env_name
        )
    except OwnershipError as exc:
        if output_json:
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "environment": env_name,
                "error": str(exc),
            }, indent=2))
        else:
            sys.stderr.write(f"Ownership / Validation Error: {exc}\n")
        return 2

    worktree_path = Path(rec_data["worktree_path"]).resolve()
    project_name = rec_data["compose_project_name"]
    base_compose_files = manifest_data["compose"]["base_compose_files"]
    override_rel_path = manifest_data["compose"]["override_file"]

    app_info = manifest_data.get("application", {})
    app_service = app_info.get("service_name")
    app_port = int(app_info.get("app_port"))

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
        if output_json:
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            sys.stderr.write(f"Security Error: {exc}\n")
        return 2

    overall_state, overall_health = _evaluate_overall_status(containers)
    actual_port, app_url = _extract_app_published_port(containers, app_service, app_port)

    # Detect stale stored state: if stored state claimed running but Docker shows stopped
    is_stale = False
    if stored_state.get("status") == "RUNNING" and overall_state != "RUNNING":
        is_stale = True

    if overall_state != "RUNNING":
        # Never present stale URL when containers are stopped
        app_url = None
        actual_port = None

    if output_json:
        data = {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "environment": env_name,
            "compose_project": project_name,
            "worktree_path": str(worktree_path),
            "status": overall_state,
            "health": overall_health,
            "app_target_port": app_port,
            "host_port": actual_port,
            "url": app_url,
            "ownership_verified": True,
            "stale_runtime_state": is_stale,
            "containers": [
                {
                    "name": c.get("Name"),
                    "service": c.get("Service"),
                    "state": c.get("State"),
                    "health": _evaluate_container_health(c),
                }
                for c in containers
            ],
        }
        print(json.dumps(data, indent=2))
    else:
        print("========================================================================")
        print("isolation-check status -- ENVIRONMENT STATUS")
        print(f"Environment: {env_name}")
        print(f"Project:     {project_name}")
        print(f"Worktree:    {worktree_path}")
        print(f"Ownership:   VERIFIED (managed by isolation-check)")
        print()
        print(f"Status:      {overall_state}")
        print(f"Health:      {overall_health}")
        if is_stale:
            print("Notice:      Stored runtime state is STALE (containers have stopped).")
        if app_url:
            print(f"URL:         {app_url} (Loopback 127.0.0.1:{actual_port} -> container :{app_port})")
        else:
            print(f"URL:         Not available (environment is {overall_state})")
        print()
        if containers:
            print("Services:")
            for c in containers:
                svc = c.get("Service", "unknown")
                st = c.get("State", "unknown")
                hl = _evaluate_container_health(c)
                print(f"  - {svc:12} State: {st:8} Health: {hl}")
        else:
            print("No active containers found for this project.")
        print("========================================================================")

    return 0


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

    try:
        rec_file, rec_data, manifest_file, manifest_data = locate_and_validate_environment(
            repo_hint, env_name
        )
    except OwnershipError as exc:
        if output_json:
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "environment": env_name,
                "error": str(exc),
            }, indent=2))
        else:
            sys.stderr.write(f"Ownership / Validation Error: {exc}\n")
        return 2

    worktree_path = Path(rec_data["worktree_path"]).resolve()
    project_name = rec_data["compose_project_name"]
    base_compose_files = manifest_data["compose"]["base_compose_files"]
    override_rel_path = manifest_data["compose"]["override_file"]
    env_rel_path = manifest_data["compose"]["env_file"]
    dedicated_volume = manifest_data.get("database", {}).get("volume_name", "")

    # Check if already stopped (safe repeatability)
    try:
        containers = _inspect_compose_containers(
            worktree_path, project_name, base_compose_files, override_rel_path
        )
    except OwnershipError as exc:
        if output_json:
            print(json.dumps({"success": False, "error": str(exc)}, indent=2))
        else:
            sys.stderr.write(f"Security Error: {exc}\n")
        return 2

    if not containers:
        # Mark runtime state as stopped
        state_file = worktree_path / ".isolation-check" / "runtime-state.json"
        if state_file.is_file():
            try:
                s_data = json.loads(state_file.read_text(encoding="utf-8"))
                s_data["status"] = "STOPPED"
                s_data["stopped_at"] = datetime.now(timezone.utc).isoformat()
                state_file.write_text(json.dumps(s_data, indent=2), encoding="utf-8")
            except Exception:
                pass

        if output_json:
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": True,
                "environment": env_name,
                "compose_project": project_name,
                "status": "STOPPED",
                "message": "Environment is already stopped.",
                "volume_preserved": dedicated_volume,
            }, indent=2))
        else:
            print(f"Environment '{env_name}' (project '{project_name}') is already stopped.")
            print(f"Dedicated volume '{dedicated_volume}' and worktree were preserved.")
        return 0

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
        if output_json:
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "environment": env_name,
                "error": err,
            }, indent=2))
        else:
            sys.stderr.write(f"Error: Failed to stop environment '{env_name}': {err}\n")
        return 1

    # Mark runtime state as stopped
    state_file = worktree_path / ".isolation-check" / "runtime-state.json"
    if state_file.is_file():
        try:
            s_data = json.loads(state_file.read_text(encoding="utf-8"))
            s_data["status"] = "STOPPED"
            s_data["stopped_at"] = datetime.now(timezone.utc).isoformat()
            state_file.write_text(json.dumps(s_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    if output_json:
        print(json.dumps({
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "success": True,
            "environment": env_name,
            "compose_project": project_name,
            "status": "STOPPED",
            "volume_preserved": dedicated_volume,
        }, indent=2))
    else:
        print("========================================================================")
        print("isolation-check down -- ENVIRONMENT STOPPED")
        print(f"Environment: {env_name}")
        print(f"Project:     {project_name}")
        print(f"Status:      STOPPED")
        print(f"Storage:     Dedicated volume '{dedicated_volume}' was PRESERVED.")
        print(f"Worktree:    {worktree_path} was PRESERVED.")
        print("========================================================================")

    return 0
