"""Concurrency controls and atomic file operations for managed environments."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys
import time
from typing import Any, Generator
import uuid


class ConcurrencyLockError(Exception):
    """Raised when an operation cannot proceed because another active process holds the lock."""


def atomic_write_text(target_path: Path | str, content: str, mode: int | None = None) -> None:
    """Writes text to target_path atomically using a temporary file and atomic replace.

    Guarantees that readers never observe partially written or corrupted files.

    Permissions:
    - On POSIX platforms, if `mode` is specified (e.g. 0o600), the temporary file is created
      with restrictive permissions from the initial open call, preventing a window of broad
      or world-readable access, and the mode is preserved across atomic replacement.
    - Note on Windows: File permissions in Windows NTFS are governed by Access Control Lists (ACLs)
      rather than POSIX octal modes. In Python on Windows, `os.chmod` only toggles read-only flags
      and cannot restrict read access to the file owner. On POSIX systems, `mode` is strictly
      enforced from creation through atomic replacement.
    """
    target = Path(target_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Place temp file in the same directory to ensure it is on the same filesystem for os.replace
    temp_name = f".{target.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:8]}"
    temp_path = target.parent / temp_name

    # Use restrictive permissions on initial file creation
    open_flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
    open_mode = mode if mode is not None else 0o666

    fd = os.open(str(temp_path), open_flags, open_mode)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        if mode is not None and sys.platform != "win32":
            try:
                os.chmod(temp_path, mode)
            except OSError:
                pass

        # Atomic replacement
        os.replace(temp_path, target)

        if mode is not None and sys.platform != "win32":
            try:
                os.chmod(target, mode)
            except OSError:
                pass
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def get_process_start_time(pid: int) -> str | None:
    """Returns a unique start-time identifier for a process to detect PID reuse, or None if unavailable."""
    if pid <= 0:
        return None

    if sys.platform == "linux":
        try:
            stat_path = Path(f"/proc/{pid}/stat")
            if stat_path.is_file():
                content = stat_path.read_text(encoding="utf-8", errors="replace")
                rparen = content.rfind(")")
                if rparen != -1:
                    fields = content[rparen + 1:].strip().split()
                    # Field index 19 corresponds to starttime (22nd field in /proc/[pid]/stat)
                    if len(fields) > 19:
                        return fields[19]
        except Exception:
            return None

    elif sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                class FILETIME(ctypes.Structure):
                    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

                create_time = FILETIME()
                exit_time = FILETIME()
                kernel_time = FILETIME()
                user_time = FILETIME()
                if kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(create_time),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                ):
                    val = (create_time.dwHighDateTime << 32) | create_time.dwLowDateTime
                    kernel32.CloseHandle(handle)
                    return str(val)
                kernel32.CloseHandle(handle)
        except Exception:
            return None

    return None


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


def is_lock_abandoned(lock_data: dict[str, Any]) -> tuple[bool, str]:
    """Determines whether an existing lock is proven abandoned.

    Returns:
        (is_abandoned, reason)
    """
    lock_pid = lock_data.get("pid")
    lock_host = lock_data.get("host")
    lock_start_time = lock_data.get("process_start_time")
    current_host = socket.gethostname()

    # Rule 1: Different host cannot be safely inspected or verified as dead
    if lock_host and lock_host != current_host:
        return False, f"Lock is held on a different host ('{lock_host}'; current host is '{current_host}')."

    # Rule 2: Non-integer or missing PID
    if not isinstance(lock_pid, int) or lock_pid <= 0:
        return False, "Lock contains invalid or missing process ID (PID)."

    # Rule 3: Check if PID is alive on current host
    if not _is_pid_alive(lock_pid):
        return True, f"Process PID {lock_pid} is no longer running on this host."

    # Rule 4: PID is alive -> check for PID reuse via start-time verification
    if lock_start_time is not None:
        current_start_time = get_process_start_time(lock_pid)
        if current_start_time is not None and current_start_time != lock_start_time:
            return (
                True,
                f"Process PID {lock_pid} has a mismatched start identity "
                f"({current_start_time} != {lock_start_time}); original owner terminated.",
            )
        if current_start_time is not None and current_start_time == lock_start_time:
            return False, f"Owner process PID {lock_pid} is actively running."

    # If start time could not be queried or matches, live PID must never be stolen based on age
    return False, f"Owner process PID {lock_pid} is currently alive."


@contextlib.contextmanager
def acquire_environment_lock(
    lock_path: Path | str,
    env_name: str,
    command: str,
    timeout: float = 0.0,
    poll_interval: float = 0.05,
) -> Generator[Path, None, None]:
    """Acquires an exclusive lifecycle lock file with robust owner identity and stale-lock recovery.

    A lock is NEVER reclaimed solely based on elapsed time while its verified owner process is alive.

    Args:
        lock_path: Path to the lock file.
        env_name: Name of the environment being locked.
        command: Command name or description requesting the lock.
        timeout: Maximum duration (seconds) the caller will wait to acquire the lock.
        poll_interval: Interval (seconds) between acquisition attempts.

    Raises:
        ConcurrencyLockError: If the lock cannot be acquired within the timeout.
    """
    path = Path(lock_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    acquired = False
    lock_fd: int | None = None
    owner_token = uuid.uuid4().hex
    host = socket.gethostname()
    current_pid = os.getpid()
    proc_start_time = get_process_start_time(current_pid)

    start_wait = time.monotonic()
    last_lock_data: dict[str, Any] = {}
    last_abandon_reason = ""

    while True:
        try:
            # Atomic exclusive file creation
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            lock_fd = os.open(str(path), flags, 0o600)
            acquired = True
            break
        except FileExistsError:
            # Read existing lock data
            lock_data: dict[str, Any] = {}
            for _ in range(2):
                try:
                    raw = path.read_text(encoding="utf-8")
                    if raw.strip():
                        lock_data = json.loads(raw)
                        break
                except Exception:
                    time.sleep(0.02)

            last_lock_data = lock_data
            abandoned, reason = is_lock_abandoned(lock_data)
            last_abandon_reason = reason

            if abandoned:
                # Safely reclaim abandoned lock
                try:
                    path.unlink(missing_ok=True)
                    continue
                except OSError:
                    pass

            elapsed = time.monotonic() - start_wait
            if elapsed >= timeout:
                # Timed out waiting for an active lock.
                # Do NOT mutate the existing active lock!
                held_pid = lock_data.get("pid")
                held_host = lock_data.get("host") or "unknown"
                held_cmd = lock_data.get("command") or "unknown"
                held_time = lock_data.get("created_at") or "unknown"

                msg = [
                    f"Environment '{env_name}' is locked by PID {held_pid} on host '{held_host}'",
                    f"(command: '{held_cmd}', acquired at {held_time}).",
                    f"Existing lock cannot be proven abandoned: {reason}",
                    "",
                    "Recovery procedure:",
                    f"  1. Verify if process PID {held_pid} is still actively running on host '{held_host}'.",
                    f"  2. If the process terminated abnormally without releasing the lock, manually remove:",
                    f"       {path}",
                ]
                raise ConcurrencyLockError("\n".join(msg))

            time.sleep(poll_interval)

    if not acquired or lock_fd is None:
        raise ConcurrencyLockError(f"Could not acquire lock for environment '{env_name}'.")

    try:
        # Write robust owner identity into opened descriptor
        payload = json.dumps({
            "pid": current_pid,
            "process_start_time": proc_start_time,
            "host": host,
            "owner_token": owner_token,
            "command": command,
            "environment": env_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
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

        # Use lock-owner token to ensure we NEVER delete another process's lock
        try:
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if data.get("owner_token") == owner_token:
                    path.unlink(missing_ok=True)
        except Exception:
            pass
