"""Ownership and path validation for runtime commands."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from isolation_check.collect.compose import _build_safe_env


class OwnershipError(Exception):
    """Raised when ownership, worktree registration, or path validation fails."""


def _resolve_repo_root(hint_path: Path | None = None) -> Path:
    """Finds the main Git repository root, even when invoked from inside a linked worktree."""
    start_dir = Path(hint_path).resolve() if hint_path else Path.cwd().resolve()
    if not start_dir.exists():
        raise OwnershipError(f"Directory does not exist: {start_dir}")

    try:
        # Check git common directory (in a worktree, this points to main .git)
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(start_dir),
            capture_output=True,
            text=True,
            env=_build_safe_env(),
            check=False,
        )
        if proc.returncode == 0:
            common_dir_str = proc.stdout.strip()
            common_dir = (start_dir / common_dir_str).resolve()
            # If common dir is .git, repo root is its parent
            if common_dir.name == ".git":
                return common_dir.parent
            return common_dir
    except Exception:
        pass

    # Fallback to standard show-toplevel
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start_dir),
            capture_output=True,
            text=True,
            env=_build_safe_env(),
            check=False,
        )
        if proc.returncode == 0:
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass

    # If not inside a git repo, check if current directory or parent contains .isolation-check
    candidate = start_dir
    while candidate.parent != candidate:
        if (candidate / ".isolation-check" / "environments").is_dir():
            return candidate
        candidate = candidate.parent

    raise OwnershipError(f"Could not locate Git repository root from '{start_dir}'.")


def _get_registered_git_worktrees(repo_root: Path) -> list[Path]:
    """Retrieves all registered worktrees for the repository using git worktree list."""
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            env=_build_safe_env(),
            check=False,
        )
        if proc.returncode != 0:
            return []
        worktrees: list[Path] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("worktree "):
                wt_path_str = line.split(" ", 1)[1].strip()
                worktrees.append(Path(wt_path_str).resolve())
        return worktrees
    except Exception:
        return []


def locate_and_validate_environment(
    repo_hint: str | Path | None,
    env_name: str,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    """Locates and validates the managed environment record and worktree manifest.

    Returns:
        (record_path, record_data, manifest_path, manifest_data)

    Raises OwnershipError if:
        - The managed environment record does not exist
        - The worktree is missing or not registered with Git
        - The manifest is missing, unowned, or tampered
        - Records disagree on name, project name, worktree path, or compose files
        - Any path attempts traversal or symlink escapes outside worktree boundary
    """
    if not env_name or not env_name.strip():
        raise OwnershipError("Environment name cannot be empty.")

    # 1. Locate repository root
    repo_root = _resolve_repo_root(Path(repo_hint) if repo_hint else None)

    # 2. Load repository-level managed environment record
    rec_file = repo_root / ".isolation-check" / "environments" / f"{env_name}.json"
    if not rec_file.is_file():
        raise OwnershipError(
            f"Managed environment '{env_name}' not found in repository records ({rec_file}). "
            "Only environments previously created with 'isolation-check create' are managed."
        )

    try:
        record_data = json.loads(rec_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise OwnershipError(f"Failed to read managed environment record '{rec_file}': {exc}") from exc

    if not isinstance(record_data, dict):
        raise OwnershipError(f"Invalid environment record format in '{rec_file}'.")

    # 3. Confirm record environment name matches
    if record_data.get("name") != env_name:
        raise OwnershipError(
            f"Record mismatch: record file '{rec_file}' specifies environment '{record_data.get('name')}', expected '{env_name}'."
        )

    # 4. Resolve worktree path canonically
    raw_wt_path = record_data.get("worktree_path")
    if not raw_wt_path:
        raise OwnershipError(f"Environment record '{rec_file}' is missing 'worktree_path'.")

    worktree_path = Path(raw_wt_path).resolve()
    if not worktree_path.is_dir():
        raise OwnershipError(
            f"Worktree directory for environment '{env_name}' does not exist: {worktree_path}"
        )

    # 5. Confirm worktree is still registered with source Git repository
    registered_worktrees = _get_registered_git_worktrees(repo_root)
    # Check if worktree_path is in registered worktrees
    is_registered = any(wt.samefile(worktree_path) if wt.exists() else wt == worktree_path for wt in registered_worktrees)
    if not is_registered:
        raise OwnershipError(
            f"Worktree '{worktree_path}' is not registered with Git repository '{repo_root}'. "
            "Refusing to operate on unlinked or unregistered worktree."
        )

    # 6. Load worktree ownership manifest
    manifest_file = worktree_path / ".isolation-check" / "manifest.json"
    if not manifest_file.is_file():
        raise OwnershipError(
            f"Worktree '{worktree_path}' is not owned by isolation-check (missing ownership manifest: {manifest_file})."
        )

    # Also check that manifest_file declared in record matches
    rec_manifest_path = Path(record_data.get("manifest_file", manifest_file)).resolve()
    if not rec_manifest_path.exists() or not rec_manifest_path.samefile(manifest_file):
        raise OwnershipError(
            f"Manifest path mismatch between record ({rec_manifest_path}) and worktree ({manifest_file})."
        )

    try:
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise OwnershipError(f"Failed to read ownership manifest '{manifest_file}': {exc}") from exc

    if not isinstance(manifest_data, dict):
        raise OwnershipError(f"Invalid ownership manifest format in '{manifest_file}'.")

    # 7. Validate tool ownership and manifest schema
    if manifest_data.get("tool") != "isolation-check":
        raise OwnershipError(
            f"Worktree '{worktree_path}' is not owned by isolation-check (tool is '{manifest_data.get('tool')}')."
        )

    manifest_env = manifest_data.get("environment", {})
    if manifest_env.get("name") != env_name:
        raise OwnershipError(
            f"Ownership manifest mismatch: manifest specifies environment '{manifest_env.get('name')}', expected '{env_name}'."
        )

    # 8. Confirm both records agree on Compose project name
    record_project = record_data.get("compose_project_name")
    manifest_project = manifest_data.get("compose", {}).get("project_name")
    if not record_project or not manifest_project or record_project != manifest_project:
        raise OwnershipError(
            f"Compose project mismatch: record specifies '{record_project}', manifest specifies '{manifest_project}'."
        )

    # 9. Path traversal & symlink escape validation
    compose_info = manifest_data.get("compose", {})
    base_compose_files = compose_info.get("base_compose_files", [])
    if not base_compose_files or not isinstance(base_compose_files, list):
        raise OwnershipError(f"Ownership manifest in '{manifest_file}' specifies no base Compose files.")

    override_file = compose_info.get("override_file")
    if not override_file:
        raise OwnershipError(f"Ownership manifest in '{manifest_file}' is missing 'override_file'.")

    env_file = compose_info.get("env_file")
    if not env_file:
        raise OwnershipError(f"Ownership manifest in '{manifest_file}' is missing 'env_file'.")

    managed_files = manifest_data.get("managed_files", [])

    # Validate all declared paths stay strictly inside worktree_path
    for p_str in list(base_compose_files) + [override_file, env_file] + list(managed_files):
        p = Path(p_str)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (worktree_path / p).resolve()

        # Reject path traversal outside worktree boundary
        try:
            resolved.relative_to(worktree_path)
        except ValueError:
            raise OwnershipError(
                f"Path traversal detected in manifest: path '{p_str}' (resolved: {resolved}) "
                f"escapes worktree boundary '{worktree_path}'."
            )

        if not resolved.exists():
            raise OwnershipError(
                f"Required managed file does not exist: {resolved} (declared as '{p_str}')"
            )

    return rec_file, record_data, manifest_file, manifest_data
