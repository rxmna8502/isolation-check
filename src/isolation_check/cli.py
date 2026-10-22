"""Command-line interface for isolation-check."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from isolation_check.collect.compose import (
    ComposeCollectorError,
    collect_compose_config,
    load_compose_json_file,
    resolve_compose_target,
)
from isolation_check.models import EngineScope, Finding, HostScope
from isolation_check.normalize.compose import normalize_compose_model
from isolation_check.report import build_report, format_json_report, format_text_report
from isolation_check.rules.ports import check_ports
from isolation_check.rules.volumes import check_volumes
from isolation_check.version import get_package_version


def build_parser() -> argparse.ArgumentParser:
    """Builds and returns the top-level argument parser for isolation-check."""
    parser = argparse.ArgumentParser(
        prog="isolation-check",
        description="Inspect and prepare isolated local development environments.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {get_package_version()}",
    )

    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser(
        "check",
        help="Compare two Docker Compose configurations for resource collisions.",
    )
    check_parser.add_argument(
        "left",
        nargs="?",
        help="Path to first Compose environment directory, compose file, or JSON fixture.",
    )
    check_parser.add_argument(
        "right",
        nargs="?",
        help="Path to second Compose environment directory, compose file, or JSON fixture.",
    )
    check_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat UNKNOWN findings as exit code 2 if there is no FAIL.",
    )
    check_parser.add_argument(
        "--engine-scope",
        default="local-docker-engine",
        help="Explicit identifier for the Docker engine scope (default: local-docker-engine).",
    )
    check_parser.add_argument(
        "--no-shared-engine",
        action="store_true",
        help="Do not assume a shared Docker engine scope (treat scopes as distinct/unknown).",
    )
    check_parser.add_argument(
        "--host-scope",
        default="local-host",
        help="Explicit identifier for the host machine scope (default: local-host).",
    )
    check_parser.add_argument(
        "--no-shared-host",
        action="store_true",
        help="Do not assume a shared host scope (treat host scopes as distinct/unknown).",
    )

    create_parser = subparsers.add_parser(
        "create",
        help="Safely prepare a Git worktree with a generated Docker Compose environment.",
    )
    create_parser.add_argument(
        "name",
        help="Name of the environment to create (e.g. feature-payments).",
    )
    create_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and configuration and display proposed plan without making changes.",
    )
    create_parser.add_argument(
        "--repo",
        help="Source Git repository path (default: current working directory).",
    )
    create_parser.add_argument(
        "--base-ref",
        help="Base git revision/commit/branch to branch from (default: HEAD).",
    )
    create_parser.add_argument(
        "--branch",
        help="Name of the branch to create in the worktree (default: matches environment name).",
    )
    create_parser.add_argument(
        "--worktree-path",
        help="Path where the worktree should be created (default: sibling of repo).",
    )
    create_parser.add_argument(
        "-f",
        "--compose-file",
        action="append",
        dest="compose_files",
        help="Compose file(s) in merge order (can be specified multiple times).",
    )
    create_parser.add_argument(
        "--app-service",
        help="Explicitly configured application service name.",
    )
    create_parser.add_argument(
        "--db-service",
        help="Explicitly configured PostgreSQL service name.",
    )
    create_parser.add_argument(
        "--db-env-var",
        help="Application's database connection environment variable name (e.g. DATABASE_URL).",
    )
    create_parser.add_argument(
        "--app-port",
        type=int,
        help="Application container target port (e.g. 3000).",
    )
    create_parser.add_argument(
        "--publish-db",
        action="store_true",
        default=None,
        help="Publish PostgreSQL database to the host (default: false, internal container network only).",
    )
    create_parser.add_argument(
        "--config",
        help="Path to TOML configuration file specifying environment creation parameters.",
    )
    create_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )

    up_parser = subparsers.add_parser(
        "up",
        help="Start a managed environment's Docker Compose project.",
    )
    up_parser.add_argument(
        "environment",
        help="Name of the managed environment to start.",
    )
    up_parser.add_argument(
        "--repo",
        help="Path to source Git repository or worktree (default: current directory).",
    )
    up_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output result as structured JSON.",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Query runtime state and health of a managed environment.",
    )
    status_parser.add_argument(
        "environment",
        help="Name of the managed environment.",
    )
    status_parser.add_argument(
        "--repo",
        help="Path to source Git repository or worktree (default: current directory).",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output status as structured JSON.",
    )

    down_parser = subparsers.add_parser(
        "down",
        help="Stop a managed environment while preserving data volumes.",
        description="Stop a managed environment while preserving data volumes.",
    )
    down_parser.add_argument(
        "environment",
        help="Name of the managed environment to stop.",
    )
    down_parser.add_argument(
        "--repo",
        help="Path to source Git repository or worktree (default: current directory).",
    )
    down_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output result as structured JSON.",
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    raw_args = list(argv if argv is not None else sys.argv[1:])

    # Default to 'check' subcommand if running `isolation-check <left> <right>` directly
    if raw_args and not raw_args[0].startswith("-") and raw_args[0] not in (
        "check", "create", "up", "status", "down", "help"
    ):
        raw_args.insert(0, "check")

    parser = build_parser()
    return parser.parse_args(raw_args)


def is_interactive() -> bool:
    """Returns True if standard input and output are attached to an interactive TTY."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _load_env_config(path_str: str) -> tuple[dict, str, str]:
    """Loads raw Compose config for an environment path.

    Returns (raw_config_dict, label, root_str).
    Detects if the input is already a JSON fixture or needs Docker Compose resolution.
    """
    p = Path(path_str).resolve()
    if not p.exists():
        raise ComposeCollectorError(f"Target path does not exist: {path_str}")

    if p.is_file() and p.suffix.lower() == ".json":
        # Pre-rendered Compose JSON fixture (offline mode)
        raw_config = load_compose_json_file(p)
        label = p.stem
        root = str(p.parent)
        return raw_config, label, root

    # Standard Docker Compose environment
    project_dir, compose_file = resolve_compose_target(p)
    raw_config = collect_compose_config(project_dir, compose_file)
    label = project_dir.name
    root = str(project_dir)
    return raw_config, label, root


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(argv if argv is not None else sys.argv[1:])
    if not raw_args:
        if is_interactive():
            from isolation_check.tui import run_tui
            return run_tui()
        else:
            parser = build_parser()
            parser.print_help()
            return 0

    args = parse_args(raw_args)

    if args.command == "up":
        from isolation_check.runtime.lifecycle import execute_up
        return execute_up(
            env_name=args.environment,
            repo_hint=args.repo,
            output_json=getattr(args, "output_json", False),
        )

    if args.command == "status":
        from isolation_check.runtime.lifecycle import execute_status
        return execute_status(
            env_name=args.environment,
            repo_hint=args.repo,
            output_json=getattr(args, "output_json", False),
        )

    if args.command == "down":
        from isolation_check.runtime.lifecycle import execute_down
        return execute_down(
            env_name=args.environment,
            repo_hint=args.repo,
            output_json=getattr(args, "output_json", False),
        )

    if args.command == "create":
        from isolation_check.create.manager import execute_create, execute_dry_run
        from isolation_check.create.spec import SpecError, load_spec_from_cli

        try:
            spec = load_spec_from_cli(
                name=args.name,
                repo_arg=args.repo,
                base_ref_arg=args.base_ref,
                branch_arg=args.branch,
                worktree_path_arg=args.worktree_path,
                compose_files_arg=args.compose_files,
                app_service_arg=args.app_service,
                db_service_arg=args.db_service,
                db_env_var_arg=args.db_env_var,
                app_port_arg=getattr(args, "app_port", None),
                publish_db_arg=args.publish_db,
                dry_run=args.dry_run,
                output_format=args.format,
                config_path_arg=args.config,
            )
        except SpecError as exc:
            if getattr(args, "format", "text") == "json":
                import json
                print(json.dumps({
                    "schema_version": "1.0",
                    "tool_version": get_package_version(),
                    "success": False,
                    "error": str(exc),
                }, indent=2))
            else:
                sys.stderr.write(f"Configuration Error: {exc}\n")
            return 2

        if spec.dry_run:
            return execute_dry_run(spec)
        return execute_create(spec)

    if not getattr(args, "left", None) or not getattr(args, "right", None):
        sys.stderr.write("Error: Two environment paths ('left' and 'right') are required.\n")
        sys.stderr.write("Usage: isolation-check check <path1> <path2> [--format {text,json}] [--strict]\n")
        return 2

    # Determine declared engine scope
    if args.no_shared_engine:
        left_engine_scope = EngineScope(scope_id=None, basis="unspecified engine scope")
        right_engine_scope = EngineScope(scope_id=None, basis="unspecified engine scope")
    else:
        left_engine_scope = EngineScope(
            scope_id=args.engine_scope,
            basis="explicit shared local-engine assumption",
        )
        right_engine_scope = EngineScope(
            scope_id=args.engine_scope,
            basis="explicit shared local-engine assumption",
        )

    # Determine declared host scope
    if args.no_shared_host:
        left_host_scope = HostScope(scope_id=None, basis="unspecified host scope")
        right_host_scope = HostScope(scope_id=None, basis="unspecified host scope")
    else:
        left_host_scope = HostScope(
            scope_id=args.host_scope,
            basis="explicit shared local-host assumption",
        )
        right_host_scope = HostScope(
            scope_id=args.host_scope,
            basis="explicit shared local-host assumption",
        )

    try:
        left_raw, left_label, left_root = _load_env_config(args.left)
        right_raw, right_label, right_root = _load_env_config(args.right)
    except ComposeCollectorError as exc:
        # Sanitized error message without leaking sensitive data
        err_msg = str(exc)
        if args.format == "json":
            import json
            print(json.dumps({
                "schema_version": "1.0",
                "tool_version": get_package_version(),
                "complete": False,
                "error": err_msg,
            }, indent=2))
        else:
            sys.stderr.write(f"Collection Error: {err_msg}\n")
        return 2
    except Exception:
        # Do not leak stack traces or secret variables
        sys.stderr.write("Unexpected error occurred during environment collection.\n")
        return 2

    # Normalization
    left_env = normalize_compose_model(
        left_raw, left_label, left_root, left_engine_scope, left_host_scope
    )
    right_env = normalize_compose_model(
        right_raw, right_label, right_root, right_engine_scope, right_host_scope
    )

    # Rule evaluations
    findings: list[Finding] = []
    # 1. Volume collisions (VOL001)
    findings.extend(check_volumes(left_env, right_env, include_coverage=False))
    # 2. Host-port collisions (PORT001)
    findings.extend(check_ports(left_env, right_env))
    # 3. Overall coverage disclaimer
    findings.append(
        Finding(
            rule_id="COVERAGE",
            status="unknown",
            severity="info",
            confidence="high",
            basis="partial evaluation",
            message=(
                "Bind mounts, databases, project namespaces, and runtime state were not evaluated. "
                "Exit 0 does not prove complete environment isolation."
            ),
            environments=[left_env.label, right_env.label],
        )
    )

    # Report generation
    report = build_report(
        findings=findings,
        environments=[left_env.label, right_env.label],
        engine_scope=left_engine_scope,
        host_scope=left_host_scope,
    )

    if args.format == "json":
        print(format_json_report(report))
    else:
        print(format_text_report(report))

    # Exit code determination
    has_fail = any(f.status == "fail" for f in findings)
    has_unknown = any(f.status == "unknown" for f in findings)

    if has_fail:
        return 1
    if args.strict and has_unknown:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
