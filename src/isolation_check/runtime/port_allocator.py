"""Loopback host-port allocation and collision handling for managed runtimes.

NOTE ON CHECK-TO-BIND RACE CONDITION:
Testing socket availability before starting Docker Compose is subject to a classic
time-of-check to time-of-use (TOCTOU) / check-to-bind race condition: another host
process may bind to the candidate port between our socket check and Docker's bind() call.
Testing socket availability verifies immediate availability but DOES NOT guarantee
exclusive reservation or permanent ownership.

To eliminate races between different managed environments running concurrently:
1. isolation-check uses atomic reservation records in `.isolation-check/ports/<port>.json`.
2. isolation-check acquires a repository-scoped port-allocation lock during candidate
   selection and Compose startup.
3. If and only if startup fails with an identified host-port binding conflict, isolation-check
   retries startup with another bounded loopback candidate port. Unrelated errors are NOT retried.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
from typing import Sequence

from isolation_check.runtime.concurrency import _is_pid_alive

DEFAULT_PORT_START = 10001
MAX_PORT_RANGE = 65535
PORT_RESERVATIONS_DIR_NAME = "ports"


def is_port_free_on_loopback(port: int, host: str = "127.0.0.1") -> bool:
    """Checks if a port is currently available to bind on 127.0.0.1.

    Notice: This check is subject to a check-to-bind race and does not grant
    permanent port ownership.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def reserve_loopback_port(repo_root: Path, port: int, env_name: str) -> bool:
    """Atomically reserves a loopback host port for an environment in the repository.

    Returns True if successfully reserved or already owned by env_name; False otherwise.
    """
    ports_dir = repo_root / ".isolation-check" / PORT_RESERVATIONS_DIR_NAME
    ports_dir.mkdir(parents=True, exist_ok=True)
    res_file = ports_dir / f"{port}.json"

    for _ in range(2):
        try:
            fd = os.open(str(res_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            payload = json.dumps({
                "port": port,
                "env_name": env_name,
                "pid": os.getpid(),
                "reserved_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2).encode("utf-8")
            os.write(fd, payload)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                raw = res_file.read_text(encoding="utf-8")
                data = json.loads(raw)
                if data.get("env_name") == env_name:
                    return True
                res_pid = data.get("pid")
                if isinstance(res_pid, int) and not _is_pid_alive(res_pid):
                    # Stale reservation from a dead process, reclaim it
                    res_file.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            return False
    return False


def release_port_reservation(repo_root: Path, env_name: str, port: int | None = None) -> None:
    """Releases active port reservations for an environment."""
    ports_dir = repo_root / ".isolation-check" / PORT_RESERVATIONS_DIR_NAME
    if not ports_dir.is_dir():
        return

    if port is not None:
        target_files = [ports_dir / f"{port}.json"]
    else:
        target_files = list(ports_dir.glob("*.json"))

    for f in target_files:
        try:
            if f.is_file():
                raw = f.read_text(encoding="utf-8")
                data = json.loads(raw)
                if data.get("env_name") == env_name:
                    f.unlink(missing_ok=True)
        except Exception:
            pass


def collect_active_runtime_ports(repo_root: Path) -> set[int]:
    """Collects all host ports currently recorded in active runtime-state.json and reservation files."""
    active_ports: set[int] = set()

    # 1. Active runtime states
    env_dir = repo_root / ".isolation-check" / "environments"
    if env_dir.is_dir():
        for rec_file in env_dir.glob("*.json"):
            try:
                data = json.loads(rec_file.read_text(encoding="utf-8"))
                wt_path = Path(data.get("worktree_path", "")).resolve()
                state_file = wt_path / ".isolation-check" / "runtime-state.json"
                if state_file.is_file():
                    state_data = json.loads(state_file.read_text(encoding="utf-8"))
                    if state_data.get("status") == "RUNNING":
                        port = state_data.get("host_port")
                        if isinstance(port, int) and 1 <= port <= 65535:
                            active_ports.add(port)
            except Exception:
                continue

    # 2. Transient port reservations
    ports_dir = repo_root / ".isolation-check" / PORT_RESERVATIONS_DIR_NAME
    if ports_dir.is_dir():
        for p_file in ports_dir.glob("*.json"):
            try:
                raw = p_file.read_text(encoding="utf-8")
                data = json.loads(raw)
                res_pid = data.get("pid")
                if isinstance(res_pid, int) and not _is_pid_alive(res_pid):
                    p_file.unlink(missing_ok=True)
                    continue
                p_val = int(p_file.stem)
                if 1 <= p_val <= 65535:
                    active_ports.add(p_val)
            except Exception:
                continue

    return active_ports


def allocate_candidate_loopback_port(
    repo_root: Path,
    env_name: str = "",
    exclude_ports: Sequence[int] = (),
    preferred_start: int = DEFAULT_PORT_START,
) -> int:
    """Allocates and reserves a candidate loopback host port.

    Checks active runtime state, transient reservations, and socket availability.
    """
    occupied_ports = collect_active_runtime_ports(repo_root)
    excluded = set(occupied_ports).union(exclude_ports)

    candidate = preferred_start
    while candidate <= MAX_PORT_RANGE:
        if candidate not in excluded:
            if is_port_free_on_loopback(candidate):
                if env_name:
                    if reserve_loopback_port(repo_root, candidate, env_name):
                        return candidate
                else:
                    return candidate
        candidate += 1

    raise RuntimeError(
        f"Unable to find an available loopback port in range {preferred_start}-{MAX_PORT_RANGE} on 127.0.0.1."
    )


def is_port_binding_conflict(error_output: str) -> bool:
    """Determines if a Docker Compose failure was caused by a host-port binding conflict."""
    patterns = (
        r"bind: address already in use",
        r"port is already allocated",
        r"listen tcp 127\.0\.0\.1:\d+: bind",
        r"failed to bind host port",
        r"address already in use",
    )
    lower = error_output.lower()
    return any(re.search(pat, lower) for pat in patterns)
