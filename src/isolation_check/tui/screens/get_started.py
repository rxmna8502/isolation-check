"""Guided sequential setup wizard screen to generate isolation-check.toml."""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from isolation_check.tui.adapters import (
    PrerequisiteResult,
    ServiceSummary,
    check_all_prerequisites,
    find_compose_candidates,
    inspect_compose_definition,
    load_existing_config_toml,
    render_config_toml,
    resolve_local_repo,
    save_config_toml,
    validate_selected_service_pair,
)
from isolation_check.tui.screens.modals import OverwriteConfigModal


def compute_unified_diff(existing: str, proposed: str) -> str:
    """Computes a colored unified diff between existing and proposed config."""
    diff_lines = list(
        difflib.unified_diff(
            existing.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile="existing/isolation-check.toml",
            tofile="proposed/isolation-check.toml",
        )
    )
    if not diff_lines:
        return "[dim]No changes detected; proposed configuration matches existing file.[/dim]"
    formatted: list[str] = []
    for line in diff_lines:
        line_clean = line.rstrip("\r\n")
        if line_clean.startswith("+++") or line_clean.startswith("---"):
            formatted.append(f"[bold]{line_clean}[/bold]")
        elif line_clean.startswith("+"):
            formatted.append(f"[green]{line_clean}[/green]")
        elif line_clean.startswith("-"):
            formatted.append(f"[red]{line_clean}[/red]")
        elif line_clean.startswith("@@"):
            formatted.append(f"[cyan]{line_clean}[/cyan]")
        else:
            formatted.append(line_clean)
    return "\n".join(formatted)


