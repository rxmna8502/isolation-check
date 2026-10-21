"""Specification and input configuration for environment creation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib
from typing import Any


class SpecError(Exception):
    """Raised when environment creation specification is invalid or incomplete."""


@dataclass(frozen=True)
class CreateSpec:
    """Explicit developer-provided specification for creating an isolated environment."""

    name: str
    repo: Path
    base_ref: str
    branch: str
    worktree_path: Path
    compose_files: list[str]
    app_service: str
    db_service: str
    db_env_var: str
    app_port: int | None = None
    publish_db: bool = False
    dry_run: bool = False
    format: str = "text"
    config_path: Path | None = None

    def sanitize_compose_project_name(self) -> str:
        """Derives a safe Compose project name from the environment name."""
        # Lowercase, replace non-alphanumeric (except hyphens/underscores) with underscores
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", self.name).lower()
        # Ensure it does not start with special characters
        cleaned = re.sub(r"^[^a-z0-9]+", "", cleaned)
        if not cleaned:
            cleaned = "env"
        return cleaned

    def dedicated_volume_name(self) -> str:
        """Computes the dedicated PostgreSQL volume name."""
        return f"{self.sanitize_compose_project_name()}_pgdata"


def _validate_environment_name(name: str) -> None:
    if not name or not name.strip():
        raise SpecError("Environment name cannot be empty.")
    if "/" in name or "\\" in name or ".." in name:
        raise SpecError(f"Environment name '{name}' contains invalid path characters.")
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", name):
        raise SpecError(
            f"Environment name '{name}' is invalid. Must start with an alphanumeric character "
            "and contain only alphanumerics, dashes, underscores, or dots."
        )


def _load_toml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SpecError(f"Configuration file not found: {path}")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise SpecError(f"Failed to parse TOML configuration from {path}: {exc}") from exc

    # Support [create], [project], [template], or top-level keys
    if "create" in data and isinstance(data["create"], dict):
        return data["create"]
    if "project" in data and isinstance(data["project"], dict):
        return data["project"]
    if "template" in data and isinstance(data["template"], dict):
        return data["template"]
    return data


def load_spec_from_cli(
    name: str,
    repo_arg: str | None = None,
    base_ref_arg: str | None = None,
    branch_arg: str | None = None,
    worktree_path_arg: str | None = None,
    compose_files_arg: list[str] | None = None,
    app_service_arg: str | None = None,
    db_service_arg: str | None = None,
    db_env_var_arg: str | None = None,
    app_port_arg: int | str | None = None,
    publish_db_arg: bool | None = None,
    dry_run: bool = False,
    output_format: str = "text",
    config_path_arg: str | None = None,
    **kwargs: Any,
) -> CreateSpec:
    """Builds a CreateSpec from CLI arguments and optional configuration file.

    Requires explicit identification of:
    - Source repository and base revision
    - Compose files, in order
    - Application service
    - PostgreSQL service
    - Application database connection environment variable
    """
    repo_arg = repo_arg or kwargs.get("repo")
    base_ref_arg = base_ref_arg or kwargs.get("base_ref")
    branch_arg = branch_arg or kwargs.get("branch")
    worktree_path_arg = worktree_path_arg or kwargs.get("worktree_path")
    compose_files_arg = compose_files_arg or kwargs.get("compose_files")
    app_service_arg = app_service_arg or kwargs.get("app_service")
    db_service_arg = db_service_arg or kwargs.get("db_service")
    db_env_var_arg = db_env_var_arg or kwargs.get("db_env_var")
    if app_port_arg is None and "app_port" in kwargs:
        app_port_arg = kwargs["app_port"]
    if publish_db_arg is None and "publish_db" in kwargs:
        publish_db_arg = kwargs["publish_db"]
    config_path_arg = config_path_arg or kwargs.get("config_path")

    _validate_environment_name(name)

    # 1. Resolve source repo
    repo_path = Path(repo_arg).resolve() if repo_arg else Path.cwd().resolve()

    # 2. Check for config file
    cfg_data: dict[str, Any] = {}
    config_file: Path | None = None
    if config_path_arg:
        config_file = Path(config_path_arg).resolve()
        cfg_data = _load_toml_config(config_file)
    else:
        # Check standard default config file in repo root if present
        default_cfg = repo_path / "isolation-check.toml"
        if default_cfg.is_file():
            config_file = default_cfg
            cfg_data = _load_toml_config(default_cfg)

    # 3. Merge CLI options (CLI options take precedence over config file)
    base_ref = base_ref_arg or cfg_data.get("base_ref") or "HEAD"
    branch = branch_arg or cfg_data.get("branch") or name

    if worktree_path_arg:
        worktree_path = Path(worktree_path_arg).resolve()
    elif "worktree_path" in cfg_data:
        worktree_path = (repo_path / cfg_data["worktree_path"]).resolve()
    else:
        # Default sibling worktree directory
        worktree_path = (repo_path.parent / name).resolve()

    # Compose files in declared order
    compose_files: list[str] = []
    if compose_files_arg:
        compose_files = list(compose_files_arg)
    elif "compose_files" in cfg_data and isinstance(cfg_data["compose_files"], list):
        compose_files = [str(x) for x in cfg_data["compose_files"]]

    app_service = app_service_arg or cfg_data.get("app_service")
    db_service = db_service_arg or cfg_data.get("db_service") or cfg_data.get("postgres_service")
    db_env_var = (
        db_env_var_arg
        or cfg_data.get("db_env_var")
        or cfg_data.get("postgres_variable")
        or cfg_data.get("database_url_env")
    )

    if publish_db_arg is not None:
        publish_db = bool(publish_db_arg)
    else:
        publish_db = bool(cfg_data.get("publish_db", False))

    # Parse and validate app_port if provided
    raw_app_port = app_port_arg if app_port_arg is not None else cfg_data.get("app_port", cfg_data.get("container_port"))
    app_port: int | None = None
    if raw_app_port is not None and str(raw_app_port).strip():
        try:
            app_port = int(raw_app_port)
        except (ValueError, TypeError) as exc:
            raise SpecError(
                f"Application container port (app_port) must be an integer between 1 and 65535, got '{raw_app_port}'."
            ) from exc
        if app_port < 1 or app_port > 65535:
            raise SpecError(
                f"Application container port (app_port) must be an integer between 1 and 65535, got {app_port}."
            )

    # 4. Enforce explicit inputs requirement without guessing
    missing: list[str] = []
    if not compose_files:
        missing.append("Compose files in order (-f / --compose-file or compose_files in config)")
    if not app_service:
        missing.append("Application service (--app-service or app_service in config)")
    if not db_service:
        missing.append("PostgreSQL service (--db-service or db_service in config)")
    if not db_env_var:
        missing.append("Database connection environment variable (--db-env-var or db_env_var in config)")

    if missing:
        details = "\n  - " + "\n  - ".join(missing)
        raise SpecError(
            f"The following required configuration mappings must be explicitly identified:{details}\n"
            "Isolation-check requires explicit service and storage declarations and will not guess them from random files."
        )

    return CreateSpec(
        name=name,
        repo=repo_path,
        base_ref=str(base_ref),
        branch=str(branch),
        worktree_path=worktree_path,
        compose_files=compose_files,
        app_service=str(app_service),
        db_service=str(db_service),
        db_env_var=str(db_env_var),
        app_port=app_port,
        publish_db=publish_db,
        dry_run=dry_run,
        format=output_format,
        config_path=config_file,
    )
