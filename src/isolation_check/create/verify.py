"""Verification of merged Compose model after environment generation."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from isolation_check.collect.compose import _build_safe_env
from isolation_check.create.generator import GeneratedEnvironmentPlan
from isolation_check.create.spec import CreateSpec


class VerificationError(Exception):
    """Raised when generated Compose configuration fails post-creation verification."""


def verify_generated_environment(
    spec: CreateSpec,
    plan: GeneratedEnvironmentPlan,
) -> dict[str, Any]:
    """Runs Compose config on the worktree with all base files and override file.

    Verifies:
    1. Project name matches intended plan.
    2. PostgreSQL service mounts the dedicated volume.
    3. Dedicated volume is defined with the expected unique name.
    4. Application service has the database environment variable set to connect to PostgreSQL.
    5. Host ports on PostgreSQL are absent if publish_db is False.
    """
    wt = spec.worktree_path
    override_file = wt / plan.override_compose_rel_path

    cmd = ["docker", "compose"]
    for f in spec.compose_files:
        cmd.extend(["-f", f])
    cmd.extend(["-f", str(override_file), "config", "--format", "json"])

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(wt),
            capture_output=True,
            text=True,
            env=_build_safe_env(),
            timeout=30.0,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VerificationError("Docker CLI ('docker') was not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise VerificationError("Compose verification timed out after 30 seconds.") from exc

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"Exited with code {proc.returncode}"
        raise VerificationError(f"Merged Compose configuration is invalid: {err}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"Failed to parse merged Compose JSON output: {exc}") from exc

    # 1. Verify project name
    if data.get("name") != plan.project_name:
        raise VerificationError(
            f"Merged Compose project name mismatch: expected '{plan.project_name}', got '{data.get('name')}'."
        )

    # 2. Verify PostgreSQL volume
    services = data.get("services") or {}
    db_service_cfg = services.get(spec.db_service) or {}
    mounts = db_service_cfg.get("volumes") or []

    found_dedicated_mount = False
    for mount in mounts:
        if isinstance(mount, dict):
            src = mount.get("source")
            target = mount.get("target") or ""
            # Check if PostgreSQL data directory mounts dedicated volume
            if target.startswith("/var/lib/postgresql") or target.startswith("/data"):
                # Check top-level volumes definition for this source
                top_vols = data.get("volumes") or {}
                vol_entry = top_vols.get(src) if isinstance(top_vols, dict) else None
                resolved_name = (vol_entry.get("name") if isinstance(vol_entry, dict) else None) or src

                if resolved_name == plan.dedicated_volume:
                    found_dedicated_mount = True
                else:
                    raise VerificationError(
                        f"PostgreSQL data mount target '{target}' resolves to volume '{resolved_name}' "
                        f"instead of dedicated volume '{plan.dedicated_volume}'."
                    )

    if not found_dedicated_mount:
        raise VerificationError(
            f"PostgreSQL service '{spec.db_service}' does not mount dedicated volume '{plan.dedicated_volume}'."
        )

    # 3. Verify application service database URL
    app_service_cfg = services.get(spec.app_service) or {}
    app_env = app_service_cfg.get("environment") or {}
    env_dict: dict[str, str] = {}
    if isinstance(app_env, dict):
        env_dict = {str(k): str(v) for k, v in app_env.items()}
    elif isinstance(app_env, list):
        for item in app_env:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                env_dict[k.strip()] = v.strip()

    if spec.db_env_var not in env_dict:
        raise VerificationError(
            f"Application service '{spec.app_service}' is missing expected database variable '{spec.db_env_var}'."
        )

    db_url_val = env_dict[spec.db_env_var]
    expected_host_part = f"@{spec.db_service}:5432"
    if expected_host_part not in db_url_val and f"@{spec.db_service}/" not in db_url_val:
        raise VerificationError(
            f"Application database variable '{spec.db_env_var}' does not connect to container "
            f"service '{spec.db_service}' (value: [REDACTED])."
        )

    # 4. Verify host ports
    if not spec.publish_db:
        db_ports = db_service_cfg.get("ports")
        if db_ports:
            raise VerificationError(
                f"PostgreSQL service '{spec.db_service}' still has published host ports despite --publish-db=false: {db_ports}"
            )

    return data
