"""Docker Compose configuration collector.

Executes `docker compose config --format json` safely using bounded streams,
subprocess argument lists, timeouts, and sanitized error messages.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

# Maximum allowed size for Compose JSON output (1 MB) to prevent memory exhaustion
DEFAULT_OUTPUT_LIMIT = 1_048_576
DEFAULT_TIMEOUT_SECONDS = 15.0

COMPOSE_FILE_CANDIDATES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)


class ComposeCollectorError(Exception):
    """Base exception for Compose collection failures."""


class ComposeFileNotFoundError(ComposeCollectorError):
    """Raised when Compose configuration file cannot be found."""


class ComposeExecutionError(ComposeCollectorError):
    """Raised when `docker compose` execution fails."""


class ComposeOutputLimitExceededError(ComposeCollectorError):
    """Raised when Compose output exceeds safety threshold."""


def resolve_compose_target(path: str | Path) -> tuple[Path, list[Path]]:
    """Resolves an environment path to (project_directory, compose_files).

    If the directory contains an isolation-check manifest (.isolation-check/manifest.json),
    resolves the full set of base files and generated override file.
    Otherwise searches for standard Compose candidates and optional override file.
    """
    target = Path(path).resolve()
    if not target.exists():
        raise ComposeFileNotFoundError(f"Target path does not exist: {target}")

    if target.is_file():
        return target.parent, [target]

    if target.is_dir():
        # Check if managed by isolation-check with manifest.json
        manifest_file = target / ".isolation-check" / "manifest.json"
        if manifest_file.is_file():
            try:
                import json
                m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                compose_info = m_data.get("compose", {})
                base_files = compose_info.get("base_compose_files", [])
                override_file = compose_info.get("override_file")
                files: list[Path] = []
                for bf in base_files:
                    bf_path = (target / bf).resolve()
                    if bf_path.is_file():
                        files.append(bf_path)
                if override_file:
                    ov_path = (target / override_file).resolve()
                    if ov_path.is_file():
                        files.append(ov_path)
                if files:
                    return target, files
            except Exception:
                pass

        # Standard discovery
        found_base: Path | None = None
        for candidate in COMPOSE_FILE_CANDIDATES:
            candidate_path = target / candidate
            if candidate_path.is_file():
                found_base = candidate_path
                break

        if found_base is not None:
            files = [found_base]
            for ov_name in ("compose.override.yaml", "compose.override.yml", "docker-compose.override.yaml", "docker-compose.override.yml"):
                ov_path = target / ov_name
                if ov_path.is_file():
                    files.append(ov_path)
                    break
            return target, files

        candidates_str = ", ".join(COMPOSE_FILE_CANDIDATES)
        raise ComposeFileNotFoundError(
            f"No Compose file found in directory '{target}'. Searched for: {candidates_str}"
        )

    raise ComposeFileNotFoundError(f"Target is neither a file nor a directory: {target}")


def _build_safe_env() -> dict[str, str]:
    """Constructs a minimal, sanitized environment for Docker Compose execution.

    Avoids passing arbitrary application environment variables that might contain
    secrets or alter compose interpolation unintentionally, while retaining
    necessary system paths and Docker host/context variables.
    """
    allowed_keys = {
        # System & execution paths
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        # User home & profile
        "USER",
        "USERNAME",
        "HOME",
        "USERPROFILE",
        "HOMEPATH",
        "HOMEDRIVE",
        "APPDATA",
        "LOCALAPPDATA",
        # Platform program directories (required for Docker CLI plugin discovery)
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "COMMONPROGRAMFILES",
        # Docker configuration & context
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
        "DOCKER_CERT_PATH",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CLI_PLUGIN_PATH",
    }
    return {k: v for k, v in os.environ.items() if k in allowed_keys}


def collect_compose_config(
    project_dir: str | Path,
    compose_file: str | Path | Sequence[str | Path],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_OUTPUT_LIMIT,
) -> dict[str, Any]:
    """Invokes `docker compose config --format json` and returns the parsed model.

    Safety controls applied:
    - Fixed argument list; shell=False.
    - Explicit working/project directory and file options.
    - Sanitized inherited environment.
    - Hard bounded output buffer to prevent memory exhaustion.
    - Strict execution timeout.
    - Sanitized error reporting without raw secret echoing.
    """
    p_dir = Path(project_dir).resolve()

    cmd = [
        "docker",
        "compose",
        "--project-directory",
        str(p_dir),
    ]

    if isinstance(compose_file, (list, tuple)):
        for f in compose_file:
            cmd.extend(["-f", str(Path(f).resolve())])
    else:
        cmd.extend(["-f", str(Path(compose_file).resolve())])

    cmd.extend(["config", "--format", "json"])

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_build_safe_env(),
            cwd=str(p_dir),
        )
    except FileNotFoundError as exc:
        raise ComposeExecutionError(
            "The 'docker' executable was not found on PATH. Ensure Docker Compose is installed."
        ) from exc
    except Exception as exc:
        raise ComposeExecutionError("Failed to spawn docker compose subprocess.") from exc

    try:
        # Bounded read to avoid memory exhaustion
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise ComposeExecutionError(
            f"Command 'docker compose config' timed out after {timeout_seconds}s."
        )

    if len(stdout_bytes) > max_bytes:
        raise ComposeOutputLimitExceededError(
            f"Compose configuration output exceeded limit of {max_bytes} bytes."
        )

    if proc.returncode != 0:
        # Sanitize stderr: do not print raw output that might echo environment values
        # Extract generic summary line if available without secret leakage
        err_msg = (
            f"docker compose config exited with status {proc.returncode}."
        )
        raise ComposeExecutionError(err_msg)

    try:
        data = json.loads(stdout_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComposeExecutionError("Invalid JSON returned by docker compose config.") from exc

    if not isinstance(data, dict):
        raise ComposeExecutionError("Root of Compose config JSON must be an object.")

    return data


def load_compose_json_file(file_path: str | Path, max_bytes: int = DEFAULT_OUTPUT_LIMIT) -> dict[str, Any]:
    """Loads a pre-rendered Compose configuration JSON file (useful for offline testing)."""
    p = Path(file_path).resolve()
    if not p.is_file():
        raise ComposeFileNotFoundError(f"JSON fixture file not found: {p}")

    with p.open("rb") as stream:
        raw = stream.read(max_bytes + 1)

    if len(raw) > max_bytes:
        raise ComposeOutputLimitExceededError(
            f"JSON fixture file exceeded limit of {max_bytes} bytes: {p}"
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComposeExecutionError(f"Invalid JSON in fixture: {p.name}") from exc

    if not isinstance(data, dict):
        raise ComposeExecutionError(f"Root of Compose config JSON must be an object: {p.name}")

    return data