class GetStartedScreen(Screen[None]):
    """Sequential 5-step wizard to guide configuration of isolation-check.toml."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]

    TOTAL_STEPS = 5

    def __init__(self) -> None:
        super().__init__()
        self.current_step: int = 1

        # Step 1: Repository state
        self.repo_path: Path | None = None
        self.repo_verified: bool = False
        self._last_verified_path: str = ""
        self.repo_error_summary: str = ""
        self.repo_error_details: str = ""
        self.repo_details_visible: bool = False

        # Step 2: Prerequisites state
        self.prereqs_results: list[PrerequisiteResult] = []
        self.prereqs_checked: bool = False
        self.prereqs_passed: bool = False
        self.prereqs_error_details: str = ""
        self.prereqs_details_visible: bool = False

        # Step 3: Compose files & services state
        self.compose_inspected: bool = False
        self._last_verified_compose: str = ""
        self.discovered_services: list[str] = []
        self.service_summaries: list[ServiceSummary] = []
        self.service_mapping_confirmed: bool = False
        self.ports_map: dict[str, list[int]] = {}
        self.compose_error_summary: str = ""
        self.compose_error_details: str = ""
        self.compose_details_visible: bool = False

        # Step 4 & 5: Config & Review state
        self.existing_toml_text: str | None = None
        self.proposed_toml: str = ""
        self.validation_passed: bool = False
        self.written_successfully: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-container"):
            yield Label("isolation-check -- Configuration Wizard", id="header-title")
            yield Label("[Esc] Back", id="header-status")

        with VerticalScroll(classes="wizard-scroll"):
            with Container(classes="wizard-container"):
                # Progress bar indicator
                yield Static("", id="wizard-progress-bar", classes="wizard-progress")

                # -------------------------------------------------------------
                # Step 1: Select and verify repository
                # -------------------------------------------------------------
                with Vertical(id="step-1-card", classes="wizard-step-card"):
                    yield Label("Step 1: Select & Verify Git Repository", classes="wizard-step-title")
                    yield Label(
                        "Select the local Git repository where your project and Docker Compose files are located.",
                        classes="form-help",
                    )
                    init_repo, _ = resolve_local_repo()
                    default_path = str(init_repo) if init_repo else str(Path.cwd().resolve())
                    yield Input(
                        value=default_path,
                        placeholder="/path/to/repo",
                        id="input-repo-path",
                        classes="form-input",
                    )
                    yield Label("", id="lbl-repo-status")
                    yield Button("Show details", variant="default", id="btn-repo-details")
                    yield Static("", id="box-repo-details", classes="details-box")

                    with Horizontal(classes="wizard-nav-row"):
                        yield Button("Cancel", variant="default", id="btn-step1-cancel")
                        yield Button("Verify Repository", variant="primary", id="btn-verify-repo")
                        yield Button("Next →", variant="success", id="btn-step1-next", disabled=True)

                # -------------------------------------------------------------
                # Step 2: Check prerequisites
                # -------------------------------------------------------------
                with Vertical(id="step-2-card", classes="wizard-step-card"):
                    yield Label("Step 2: Check System Prerequisites", classes="wizard-step-title")
                    yield Label(
                        "Verify Python, Git, Docker CLI, and Docker Compose availability. "
                        "Blocking failures prevent proceeding; non-blocking notices provide runtime guidance.",
                        classes="form-help",
                    )
                    yield Static("Click 'Run Checks' to diagnose prerequisites.", id="static-prereqs-summary")
                    yield Label("", id="lbl-prereqs-status")
                    yield Button("Show details", variant="default", id="btn-prereqs-details")
                    yield Static("", id="box-prereqs-details", classes="details-box")

                    with Horizontal(classes="wizard-nav-row"):
                        yield Button("← Back", variant="default", id="btn-step2-back")
                        yield Button("Run Checks", variant="primary", id="btn-check-prereqs")
                        yield Button("Next →", variant="success", id="btn-step2-next", disabled=True)

                # -------------------------------------------------------------
                # Step 3: Select Compose files and explicitly confirm service mappings
                # -------------------------------------------------------------
                with Vertical(id="step-3-card", classes="wizard-step-card"):
                    yield Label("Step 3: Compose Files & Service Mappings", classes="wizard-step-title")
                    yield Label(
                        "Specify the Compose files in merge order (comma-separated). "
                        "Then explicitly confirm which service is your application and which is PostgreSQL.",
                        classes="form-help",
                    )
                    yield Input(
                        value="docker-compose.yaml",
                        placeholder="docker-compose.yaml",
                        id="input-compose-files",
                        classes="form-input",
                    )
                    with Horizontal(classes="action-row"):
                        yield Button("Inspect Compose Files", variant="primary", id="btn-inspect-compose")
                    yield Label("", id="lbl-compose-status")
                    yield Button("Show details", variant="default", id="btn-compose-details")
                    yield Static("", id="box-compose-details", classes="details-box")

                    yield Label("PostgreSQL Database Service (Official/supported distributions only):", classes="form-label")
                    yield Select([], prompt="Select PostgreSQL Service", id="select-db-service")
                    yield Label("", id="lbl-db-guidance", classes="field-error")
                    yield Static("", id="box-unsupported-databases", classes="details-box")

                    yield Label("Application Service (Web, API, Worker):", classes="form-label")
                    yield Select([], prompt="Select Application Service", id="select-app-service")
                    yield Label("", id="lbl-app-guidance")

                    with Horizontal(classes="action-row"):
                        yield Button("Confirm Service Selection", variant="primary", id="btn-confirm-services")

                    yield Label("", id="lbl-services-status", classes="field-error")

                    yield Static(
                        "[dim]Accessible Keyboard: [Tab] Move focus • [Enter/Space] Open dropdown/trigger • [↑/↓] Navigate list • [Esc] Close dropdown or step back[/dim]",
                        classes="keyboard-hint",
                    )

                    with Horizontal(classes="wizard-nav-row"):
                        yield Button("← Back", variant="default", id="btn-step3-back")
                        yield Button("Next →", variant="success", id="btn-step3-next", disabled=True)

                # -------------------------------------------------------------
                # Step 4: Configure connection settings and application target port
                # -------------------------------------------------------------
                with Vertical(id="step-4-card", classes="wizard-step-card"):
                    yield Label("Step 4: Connection Settings & Target Port", classes="wizard-step-title")
                    yield Label(
                        "Configure the environment variable name your application expects for the database URL, "
                        "and set the application container target port.",
                        classes="form-help",
                    )

                    yield Label("Database URL Environment Variable:", classes="form-label")
                    yield Input(
                        value="DATABASE_URL",
                        placeholder="DATABASE_URL",
                        id="input-db-env-var",
                        classes="form-input",
                    )

                    yield Label("Application Container Target Port (inside container, e.g. 3000):", classes="form-label")
                    yield Input(
                        value="",
                        placeholder="3000",
                        id="input-app-port",
                        classes="form-input",
                    )

                    yield Static(
                        "[dim]Port Scope Distinction:\n"
                        "• Container Port: The port the app listens on inside its container (e.g. 3000).\n"
                        "• Host Port: Assigned dynamically on 127.0.0.1 at runtime ('isolation-check up') to prevent collisions.[/dim]",
                        classes="text-muted",
                    )

                    yield Checkbox("Publish PostgreSQL port to host (Default: unpublished)", value=False, id="chk-publish-db")
                    yield Label("", id="lbl-connection-status", classes="field-error")

                    with Horizontal(classes="wizard-nav-row"):
                        yield Button("← Back", variant="default", id="btn-step4-back")
                        yield Button("Next →", variant="success", id="btn-step4-next")

                # -------------------------------------------------------------
                # Step 5: Review changes and confirm writing
                # -------------------------------------------------------------
                with Vertical(id="step-5-card", classes="wizard-step-card"):
                    yield Label("Step 5: Review Changes & Confirm Writing", classes="wizard-step-title")
                    yield Label("", id="lbl-review-notice")
                    yield Static("", id="box-review-preview", classes="code-preview")
                    yield Label("", id="lbl-review-status")

                    with Horizontal(classes="wizard-nav-row"):
                        yield Button("← Back", variant="default", id="btn-step5-back")
                        yield Button("Write isolation-check.toml", variant="success", id="btn-write-config", disabled=True)
                        yield Button("Back to Main Menu", variant="primary", id="btn-step5-done")

        with Horizontal(id="footer-hints"):
            yield Label("[Esc] Back   [Tab] Next Field   [Enter] Trigger Action")

    def on_mount(self) -> None:
        """Initialize wizard display and perform initial repository check."""
        self.query_one("#box-repo-details", Static).display = False
        self.query_one("#btn-repo-details", Button).display = False

        self.query_one("#box-prereqs-details", Static).display = False
        self.query_one("#btn-prereqs-details", Button).display = False

        self.query_one("#box-compose-details", Static).display = False
        self.query_one("#btn-compose-details", Button).display = False

        self.query_one("#box-unsupported-databases", Static).display = False

        self.query_one("#btn-step5-done", Button).display = False

        self._set_step(1)
        self._verify_repo_action()

    def action_go_back(self) -> None:
        """Handles escape key: goes back one step or exits to home."""
        if self.current_step > 1:
            self._set_step(self.current_step - 1)
        else:
            self.app.pop_screen()

    def _set_step(self, step: int) -> None:
        """Switches active wizard step and updates progress indicator."""
        self.current_step = step

        # Update progress bar
        step_names = [
            "Repository",
            "Prerequisites",
            "Compose & Services",
            "Connection",
            "Review & Save",
        ]
        tokens: list[str] = []
        for i, name in enumerate(step_names, start=1):
            if i < step:
                tokens.append(f"[green]✓ {name}[/green]")
            elif i == step:
                tokens.append(f"[bold cyan]● {name}[/bold cyan]")
            else:
                tokens.append(f"[dim]○ {name}[/dim]")

        progress_text = (
            f"[bold]Step {step} of {self.TOTAL_STEPS}: {step_names[step - 1]}[/bold]\n"
            + " ── ".join(tokens)
        )
        self.query_one("#wizard-progress-bar", Static).update(progress_text)

        # Toggle cards
        for i in range(1, self.TOTAL_STEPS + 1):
            card = self.query_one(f"#step-{i}-card", Vertical)
            card.display = (i == step)

        # Trigger step-specific logic
        if step == 2 and not self.prereqs_checked:
            self._check_prereqs_action()
        elif step == 3 and not self.compose_inspected:
            self._inspect_compose_action()
        elif step == 4:
            self._validate_step4()
        elif step == 5:
            self._prepare_review_step()

    # -------------------------------------------------------------------------
    # Invalidation & Change Event Handlers
    # -------------------------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        input_id = event.input.id
        if input_id == "input-repo-path":
            current_val = event.input.value.strip()
            # Only invalidate if path has been modified away from verified value
            if self._last_verified_path and current_val != self._last_verified_path:
                self.repo_verified = False
                self.query_one("#btn-step1-next", Button).disabled = True
                self.query_one("#lbl-repo-status", Label).update(
                    "[dim]Repository path changed. Click 'Verify Repository' to re-validate.[/dim]"
                )
                self._invalidate_downstream(from_step=2)

        elif input_id == "input-compose-files":
            current_val = event.input.value.strip()
            # Only invalidate if compose files string has been modified
            if self._last_verified_compose and current_val != self._last_verified_compose:
                self.compose_inspected = False
                self.query_one("#btn-step3-next", Button).disabled = True
                self.query_one("#lbl-compose-status", Label).update(
                    "[dim]Compose files changed. Click 'Inspect Compose Files' to re-discover services.[/dim]"
                )
                self._invalidate_downstream(from_step=4)

        elif input_id in ("input-db-env-var", "input-app-port"):
            self._validate_step4()
            self._invalidate_downstream(from_step=5)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in ("select-app-service", "select-db-service"):
            self.service_mapping_confirmed = False
            try:
                self.query_one("#btn-step3-next", Button).disabled = True
                self.query_one("#lbl-services-status", Label).update(
                    "[dim]Service selection changed. Click 'Confirm Service Selection' to validate and proceed.[/dim]"
                )
            except Exception:
                pass
            self._invalidate_downstream(from_step=4)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "chk-publish-db":
            self._invalidate_downstream(from_step=5)

    def _invalidate_downstream(self, from_step: int) -> None:
        """Invalidates cached validation states for steps at or after from_step."""
        if from_step <= 2:
            self.prereqs_checked = False
            self.prereqs_passed = False
        if from_step <= 3:
            self.compose_inspected = False
            self.discovered_services = []
            self.service_summaries = []
            self.service_mapping_confirmed = False
        if from_step <= 4:
            self.service_mapping_confirmed = False
            try:
                self.query_one("#btn-step3-next", Button).disabled = True
            except Exception:
                pass
        if from_step <= 5:
            self.proposed_toml = ""
            self.validation_passed = False
            try:
                self.query_one("#btn-write-config", Button).disabled = True
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Button Handlers
    # -------------------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        # Step 1
        if bid == "btn-step1-cancel":
            self.app.pop_screen()
        elif bid == "btn-verify-repo":
            self._verify_repo_action()
        elif bid == "btn-step1-next":
            if self.repo_verified:
                self._set_step(2)
        elif bid == "btn-repo-details":
            self._toggle_repo_details()

        # Step 2
        elif bid == "btn-step2-back":
            self._set_step(1)
        elif bid == "btn-check-prereqs":
            self._check_prereqs_action()
        elif bid == "btn-prereqs-details":
            self._toggle_prereqs_details()
        elif bid == "btn-step2-next":
            if self.prereqs_passed:
                self._set_step(3)

        # Step 3
        elif bid == "btn-step3-back":
            self._set_step(2)
        elif bid == "btn-inspect-compose":
            self._inspect_compose_action()
        elif bid == "btn-compose-details":
            self._toggle_compose_details()
        elif bid == "btn-confirm-services":
            self._confirm_services_action()
        elif bid == "btn-step3-next":
            if self._validate_step3():
                self._set_step(4)

        # Step 4
        elif bid == "btn-step4-back":
            self._set_step(3)
        elif bid == "btn-step4-next":
            if self._validate_step4():
                self._set_step(5)

        # Step 5
        elif bid == "btn-step5-back":
            self._set_step(4)
        elif bid == "btn-write-config":
            self._write_config_action()
        elif bid == "btn-step5-done":
            self.app.pop_screen()

    # -------------------------------------------------------------------------
    # Step 1: Repository Verification Logic
    # -------------------------------------------------------------------------
    def _verify_repo_action(self) -> None:
        input_widget = self.query_one("#input-repo-path", Input)
        status_lbl = self.query_one("#lbl-repo-status", Label)
        details_btn = self.query_one("#btn-repo-details", Button)
        details_box = self.query_one("#box-repo-details", Static)
        next_btn = self.query_one("#btn-step1-next", Button)

        path_str = input_widget.value.strip()
        repo, err = resolve_local_repo(path_str if path_str else None)

        if repo:
            self.repo_path = repo
            self.repo_verified = True
            self._last_verified_path = path_str or str(repo)
            self.repo_error_summary = ""
            self.repo_error_details = ""
            status_lbl.update(f"[bold green]Verified Git Repository:[/bold green] {repo}")
            details_btn.display = False
            details_box.display = False
            next_btn.disabled = False

            # Auto-detect compose files for Step 3
            candidates = find_compose_candidates(repo)
            if candidates:
                self.query_one("#input-compose-files", Input).value = ", ".join(candidates)

            # Auto-detect existing config for pre-populating defaults
            existing_text, existing_dict = load_existing_config_toml(repo)
            self.existing_toml_text = existing_text
            if existing_dict and "create" in existing_dict:
                c_sec = existing_dict["create"]
                if "db_env_var" in c_sec:
                    self.query_one("#input-db-env-var", Input).value = str(c_sec["db_env_var"])
                if "app_port" in c_sec:
                    self.query_one("#input-app-port", Input).value = str(c_sec["app_port"])
                if "publish_db" in c_sec:
                    self.query_one("#chk-publish-db", Checkbox).value = bool(c_sec["publish_db"])
        else:
            self.repo_path = None
            self.repo_verified = False
            self.repo_error_summary = (
                "The specified directory is not a valid Git repository root. "
                "Please select a project folder containing a .git directory."
            )
            self.repo_error_details = err or f"Path '{path_str}' could not be resolved as a Git repository root."
            status_lbl.update(f"[bold red]Validation Error:[/bold red] {self.repo_error_summary}")
            details_box.update(self.repo_error_details)
            details_btn.display = True
            next_btn.disabled = True

    def _toggle_repo_details(self) -> None:
        box = self.query_one("#box-repo-details", Static)
        btn = self.query_one("#btn-repo-details", Button)
        self.repo_details_visible = not self.repo_details_visible
        box.display = self.repo_details_visible
        btn.label = "Hide details" if self.repo_details_visible else "Show details"

    # -------------------------------------------------------------------------
    # Step 2: Prerequisites Logic
    # -------------------------------------------------------------------------
    @work(thread=True)
    def _check_prereqs_action(self) -> None:
        def _set_status(txt: str) -> None:
            if not self.is_mounted:
                return
            self.query_one("#static-prereqs-summary", Static).update(txt)

        self.app.call_from_thread(_set_status, "[dim]Diagnosing system prerequisites...[/dim]")

        prereqs = check_all_prerequisites()
        failures = [p for p in prereqs if p.status == "FAIL"]
        warnings = [p for p in prereqs if p.status in ("WARN", "INFO")]
        passed = [p for p in prereqs if p.status == "PASS"]

        def _update_ui() -> None:
            if not self.is_mounted:
                return
            self.prereqs_results = prereqs
            self.prereqs_checked = True

            summary_box = self.query_one("#static-prereqs-summary", Static)
            status_lbl = self.query_one("#lbl-prereqs-status", Label)
            details_btn = self.query_one("#btn-prereqs-details", Button)
            details_box = self.query_one("#box-prereqs-details", Static)
            next_btn = self.query_one("#btn-step2-next", Button)

            # Build sanitized details breakdown
            detail_lines: list[str] = ["Prerequisite Diagnostics Breakdown:"]
            for p in prereqs:
                tag = f"[{p.status}]"
                detail_lines.append(f"• {tag:8} {p.title:24} Current: {p.current}")
                detail_lines.append(f"         Required: {p.required}")
                detail_lines.append(f"         Guidance: {p.guidance}")

            self.prereqs_error_details = "\n".join(detail_lines)
            details_box.update(self.prereqs_error_details)
            details_btn.display = True

            # Formulate plain language summary
            summary_lines = [
                f"Diagnostic Summary: [green]{len(passed)} Passed[/green], "
                f"[yellow]{len(warnings)} Notice(s)[/yellow], "
                f"[red]{len(failures)} Blocking Failure(s)[/red]\n"
            ]
            for p in prereqs:
                if p.status == "FAIL":
                    status_style = "[bold red][FAIL][/bold red]   "
                    note = " (BLOCKING)"
                elif p.status in ("WARN", "INFO"):
                    status_style = "[bold yellow][NOTICE][/bold yellow] "
                    note = " (non-blocking)"
                else:
                    status_style = "[bold green][PASS][/bold green]   "
                    note = ""
                summary_lines.append(f"  {status_style} {p.title:24} {p.current}{note}")

            summary_box.update("\n".join(summary_lines))

            if failures:
                self.prereqs_passed = False
                next_btn.disabled = True
                status_lbl.update(
                    f"[bold red]Progression Blocked:[/bold red] {len(failures)} required component(s) failed. "
                    "Install or repair missing requirements to proceed."
                )
            else:
                self.prereqs_passed = True
                next_btn.disabled = False
                if warnings:
                    status_lbl.update(
                        "[bold green]Ready to proceed.[/bold green] System requirements met with non-blocking notices."
                    )
                else:
                    status_lbl.update(
                        "[bold green]Ready to proceed.[/bold green] All system prerequisites verified successfully."
                    )

        self.app.call_from_thread(_update_ui)

    def _toggle_prereqs_details(self) -> None:
        box = self.query_one("#box-prereqs-details", Static)
        btn = self.query_one("#btn-prereqs-details", Button)
        self.prereqs_details_visible = not self.prereqs_details_visible
        box.display = self.prereqs_details_visible
        btn.label = "Hide details" if self.prereqs_details_visible else "Show details"

    # -------------------------------------------------------------------------
    # Step 3: Compose Files & Services Logic
    # -------------------------------------------------------------------------
    @work(thread=True)
    def _inspect_compose_action(self) -> None:
        def _set_status(msg: str) -> None:
            if not self.is_mounted:
                return
            try:
                self.query_one("#lbl-compose-status", Label).update(msg)
            except Exception:
                pass

        if not self.repo_path:
            self.app.call_from_thread(_set_status, "[bold red]No verified repository available.[/bold red]")
            return

        compose_input = self.query_one("#input-compose-files", Input).value.strip()
        files = [f.strip() for f in compose_input.split(",") if f.strip()]
        if not files:
            self.app.call_from_thread(_set_status, "[bold red]Please specify at least one Compose file.[/bold red]")
            return

        services, ports_map, summaries, err = inspect_compose_definition(self.repo_path, files)

        def _update_ui() -> None:
            if not self.is_mounted:
                return
            try:
                status_lbl = self.query_one("#lbl-compose-status", Label)
                details_btn = self.query_one("#btn-compose-details", Button)
                details_box = self.query_one("#box-compose-details", Static)
                app_select = self.query_one("#select-app-service", Select)
                db_select = self.query_one("#select-db-service", Select)
                db_guidance_lbl = self.query_one("#lbl-db-guidance", Label)
                unsupported_dbs_box = self.query_one("#box-unsupported-databases", Static)
                services_status_lbl = self.query_one("#lbl-services-status", Label)
                next_btn = self.query_one("#btn-step3-next", Button)
            except Exception:
                return

            if err:
                self.compose_inspected = False
                self.compose_error_summary = (
                    "Could not load or parse the specified Compose configuration. "
                    "Please check the file name and verify valid YAML format."
                )
                self.compose_error_details = err
                status_lbl.update(f"[bold red]Compose Error:[/bold red] {self.compose_error_summary}")
                details_box.update(self.compose_error_details)
                details_btn.display = True
                self.service_mapping_confirmed = False
                next_btn.disabled = True
                return

            self.compose_inspected = True
            self._last_verified_compose = compose_input
            self.discovered_services = services
            self.service_summaries = summaries
            self.ports_map = ports_map
            details_btn.display = False
            details_box.display = False

            status_lbl.update(
                f"[bold green]Discovered {len(services)} service(s):[/bold green] {', '.join(services)}"
            )

            # 1. Filter supported PostgreSQL candidates using shared backend validation rules
            db_candidates = [s for s in summaries if s.is_postgres_candidate]

            # 2. Identify unrecognized/custom database images or non-qualifying datastores
            unsupported_dbs = [
                s for s in summaries
                if not s.is_postgres_candidate and (
                    s.postgres_unsupported_reason
                    or any(token in s.name.lower() for token in ("db", "database", "sql", "postgres", "mysql", "mongo", "redis", "kafka"))
                )
            ]

            if unsupported_dbs:
                unsupported_lines = ["[bold yellow]Unrecognized / Unsupported Datastores:[/bold yellow]"]
                for s in unsupported_dbs:
                    reason = s.postgres_unsupported_reason or "Not recognized as an official or supported PostgreSQL distribution"
                    unsupported_lines.append(f"• [bold]{s.name}[/bold] ({s.image or s.build_source or 'source'}): {reason}")
                unsupported_dbs_box.update("\n".join(unsupported_lines))
                unsupported_dbs_box.display = True
            else:
                unsupported_dbs_box.display = False

            # Configure PostgreSQL Selector
            prev_db = db_select.value if not db_select.is_blank() else None

            if not db_candidates:
                db_select.set_options([])
                db_select.clear()
                db_select.disabled = True
                db_guidance_lbl.update(
                    "[bold red]No qualifying PostgreSQL candidates found in Compose.[/bold red]\n"
                    "[dim]isolation-check requires an official PostgreSQL distribution ('postgres') "
                    "with dynamic container names and standard entrypoints.[/dim]"
                )
            else:
                db_select.disabled = False
                db_options = [(s.db_option_label, s.name) for s in db_candidates]
                db_select.set_options(db_options)

                # Revalidate previously saved selection
                if prev_db:
                    valid_prev = next((s for s in db_candidates if s.name == prev_db), None)
                    if valid_prev:
                        db_select.value = prev_db
                        db_guidance_lbl.update(f"[green]Retained qualifying candidate: '{prev_db}' ({valid_prev.image})[/green]")
                    else:
                        db_select.clear()
                        db_guidance_lbl.update(
                            f"[bold yellow]Previous database selection '{prev_db}' is not a qualifying PostgreSQL candidate and was reset.[/bold yellow]"
                        )
                else:
                    if len(db_candidates) == 1:
                        db_select.value = db_candidates[0].name
                        db_guidance_lbl.update(
                            f"[green]Detected supported candidate: '{db_candidates[0].name}' ({db_candidates[0].image})[/green]"
                        )
                    else:
                        db_select.clear()
                        db_guidance_lbl.update(
                            f"[green]Found {len(db_candidates)} supported PostgreSQL candidate(s). Please choose one.[/green]"
                        )

            # Configure Application Selector
            prev_app = app_select.value if not app_select.is_blank() else None
            app_options = [(s.app_option_label, s.name) for s in summaries]
            app_select.set_options(app_options)

            if prev_app and any(s.name == prev_app for s in summaries):
                app_select.value = prev_app
            else:
                app_select.clear()

            # Require explicit confirmation; do not default to first service and call it confirmed
            self.service_mapping_confirmed = False
            next_btn.disabled = True
            services_status_lbl.update(
                "[dim]Select your Application and PostgreSQL services, then click 'Confirm Service Selection'.[/dim]"
            )

        self.app.call_from_thread(_update_ui)

    def _toggle_compose_details(self) -> None:
        box = self.query_one("#box-compose-details", Static)
        btn = self.query_one("#btn-compose-details", Button)
        self.compose_details_visible = not self.compose_details_visible
        box.display = self.compose_details_visible
        btn.label = "Hide details" if self.compose_details_visible else "Show details"

    def _confirm_services_action(self) -> None:
        """Validates the chosen application and PostgreSQL pair using backend validation rules."""
        app_select = self.query_one("#select-app-service", Select)
        db_select = self.query_one("#select-db-service", Select)
        status_lbl = self.query_one("#lbl-services-status", Label)
        next_btn = self.query_one("#btn-step3-next", Button)

        app_val = str(app_select.value) if not app_select.is_blank() else ""
        db_val = str(db_select.value) if not db_select.is_blank() else ""

        if not self.compose_inspected:
            status_lbl.update("[bold red]Please inspect Compose files first.[/bold red]")
            self.service_mapping_confirmed = False
            next_btn.disabled = True
            return

        if not db_val:
            status_lbl.update("[bold red]Please select a PostgreSQL database service.[/bold red]")
            self.service_mapping_confirmed = False
            next_btn.disabled = True
            return

        if not app_val:
            status_lbl.update("[bold red]Please select an Application service.[/bold red]")
            self.service_mapping_confirmed = False
            next_btn.disabled = True
            return

        valid, msg, proposed_env = validate_selected_service_pair(app_val, db_val, self.service_summaries)
        if not valid:
            status_lbl.update(f"[bold red]Service Mapping Rejected:[/bold red] {msg}")
            self.service_mapping_confirmed = False
            next_btn.disabled = True
            return

        self.service_mapping_confirmed = True
        status_lbl.update(msg)
        next_btn.disabled = False

        # Pre-fill Step 4 connection environment variable and port
        if proposed_env:
            self.query_one("#input-db-env-var", Input).value = proposed_env

        app_summary = next((s for s in self.service_summaries if s.name == app_val), None)
        if app_summary and app_summary.ports:
            if len(app_summary.ports) == 1:
                self.query_one("#input-app-port", Input).value = str(app_summary.ports[0])
            elif not self.query_one("#input-app-port", Input).value:
                self.query_one("#input-app-port", Input).value = str(app_summary.ports[0])

    def _validate_step3(self) -> bool:
        """Validates that valid, distinct services have been explicitly confirmed."""
        err_lbl = self.query_one("#lbl-services-status", Label)
        next_btn = self.query_one("#btn-step3-next", Button)

        if not self.compose_inspected:
            err_lbl.update("[dim]Please inspect Compose files first.[/dim]")
            next_btn.disabled = True
            return False

        if not self.service_mapping_confirmed:
            err_lbl.update("[bold red]Please explicitly click 'Confirm Service Selection' before proceeding.[/bold red]")
            next_btn.disabled = True
            return False

        next_btn.disabled = False
        return True

    # -------------------------------------------------------------------------
    # Step 4: Connection & Target Port Logic
    # -------------------------------------------------------------------------
    def _validate_step4(self) -> bool:
        db_env_input = self.query_one("#input-db-env-var", Input).value.strip()
        app_port_input = self.query_one("#input-app-port", Input).value.strip()
        status_lbl = self.query_one("#lbl-connection-status", Label)
        next_btn = self.query_one("#btn-step4-next", Button)

        if not db_env_input:
            status_lbl.update("[bold red]Database URL environment variable name cannot be empty.[/bold red]")
            next_btn.disabled = True
            return False

        if not app_port_input:
            status_lbl.update("[bold red]Application container target port is required (e.g. 3000).[/bold red]")
            next_btn.disabled = True
            return False

        try:
            port_num = int(app_port_input)
            if port_num < 1 or port_num > 65535:
                status_lbl.update(
                    f"[bold red]Container port must be between 1 and 65535 (got {port_num}).[/bold red]"
                )
                next_btn.disabled = True
                return False
        except ValueError:
            status_lbl.update(
                f"[bold red]Container port must be a whole number between 1 and 65535 (got '{app_port_input}').[/bold red]"
            )
            next_btn.disabled = True
            return False

        status_lbl.update("[bold green]Connection settings and container target port valid.[/bold green]")
        next_btn.disabled = False
        return True

    # -------------------------------------------------------------------------
    # Step 5: Review & Save Logic
    # -------------------------------------------------------------------------
    def _prepare_review_step(self) -> None:
        """Renders proposed TOML, checks for existing configuration, and displays unified diff."""
        if not self.repo_path:
            return

        app_select = self.query_one("#select-app-service", Select)
        db_select = self.query_one("#select-db-service", Select)
        db_env_input = self.query_one("#input-db-env-var", Input).value.strip()
        app_port_input = self.query_one("#input-app-port", Input).value.strip()
        compose_input = self.query_one("#input-compose-files", Input).value.strip()
        publish_db = self.query_one("#chk-publish-db", Checkbox).value

        app_svc = str(app_select.value) if not app_select.is_blank() else ""
        db_svc = str(db_select.value) if not db_select.is_blank() else ""
        files = [f.strip() for f in compose_input.split(",") if f.strip()]
        port_num = int(app_port_input) if app_port_input.isdigit() else None

        notice_lbl = self.query_one("#lbl-review-notice", Label)
        preview_box = self.query_one("#box-review-preview", Static)
        status_lbl = self.query_one("#lbl-review-status", Label)
        write_btn = self.query_one("#btn-write-config", Button)

        # Validate service confirmation and pair validation before enabling write
        if not self.service_mapping_confirmed:
            self.validation_passed = False
            notice_lbl.update("[bold red]Service Mapping Unconfirmed:[/bold red] Return to Step 3 and explicitly confirm your service mapping.")
            preview_box.update("[dim]Preview unavailable until service mapping is confirmed.[/dim]")
            write_btn.disabled = True
            return

        pair_valid, pair_msg, _ = validate_selected_service_pair(app_svc, db_svc, self.service_summaries)
        if not pair_valid:
            self.validation_passed = False
            notice_lbl.update(f"[bold red]Invalid Service Selection:[/bold red] {pair_msg}")
            preview_box.update("[dim]Preview unavailable due to invalid service selection.[/dim]")
            write_btn.disabled = True
            return

        # Validate completeness before enabling write
        if not app_svc or not db_svc or app_svc == db_svc or not files or port_num is None:
            self.validation_passed = False
            notice_lbl.update("[bold red]Configuration Incomplete:[/bold red] Return to previous steps to complete required settings.")
            preview_box.update("[dim]Preview unavailable due to missing or invalid fields.[/dim]")
            write_btn.disabled = True
            return

        proposed = render_config_toml(
            app_service=app_svc,
            db_service=db_svc,
            db_env_var=db_env_input or "DATABASE_URL",
            app_port=port_num,
            compose_files=files,
            publish_db=publish_db,
        )
        self.proposed_toml = proposed
        self.validation_passed = True

        # Check existing config in repo
        existing_text, _ = load_existing_config_toml(self.repo_path)
        self.existing_toml_text = existing_text

        if existing_text:
            diff_text = compute_unified_diff(existing_text, proposed)
            notice_lbl.update(
                "[bold yellow]Existing 'isolation-check.toml' detected in repository.[/bold yellow]\n"
                "Review proposed unified diff below before confirming overwrite:"
            )
            preview_box.update(diff_text)
            status_lbl.update("[yellow]Click 'Write isolation-check.toml' to confirm overwriting existing configuration.[/yellow]")
        else:
            notice_lbl.update("[bold green]New configuration proposal (isolation-check.toml):[/bold green]")
            preview_box.update(proposed)
            status_lbl.update("[green]Configuration valid. Click 'Write isolation-check.toml' to create configuration file.[/green]")

        write_btn.disabled = False

    def _write_config_action(self) -> None:
        """Writes the configuration to isolation-check.toml, requiring confirmation if overwriting."""
        if not self.repo_path or not self.validation_passed or not self.proposed_toml or not self.service_mapping_confirmed:
            return

        if self.existing_toml_text:
            # Overwrite confirmation required
            def _on_overwrite_confirmed(confirmed: bool) -> None:
                if confirmed:
                    self._execute_write()

            self.app.push_screen(
                OverwriteConfigModal(
                    existing_content=self.existing_toml_text,
                    proposed_content=self.proposed_toml,
                ),
                _on_overwrite_confirmed,
            )
        else:
            self._execute_write()

    def _execute_write(self) -> None:
        if not self.repo_path or not self.proposed_toml:
            return

        try:
            ok, err = save_config_toml(self.repo_path, self.proposed_toml)
        except Exception as exc:
            ok, err = False, f"Unexpected error while saving configuration: {exc}"

        status_lbl = self.query_one("#lbl-review-status", Label)
        write_btn = self.query_one("#btn-write-config", Button)
        back_btn = self.query_one("#btn-step5-back", Button)
        done_btn = self.query_one("#btn-step5-done", Button)

        if ok:
            self.written_successfully = True
            target_file = self.repo_path / "isolation-check.toml"
            status_lbl.update(
                f"[bold green]Configuration successfully written![/bold green]\n"
                f"Saved to: [bold]{target_file}[/bold]\n"
                f"You can now create and manage isolated environments from the Home screen."
            )
            write_btn.display = False
            back_btn.display = False
            done_btn.display = True
            done_btn.focus()
            self.notify("isolation-check.toml saved successfully.", severity="information")
        else:
            status_lbl.update(f"[bold red]Failed to save configuration:[/bold red] {err}")
            self.notify(f"Failed to save configuration: {err}", severity="error")
