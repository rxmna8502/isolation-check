"""Concurrency controls and atomic file operations for managed environments."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Generator
import uuid

DEFAULT_STALE_LOCK_TIMEOUT_SECONDS = 120.0


class ConcurrencyLockError(Exception):
    """Raised when an operation cannot proceed because another active process holds the lock."""


def atomic_write_text(target_path: Path | str, content: str, mode: int | None = None) -> None:
    """Writes text to target_path atomically using a temporary file and atomic replace.

    Guarantees that readers never observe partially written or corrupted files.
    """
    target = Path(target_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Place temp file in the same directory to ensure it is on the same filesystem for os.replace
    temp_name = f".{target.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:8]}"
    temp_path = target.parent / temp_name

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        if mode is not None:
            try:
                os.chmod(temp_path, mode)
            except OSError:
                pass

        # Atomic replacement
        os.replace(temp_path, target)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def _is_pid_alive(pid: int) -> bool:
    """Checks if a process ID is currently alive on the system."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if process != 0:
            kernel32.CloseHandle(process)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError as exc:
            import errno
            if exc.errno == errno.ESRCH:
                return False
            # EPERM means the process exists but is owned by another user
            return exc.errno == errno.EPERM


@contextlib.contextmanager
def acquire_environment_lock(
    lock_path: Path | str,
    env_name: str,
    command: str,
    stale_timeout: float = DEFAULT_STALE_LOCK_TIMEOUT_SECONDS,
) -> Generator[Path, None, None]:
    """Acquires an exclusive lifecycle lock file with stale-lock detection and recovery.

    Raises ConcurrencyLockError if another active process is currently holding the lock.
    """
    path = Path(lock_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    acquired = False
    lock_fd = None

    for attempt in range(2):
        try:
            # Atomic exclusive file creation
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            lock_fd = os.open(str(path), flags, 0o600)
            acquired = True
            break
        except FileExistsError:
            # Check if existing lock is stale
            is_stale = False
            lock_data: dict = {}

            try:
                raw = path.read_text(encoding="utf-8")
                lock_data = json.loads(raw)
            except Exception:
                # Corrupted or unreadable lock file is treated as potentially stale if age > timeout
                pass

            lock_pid = lock_data.get("pid")
            acquired_at_str = lock_data.get("acquired_at")
            age = float("inf")
            if acquired_at_str:
                try:
                    acquired_at = datetime.fromisoformat(acquired_at_str)
                    age = (datetime.now(timezone.utc) - acquired_at).total_seconds()
                except Exception:
                    pass

            # Stale condition 1: PID is no longer alive
            if isinstance(lock_pid, int) and not _is_pid_alive(lock_pid):
                is_stale = True
            # Stale condition 2: Lock exceeded timeout
            elif age > stale_timeout:
                is_stale = True

            if is_stale and attempt == 0:
                # Recover from stale lock
                try:
                    path.unlink(missing_ok=True)
                    continue
                except OSError:
                    pass

            # If not stale or second attempt failed, reject operation
            held_by = f"PID {lock_pid}" if lock_pid else "another process"
            held_cmd = f" running '{lock_data.get('command')}'" if lock_data.get("command") else ""
            raise ConcurrencyLockError(
                f"Environment '{env_name}' is locked by {held_by}{held_cmd}. "
                "Concurrent lifecycle operations on the same environment are prevented."
            )

    if not acquired or lock_fd is None:
        raise ConcurrencyLockError(f"Could not acquire lock for environment '{env_name}'.")

    try:
        # Write lock payload into opened descriptor
        payload = json.dumps({
            "pid": os.getpid(),
            "command": command,
            "environment": env_name,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2).encode("utf-8")
        os.write(lock_fd, payload)
        os.close(lock_fd)
        lock_fd = None

        yield path
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
