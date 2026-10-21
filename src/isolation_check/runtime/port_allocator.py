"""Loopback host-port allocation and collision handling for managed runtimes.

NOTE ON CHECK-TO-BIND RACE CONDITION:
Testing socket availability before starting Docker Compose is subject to a classic
time-of-check to time-of-use (TOCTOU) / check-to-bind race condition: another host
process may bind to the candidate port between our socket check and Docker's bind() call.
Testing socket availability verifies immediate availability but DOES NOT guarantee
exclusive reservation or permanent ownership.
To mitigate this race condition, isolation-check inspects Docker Compose startup errors.
If and only if startup fails with an identified host-port binding conflict, isolation-check
retries startup with another bounded loopback candidate port.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import socket
from typing import Sequence

DEFAULT_PORT_START = 10001
MAX_PORT_RANGE = 65535


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


def collect_active_runtime_ports(repo_root: Path) -> set[int]:
    """Collects all host ports currently recorded in active runtime-state.json files."""
    active_ports: set[int] = set()
    env_dir = repo_root / ".isolation-check" / "environments"
    if not env_dir.is_dir():
        return active_ports

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

    return active_ports


def allocate_candidate_loopback_port(
    repo_root: Path,
    exclude_ports: Sequence[int] = (),
    preferred_start: int = DEFAULT_PORT_START,
) -> int:
    """Allocates a candidate loopback host port by checking both active runtime state and socket availability.

    Subject to check-to-bind race condition as documented in module docstring.
    """
    occupied_ports = collect_active_runtime_ports(repo_root)
    excluded = set(occupied_ports).union(exclude_ports)

    candidate = preferred_start
    while candidate <= MAX_PORT_RANGE:
        if candidate not in excluded:
            if is_port_free_on_loopback(candidate):
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
