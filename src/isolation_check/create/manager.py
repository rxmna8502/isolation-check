"""Execution orchestration for dry-run and create commands."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from isolation_check.create.generator import (
    GeneratedEnvironmentPlan,
    plan_generated_environment,
    write_generated_environment_files,
)
from isolation_check.create.spec import CreateSpec, SpecError
from isolation_check.create.validate import ValidationError, validate_spec_and_config
from isolation_check.create.worktree import (
    create_worktree,
    delete_branch,
    exclude_in_worktree,
    has_uncommitted_changes,
    remove_worktree,
    resolve_commit_hash,
)
from isolation_check.runtime.concurrency import (
    ConcurrencyLockError,
    acquire_environment_lock,
    atomic_write_text,
)
from isolation_check.version import get_package_version


def _get_managed_environments_dir(repo: Path) -> Path:
    d = repo / ".isolation-check" / "environments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_managed_environment(
    spec: CreateSpec,
    plan: GeneratedEnvironmentPlan,
    base_commit: str,
) -> Path:
    env_dir = _get_managed_environments_dir(spec.repo)
    rec_file = env_dir / f"{spec.name}.json"
    data = {
        "name": spec.name,
        "branch": spec.branch,
        "worktree_path": str(spec.worktree_path),
        "compose_project_name": plan.project_name,
        "dedicated_volume": plan.dedicated_volume,
        "base_ref": spec.base_ref,
        "base_commit": base_commit,
        "manifest_file": str(spec.worktree_path / plan.manifest_rel_path),
        "app_service": spec.app_service,
        "db_service": spec.db_service,
        "app_port": plan.app_port,
        "compose_files": spec.compose_files,
        "override_file": plan.override_compose_rel_path,
        "env_file": plan.env_rel_path,
    }
    atomic_write_text(rec_file, json.dumps(data, indent=2))
    return rec_file


def execute_dry_run(spec: CreateSpec) -> int:
    """Performs full input validation and prints the proposed plan without making changes."""
    try:
        validated = validate_spec_and_config(spec)
        raw_compose = validated.raw_compose
        resolved_app_port = validated.app_port
        base_commit = resolve_commit_hash(spec.repo, spec.base_ref)
        plan = plan_generated_environment(spec, raw_compose, resolved_app_port)
    except (SpecError, ValidationError, Exception) as exc:
        if spec.format == "json":
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "dry_run": True,
                "environment": spec.name,
                "valid": False,
                "error": str(exc),
            }, indent=2))
        else:
            sys.stderr.write(f"Validation Error: {exc}\n")
        return 2

    if spec.format == "json":
        data = {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "dry_run": True,
            "environment": spec.name,
            "valid": True,
            "proposed": {
                "source_repo": str(spec.repo),
                "base_ref": spec.base_ref,
                "base_commit": base_commit,
                "branch": spec.branch,
                "worktree_path": str(spec.worktree_path),
                "compose_files": spec.compose_files,
                "compose_project_name": plan.project_name,
                "app_port": plan.app_port,
                "binding_policy": "loopback-only",
                "bind_address": "127.0.0.1",
                "db_storage": {
                    "service": spec.db_service,
                    "volume_name": plan.dedicated_volume,
                    "mount_target": "/var/lib/postgresql/data",
                },
                "db_connection": {
                    "app_service": spec.app_service,
                    "env_var": spec.db_env_var,
                    "url_redacted": plan.redacted_database_url,
                },
                "host_ports_published": spec.publish_db,
            },
            "generated_files": [
                plan.override_compose_rel_path,
                plan.env_rel_path,
                plan.manifest_rel_path,
            ],
        }
        print(json.dumps(data, indent=2))
    else:
        compose_files_str = ", ".join(spec.compose_files)
        port_desc = (
            "Published host ports retained (--publish-db)"
            if spec.publish_db
            else "PostgreSQL host ports disabled (internal container network only)"
        )
        print("========================================================================")
        print("isolation-check create -- DRY RUN")
        print(f"Target Environment: {spec.name}")
        print("Scope: local Linux, Docker Compose, PostgreSQL")
        print("========================================================================")
        print()
        print("Plan:")
        print(f"  Repository:        {spec.repo}")
        print(f"  Base revision:     {spec.base_ref} ({base_commit[:8]})")
        print(f"  Proposed branch:   {spec.branch}")
        print(f"  Worktree location: {spec.worktree_path}")
        print(f"  Compose files:     {compose_files_str}")
        print(f"  App service:       {spec.app_service} (container port :{plan.app_port})")
        print(f"  PostgreSQL service:{spec.db_service}")
        print(f"  Database env var:  {spec.db_env_var}")
        print()
        print("Proposed Configuration Changes:")
        print(f"  Compose project:   {plan.project_name}")
        print(f"  App host binding:  127.0.0.1 loopback-only (assigned dynamically at runtime)")
        print(f"  Database storage:  Dedicated volume '{plan.dedicated_volume}'")
        print(f"  Host ports:        {port_desc}")
        print(f"  App database URL:  {plan.redacted_database_url}")
        print()
        print("Generated Files to Create:")
        print(f"  - {spec.worktree_path / plan.override_compose_rel_path}")
        print(f"  - {spec.worktree_path / plan.env_rel_path}")
        print(f"  - {spec.worktree_path / plan.manifest_rel_path}")
        print()
        print("Status: VALIDATION SUCCESSFUL")
        print("Dry run complete. No files, worktrees, branches, or Docker resources were created or modified.")
        print("========================================================================")

    return 0


def execute_create(spec: CreateSpec) -> int:
    """Executes environment creation with rollback and recovery tracking."""
    # 1. Pre-flight validation
    try:
        validated = validate_spec_and_config(spec)
        raw_compose = validated.raw_compose
        resolved_app_port = validated.app_port
        base_commit = resolve_commit_hash(spec.repo, spec.base_ref)
        plan = plan_generated_environment(spec, raw_compose, resolved_app_port)
    except (SpecError, ValidationError, Exception) as exc:
        if spec.format == "json":
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "error": str(exc),
            }, indent=2))
        else:
            sys.stderr.write(f"Validation Error: {exc}\n")
        return 2

    # 2. Concurrency lock and resource tracking for safe failure recovery
    created_resources: list[tuple[str, Any]] = []
    env_dir = _get_managed_environments_dir(spec.repo)
    lock_file = env_dir / f".{spec.name}.create.lock"

    try:
        with acquire_environment_lock(lock_file, spec.name, "create"):
            try:
                # Step A: Create worktree
                create_worktree(spec.repo, spec.branch, spec.worktree_path, spec.base_ref)
                created_resources.append(("worktree", spec.worktree_path))
                created_resources.append(("branch", spec.branch))

                # Step B: Exclude .isolation-check from git tracking in worktree
                exclude_in_worktree(spec.worktree_path, ".isolation-check/")

                # Step C: Write generated environment files
                created_files = write_generated_environment_files(spec, plan, raw_compose, base_commit)
                for f in created_files:
                    created_resources.append(("file", f))

                # Step D: Verify merged Compose model
                from isolation_check.create.verify import verify_generated_environment
                verify_generated_environment(spec, plan)

                # Step E: Register managed environment record in repo
                rec_file = register_managed_environment(spec, plan, base_commit)
                created_resources.append(("record", rec_file))

            except Exception as exc:
                # Error handling with change-preservation guardrails
                err_msg = str(exc)

                # Check if worktree acquired user changes
                user_changes = has_uncommitted_changes(spec.worktree_path)
                if user_changes:
                    # Never delete user work!
                    sys.stderr.write(f"\nError: Environment creation failed: {err_msg}\n")
                    sys.stderr.write(
                        "SAFETY WARNING: Worktree was PRESERVED because uncommitted user changes were detected.\n"
                        f"  Worktree path: {spec.worktree_path}\n"
                        f"  Branch:        {spec.branch}\n"
                        "Safe Recovery Steps:\n"
                        f"  1. Inspect worktree: cd {spec.worktree_path}\n"
                        f"  2. If safe to discard: git worktree remove --force {spec.worktree_path} && git branch -D {spec.branch}\n"
                    )
                else:
                    # Clean rollback of untracked resources created during this invocation
                    for res_type, res_val in reversed(created_resources):
                        try:
                            if res_type == "file" and isinstance(res_val, Path) and res_val.is_file():
                                res_val.unlink()
                            elif res_type == "record" and isinstance(res_val, Path) and res_val.is_file():
                                res_val.unlink()
                            elif res_type == "worktree" and isinstance(res_val, Path):
                                remove_worktree(spec.repo, res_val, force=True)
                            elif res_type == "branch" and isinstance(res_val, str):
                                delete_branch(spec.repo, res_val, force=True)
                        except Exception:
                            pass

                    if spec.format == "json":
                        print(json.dumps({
                            "schema_version": "1.0",
                            "tool_version": get_package_version(),
                            "success": False,
                            "error": err_msg,
                            "rolled_back": True,
                        }, indent=2))
                    else:
                        sys.stderr.write(f"Error: Environment creation failed: {err_msg}\n")
                        sys.stderr.write("Partially created resources were safely cleaned up.\n")

                return 2

    except ConcurrencyLockError as exc:
        if spec.format == "json":
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "success": False,
                "error": str(exc),
            }, indent=2))
        else:
            sys.stderr.write(f"Concurrency Error: {exc}\n")
        return 2

    # 3. Successful creation output
    if spec.format == "json":
        data = {
            "schema_version": "1.0",
            "tool_version": get_package_version(),
            "success": True,
            "environment": spec.name,
            "branch": spec.branch,
            "worktree_path": str(spec.worktree_path),
            "compose_project_name": plan.project_name,
            "dedicated_volume": plan.dedicated_volume,
            "manifest_file": str(spec.worktree_path / plan.manifest_rel_path),
            "app_port": plan.app_port,
            "database_url_redacted": plan.redacted_database_url,
        }
        print(json.dumps(data, indent=2))
    else:
        print("========================================================================")
        print("isolation-check create -- ENVIRONMENT CREATED")
        print(f"Environment: {spec.name}")
        print("========================================================================")
        print()
        print("Created Worktree:")
        print(f"  Location:        {spec.worktree_path}")
        print(f"  Branch:          {spec.branch}")
        print(f"  Base revision:   {spec.base_ref} ({base_commit[:8]})")
        print()
        print("Generated Compose Configuration:")
        print(f"  Project name:    {plan.project_name}")
        print(f"  Dedicated volume:{plan.dedicated_volume}")
        print(f"  App container :  port :{plan.app_port} (loopback-only on 127.0.0.1)")
        print(f"  Override file:   {spec.worktree_path / plan.override_compose_rel_path}")
        print(f"  Private env:     {spec.worktree_path / plan.env_rel_path}")
        print(f"  Manifest:        {spec.worktree_path / plan.manifest_rel_path}")
        print(f"  Database URL:    {plan.redacted_database_url}")
        print()
        print("Verification:")
        print("  Merged Compose configuration verified successfully.")
        print("  PostgreSQL storage and database connection isolated to this environment.")
        print()
        print("To start this environment:")
        print(f"  isolation-check up {spec.name}")
        print("Or directly via Docker Compose:")
        compose_cmd_files = " ".join([f"-f {f}" for f in spec.compose_files])
        print(f"  cd {spec.worktree_path}")
        print(f"  docker compose {compose_cmd_files} -f {plan.override_compose_rel_path} up -d")
        print("========================================================================")

    return 0
