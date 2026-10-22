"""Create Environment Screen for isolation-check TUI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    Static,
)

from isolation_check.create.spec import CreateSpec, SpecError, load_spec_from_cli
from isolation_check.tui.adapters import do_create, dry_run_create, resolve_local_repo
from isolation_check.tui.screens.modals import ConfirmModal


class CreateEnvScreen(Screen[None]):
    """Screen for defining and creating an isolated environment."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("d", "run_dry_run", "Dry Run"),
        ("c", "run_create", "Create"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_spec: CreateSpec | None = None
        self._dry_run_passed: bool = False
        self._is_busy: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="screen-scroll"):
            yield Static("Create an Isolated Environment", classes="section-title")
            yield Static(
                "Define a new worktree and container environment. Dry-run validation is "
                "required before the git worktree and branch can be created.",
                classes="section-subtitle",
            )

            # Error / Notice Banner
            yield Static(id="form-feedback", classes="text-muted")

            # Form fields
            with Container(classes="card"):
                yield Label("Environment Name *")
                yield Input(placeholder="e.g. feature-login, pr-42", id="field-name")

                yield Label("Source Repository Path")
                yield Input(placeholder="/path/to/repo (blank for current directory)", id="field-repo")

                yield Label("Base Git Revision (branch, tag, or commit)")
                yield Input(value="HEAD", placeholder="HEAD", id="field-base-ref")

                yield Label("New Branch Name (optional, defaults to environment name)")
                yield Input(placeholder="Defaults to environment name", id="field-branch")

                yield Label("Worktree Path (optional, defaults to sibling directory)")
                yield Input(placeholder="Defaults to ../<env-name>", id="field-worktree")

                yield Label("Compose Files (comma-separated, in load order)")
                yield Input(value="docker-compose.yaml", placeholder="docker-compose.yaml", id="field-compose")

                yield Label("Application Service Name *")
                yield Input(placeholder="e.g. web, api, app", id="field-app-service")

                yield Label("PostgreSQL Service Name *")
                yield Input(placeholder="e.g. db, postgres", id="field-db-service")

                yield Label("Database Env Var in App Container *")
                yield Input(value="DATABASE_URL", placeholder="DATABASE_URL", id="field-db-env-var")

                yield Label("App Container Target Port (optional)")
                yield Input(placeholder="e.g. 3000, 8080 (extracted from Compose if blank)", id="field-app-port")

                with Horizontal(classes="mt-1"):
                    yield Checkbox("Publish Database Port to Host (off by default for isolation)", id="field-publish-db")

            # Action Buttons
            with Horizontal(classes="action-row"):
                yield Button("1. Validate & Plan (Dry Run)", variant="primary", id="btn-dry-run")
                yield Button("2. Create Environment", variant="success", id="btn-create", disabled=True)
                yield Button("Back to Home", variant="default", id="btn-back")

            yield LoadingIndicator(id="create-spinner")

            # Plan preview section (hidden until dry run succeeds)
            with Container(id="plan-container", classes="card"):
                yield Static("Proposed Dry-Run Plan", classes="card-header")
                yield Static(id="plan-details", classes="monospace-box", markup=False)

            # Result section
            with Container(id="result-container", classes="card"):
                yield Static("Creation Result", classes="card-header")
                yield Static(id="result-details")

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#create-spinner", LoadingIndicator).display = False
        self.query_one("#plan-container", Container).display = False
        self.query_one("#result-container", Container).display = False

        # Attempt to autofill repo from context
        app = getattr(self, "app", None)
        repo_hint = getattr(app, "active_repo", None)
        if not repo_hint:
            repo_hint, _ = resolve_local_repo()
        if repo_hint:
            self.query_one("#field-repo", Input).value = str(repo_hint)

            # Check if repo has isolation-check.toml to prefill defaults
            cfg_file = Path(repo_hint) / "isolation-check.toml"
            if cfg_file.is_file():
                try:
                    import tomllib
                    data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
                    create_sec = data.get("create", data)
                    if "app_service" in create_sec:
                        self.query_one("#field-app-service", Input).value = str(create_sec["app_service"])
                    if "db_service" in create_sec:
                        self.query_one("#field-db-service", Input).value = str(create_sec["db_service"])
                    if "db_env_var" in create_sec:
                        self.query_one("#field-db-env-var", Input).value = str(create_sec["db_env_var"])
                    if "compose_files" in create_sec and isinstance(create_sec["compose_files"], list):
                        self.query_one("#field-compose", Input).value = ", ".join(create_sec["compose_files"])
                    if "app_port" in create_sec:
                        self.query_one("#field-app-port", Input).value = str(create_sec["app_port"])
                    if "publish_db" in create_sec:
                        self.query_one("#field-publish-db", Checkbox).value = bool(create_sec["publish_db"])
                except Exception:
                    pass

    def action_go_back(self) -> None:
        if self._is_busy:
            self.notify("Operation in progress. Please wait before exiting.", severity="warning")
            return
        self.app.pop_screen()

    def action_run_dry_run(self) -> None:
        self._on_dry_run_clicked()

    def action_run_create(self) -> None:
        if self._dry_run_passed and self._current_spec:
            self._confirm_and_create()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-back":
            self.action_go_back()
        elif bid == "btn-dry-run":
            self._on_dry_run_clicked()
        elif bid == "btn-create":
            self._confirm_and_create()

    def _collect_spec(self) -> CreateSpec:
        name = self.query_one("#field-name", Input).value.strip()
        if not name:
            raise SpecError("Environment Name is required.")

        repo_str = self.query_one("#field-repo", Input).value.strip()
        repo_path = Path(repo_str) if repo_str else None

        base_ref = self.query_one("#field-base-ref", Input).value.strip() or "HEAD"
        branch = self.query_one("#field-branch", Input).value.strip() or None
        worktree = self.query_one("#field-worktree", Input).value.strip() or None

        compose_raw = self.query_one("#field-compose", Input).value.strip()
        compose_files = [f.strip() for f in compose_raw.split(",") if f.strip()] if compose_raw else None

        app_service = self.query_one("#field-app-service", Input).value.strip() or None
        db_service = self.query_one("#field-db-service", Input).value.strip() or None
        db_env_var = self.query_one("#field-db-env-var", Input).value.strip() or None

        port_str = self.query_one("#field-app-port", Input).value.strip()
        app_port = int(port_str) if port_str.isdigit() else None

        publish_db = self.query_one("#field-publish-db", Checkbox).value

        spec = load_spec_from_cli(
            name=name,
            repo_arg=str(repo_path) if repo_path else None,
            base_ref_arg=base_ref,
            branch_arg=branch,
            worktree_path_arg=worktree,
            compose_files_arg=compose_files,
            app_service_arg=app_service,
            db_service_arg=db_service,
            db_env_var_arg=db_env_var,
            app_port_arg=app_port,
            publish_db_arg=publish_db,
            dry_run=True,
        )
        return spec

    def _on_dry_run_clicked(self) -> None:
        if self._is_busy:
            return
        feedback = self.query_one("#form-feedback", Static)
        try:
            spec = self._collect_spec()
        except SpecError as exc:
            feedback.update(f"[red]Input Error:[/red] {exc}")
            self.query_one("#btn-create", Button).disabled = True
            self._dry_run_passed = False
            return
        except Exception as exc:
            feedback.update(f"[red]Error:[/red] {exc}")
            self.query_one("#btn-create", Button).disabled = True
            self._dry_run_passed = False
            return

        feedback.update("[dim]Validating configuration and planning isolated environment...[/dim]")
        self.query_one("#btn-dry-run", Button).disabled = True
        self.query_one("#create-spinner", LoadingIndicator).display = True
        self._is_busy = True
        self._execute_dry_run_worker(spec)

    @work(exclusive=True, thread=True)
    def _execute_dry_run_worker(self, spec: CreateSpec) -> None:
        result = dry_run_create(spec)
        self.app.call_from_thread(self._handle_dry_run_completed, spec, result)

    def _handle_dry_run_completed(self, spec: CreateSpec, result: Any) -> None:
        if not self.is_mounted:
            return
        self._is_busy = False
        self.query_one("#btn-dry-run", Button).disabled = False
        self.query_one("#create-spinner", LoadingIndicator).display = False
        feedback = self.query_one("#form-feedback", Static)
        plan_box = self.query_one("#plan-container", Container)

        if not result.valid:
            feedback.update(f"[red]Dry-Run Validation Failed:[/red] {result.error}")
            self.query_one("#btn-create", Button).disabled = True
            self._dry_run_passed = False
            plan_box.display = False
            return

        self._current_spec = spec
        self._dry_run_passed = True
        feedback.update("[green]Dry-run validation successful. Review proposed plan below.[/green]")
        self.query_one("#btn-create", Button).disabled = False

        plan = result.plan
        lines = [
            f"Environment Name:     {spec.name}",
            f"Source Repository:    {spec.repo}",
            f"Base Revision:        {spec.base_ref} ({result.base_commit[:8] if result.base_commit else 'unknown'})",
            f"Worktree Path:        {spec.worktree_path}",
            f"New Git Branch:       {spec.branch}",
            f"Compose Project:      {plan.project_name}",
            f"Dedicated DB Volume:  {plan.dedicated_volume}",
            f"App Container Port:   {plan.app_port}",
            f"App Host Port:        127.0.0.1 loopback-only (assigned dynamically at runtime)",
            f"Database Connection:  {plan.redacted_database_url}",
            f"Host DB Port Expose:  {'YES (WARNING: check for conflicts)' if spec.publish_db else 'NO (Loopback isolated)'}",
            f"Files to generate:    {plan.override_compose_rel_path}, {plan.env_rel_path}, {plan.manifest_rel_path}",
        ]
        self.query_one("#plan-details", Static).update("\n".join(lines))
        plan_box.display = True

    def _confirm_and_create(self) -> None:
        if not self._current_spec or not self._dry_run_passed or self._is_busy:
            return

        spec = self._current_spec
        msg = (
            f"Create environment '{spec.name}' at:\n"
            f"{spec.worktree_path}\n\n"
            f"This will create a new git worktree, checkout branch '{spec.branch}', "
            f"and generate isolated Compose override files.\n\n"
            "Database passwords and private env files remain local and redacted."
        )

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                self._start_create_process(spec)

        self.app.push_screen(ConfirmModal("Confirm Environment Creation", msg), _on_confirm)

    def _start_create_process(self, spec: CreateSpec) -> None:
        self._is_busy = True
        self.query_one("#btn-dry-run", Button).disabled = True
        self.query_one("#btn-create", Button).disabled = True
        self.query_one("#create-spinner", LoadingIndicator).display = True
        feedback = self.query_one("#form-feedback", Static)
        feedback.update("[yellow]Creating worktree and configuring isolated environment...[/yellow]")
        self._execute_create_worker(spec)

    @work(exclusive=True, thread=True)
    def _execute_create_worker(self, spec: CreateSpec) -> None:
        result = do_create(spec)
        self.app.call_from_thread(self._handle_create_completed, result)

    def _handle_create_completed(self, result: Any) -> None:
        if not self.is_mounted:
            return
        self._is_busy = False
        self.query_one("#btn-dry-run", Button).disabled = False
        self.query_one("#btn-create", Button).disabled = True
        self.query_one("#create-spinner", LoadingIndicator).display = False

        feedback = self.query_one("#form-feedback", Static)
        res_container = self.query_one("#result-container", Container)
        res_details = self.query_one("#result-details", Static)

        if result.success:
            feedback.update("[green]Environment successfully created![/green]")
            lines = [
                f"[bold green]Created Environment:[/bold green] {result.environment}",
                f"Worktree Path:       {result.worktree_path}",
                f"Branch:              {result.branch}",
                f"Compose Project:     {result.compose_project_name}",
                f"Dedicated Volume:    {result.dedicated_volume}",
                f"App Target Port:     {result.app_port}",
                f"Redacted DB URL:     {result.database_url_redacted}",
                "",
                "[bold]Next Steps:[/bold] Navigate to 'Manage environments' on the Home screen to start your environment.",
            ]
            res_details.update("\n".join(lines))
            res_container.display = True
            self.notify(f"Environment '{result.environment}' created successfully!", severity="information")
        else:
            feedback.update(f"[red]Creation Failed:[/red] {result.error}")
            lines = [
                f"[bold red]Failed to create environment:[/bold red] {result.error}",
            ]
            if result.rolled_back:
                lines.append("[yellow]Rolled back:[/yellow] Worktree and branch were cleanly removed.")
            if result.user_changes_preserved:
                lines.append("[yellow]Diagnostic notice:[/yellow] Existing files were preserved to prevent data loss.")
            res_details.update("\n".join(lines))
            res_container.display = True
            self.notify("Environment creation failed. See details below.", severity="error")
