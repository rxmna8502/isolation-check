"""Git worktree operations with disabled hooks and safety guarantees."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


class GitWorktreeError(Exception):
    """Raised when a Git operation fails."""


def _build_safe_env() -> dict[str, str]:
    """Builds a sanitized environment for git invocations."""
    safe_keys = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "GIT_CONFIG_NOSYSTEM",
    }
    return {k: v for k, v in os.environ.items() if k in safe_keys}


def run_git(
    cwd: Path,
    args: list[str],
    timeout_seconds: float = 30.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Executes a git command with repository hooks explicitly disabled.

    Never modifies global or system git configuration.
    """
    cmd = ["git", "-c", "core.hooksPath=/dev/null"] + args
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=_build_safe_env(),
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitWorktreeError("Git executable ('git') was not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitWorktreeError(f"Git command timed out after {timeout_seconds} seconds: {' '.join(cmd)}") from exc

    if check and proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"Exited with code {proc.returncode}"
        raise GitWorktreeError(f"Git command failed: {err}")

    return proc


def is_git_repository(path: Path) -> bool:
    """Checks whether the given path is inside or at the root of a Git repository."""
    if not path.exists():
        return False
    proc = run_git(path, ["rev-parse", "--is-inside-work-tree"], check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def get_repo_root(path: Path) -> Path:
    """Returns the top-level root directory of the Git repository."""
    proc = run_git(path, ["rev-parse", "--show-toplevel"])
    return Path(proc.stdout.strip()).resolve()


def branch_exists(repo: Path, branch: str) -> bool:
    """Returns True if a local branch with the given name already exists."""
    proc = run_git(
        repo,
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    )
    return proc.returncode == 0


def resolve_commit_hash(repo: Path, ref: str) -> str:
    """Resolves a git reference (branch, tag, SHA) to a full commit hash."""
    proc = run_git(repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
    if proc.returncode != 0:
        raise GitWorktreeError(f"Could not resolve base revision '{ref}' in repository {repo}.")
    return proc.stdout.strip()


def create_worktree(repo: Path, branch: str, worktree_path: Path, base_ref: str) -> None:
    """Creates a new worktree at worktree_path on a new branch starting at base_ref.

    Guarantees the current checkout of `repo` is not modified.
    """
    args = ["worktree", "add", "-b", branch, str(worktree_path), base_ref]
    run_git(repo, args)


def has_uncommitted_changes(worktree_path: Path) -> bool:
    """Checks if a worktree has acquired any uncommitted changes from user work."""
    if not worktree_path.exists():
        return False
    proc = run_git(worktree_path, ["status", "--porcelain"], check=False)
    if proc.returncode != 0:
        return False
    # Any tracked or untracked changes in the worktree
    # Filter out our own internal .isolation-check directory if present
    lines = [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip() and not line.strip().endswith(".isolation-check")
        and ".isolation-check/" not in line
    ]
    return len(lines) > 0


def remove_worktree(repo: Path, worktree_path: Path, force: bool = False) -> None:
    """Removes a git worktree."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    run_git(repo, args, check=False)


def delete_branch(repo: Path, branch: str, force: bool = False) -> None:
    """Deletes a local branch."""
    flag = "-D" if force else "-d"
    run_git(repo, ["branch", flag, branch], check=False)


def exclude_in_worktree(worktree_path: Path, pattern: str) -> None:
    """Adds a pattern to the git exclude file so it is never tracked."""
    dirs_to_update: set[Path] = set()
    for cmd in (["rev-parse", "--git-common-dir"], ["rev-parse", "--git-dir"]):
        proc = run_git(worktree_path, cmd, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            dirs_to_update.add(Path(proc.stdout.strip()).resolve())

    for target_git_dir in dirs_to_update:
        info_dir = target_git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        exclude_file = info_dir / "exclude"

        existing: list[str] = []
        if exclude_file.is_file():
            existing = exclude_file.read_text(encoding="utf-8").splitlines()

        if pattern not in existing and pattern.strip("/") not in existing:
            with open(exclude_file, "a", encoding="utf-8") as f:
                f.write(f"\n{pattern}\n")
