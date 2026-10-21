"""Docker Compose capability detection."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from isolation_check.collect.compose import _build_safe_env


class ComposeCapabilityError(Exception):
    """Raised when the installed Docker Compose CLI lacks required capabilities."""


def verify_compose_capabilities() -> None:
    """Verifies that the installed Docker Compose CLI supports !override and !reset YAML tags.

    Refuses unsupported Docker Compose versions with a clear explanation.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        base = tmp_dir / "compose.yaml"
        override = tmp_dir / "compose.override.yaml"

        base.write_text(
            "services:\n"
            "  test:\n"
            "    image: test\n"
            "    ports:\n"
            "      - '80:80'\n",
            encoding="utf-8",
        )
        override.write_text(
            "services:\n"
            "  test:\n"
            "    ports: !override\n"
            "      - '127.0.0.1:0:80'\n",
            encoding="utf-8",
        )

        cmd = [
            "docker",
            "compose",
            "--project-directory",
            str(tmp_dir),
            "-f",
            str(base),
            "-f",
            str(override),
            "config",
            "--format",
            "json",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=_build_safe_env(),
                timeout=10.0,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ComposeCapabilityError(
                "Docker Compose CLI ('docker') was not found on PATH. Ensure Docker Compose is installed."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ComposeCapabilityError(
                "Docker Compose capability check timed out."
            ) from exc

        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            raise ComposeCapabilityError(
                f"The installed Docker Compose version does not support required sequence override tags (!override / !reset): {err}. "
                "Please upgrade Docker Compose to v2.20 or newer to use isolation-check."
            )
