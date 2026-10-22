"""Manage Environments Screen for isolation-check TUI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    LoadingIndicator,
    Static,
)

from isolation_check.tui.adapters import (
    inspect_environment_live,
    list_registered_environments,
    resolve_local_repo,
    start_env,
    stop_env,
)
from isolation_check.tui.screens.modals import ConfirmModal, DetailsModal


class ManageEnvsScreen(Screen[None]):
    """Dashboard screen for listing and managing isolated environments."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "refresh_all", "Refresh"),
        ("s", "start_selected", "Start"),
        ("x", "stop_selected", "Stop"),
        ("i", "inspect_selected", "Inspect"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._repo_path: Path | None = None
        self._envs_cache: list[dict[str, Any]] = []
        self._live_statuses: dict[str, Any] = {}
        self._is_busy: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="screen-scroll"):
            yield Static("Manage Isolated Environments", classes="section-title")
            yield Static(
                "View registered environments, query live container states, and manage runtime lifecycle.",
                classes="section-subtitle",
            )

            yield Static(id="manage-repo-indicator", classes="text-muted")
            yield Static(id="manage-feedback", classes="text-muted")

            # Environment Table
            with Container(classes="card"):
                yield DataTable(id="envs-table")

            # Controls
            with Horizontal(classes="action-row"):
                yield Button("Start (up)", variant="success", id="btn-start", disabled=True)
                yield Button("Stop (down)", variant="warning", id="btn-stop", disabled=True)
                yield Button("Inspect Details", variant="primary", id="btn-inspect", disabled=True)
                yield Button("Refresh", variant="default", id="btn-refresh")
                yield Button("Back to Home", variant="default", id="btn-back")

            yield LoadingIndicator(id="manage-spinner")

            # Empty State
            with Container(id="manage-empty-container", classes="card"):
                yield Static("No Environments Registered", classes="card-header")
                yield Static(
                    "No environments were found in this repository's .isolation-check directory.\n"
                    "Use 'Create an environment' or 'Get started' from the home menu to create one."
                )

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#manage-spinner", LoadingIndicator).display = False
        self.query_one("#manage-empty-container", Container).display = False

        table = self.query_one("#envs-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "Observed State", "Health", "Application URL", "Worktree")

        self._resolve_active_repo()
        self.action_refresh_all()

    def _resolve_active_repo(self) -> None:
        app = getattr(self, "app", None)
        self._repo_path = getattr(app, "active_repo", None)
        if not self._repo_path:
            self._repo_path, _ = resolve_local_repo()
            if self._repo_path and app:
                setattr(app, "active_repo", self._repo_path)

        indicator = self.query_one("#manage-repo-indicator", Static)
        if self._repo_path:
            indicator.update(f"Active Repository: [bold]{self._repo_path}[/bold]")
        else:
            indicator.update("[red]No Git repository resolved. Open TUI inside a repository root.[/red]")

    def action_go_back(self) -> None:
        if self._is_busy:
            self.notify("Operation in progress. Please wait.", severity="warning")
            return
        self.app.pop_screen()

    def action_refresh_all(self) -> None:
        if self._is_busy:
            return
        if not self._repo_path:
            self._resolve_active_repo()
        if not self._repo_path:
            self.query_one("#manage-feedback", Static).update("[red]Cannot list environments without a valid repository.[/red]")
            return

        self._is_busy = True
        self.query_one("#manage-spinner", LoadingIndicator).display = True
        self.query_one("#btn-refresh", Button).disabled = True
        self.query_one("#manage-feedback", Static).update("[dim]Reading registered environments...[/dim]")
        self._load_envs_worker(self._repo_path)

    @work(exclusive=True, thread=True)
    def _load_envs_worker(self, repo_path: Path) -> None:
        registered = list_registered_environments(repo_path)
        statuses: dict[str, Any] = {}
        for env in registered:
            name = env.get("name", "")
            if name:
                try:
                    res = inspect_environment_live(name, repo_path)
                    statuses[name] = res
                except Exception as exc:
                    statuses[name] = None
        self.app.call_from_thread(self._handle_envs_loaded, registered, statuses)

    def _handle_envs_loaded(self, registered: list[dict[str, Any]], statuses: dict[str, Any]) -> None:
        if not self.is_mounted:
            return
        self._is_busy = False
        self.query_one("#manage-spinner", LoadingIndicator).display = False
        self.query_one("#btn-refresh", Button).disabled = False

        self._envs_cache = registered
        self._live_statuses = statuses

        table = self.query_one("#envs-table", DataTable)
        table.clear()

        feedback = self.query_one("#manage-feedback", Static)
        empty_box = self.query_one("#manage-empty-container", Container)

        if not registered:
            feedback.update("No registered environments found.")
            empty_box.display = True
            self.query_one("#btn-start", Button).disabled = True
            self.query_one("#btn-stop", Button).disabled = True
            self.query_one("#btn-inspect", Button).disabled = True
            return

        empty_box.display = False
        feedback.update(f"Found {len(registered)} registered environment(s).")

        for env in registered:
            name = env.get("name", "")
            st = statuses.get(name)

            if st is None:
                state_str = "[dim]UNKNOWN (offline)[/dim]"
                health_str = "[dim]UNKNOWN[/dim]"
                url_str = "-"
            else:
                raw_st = getattr(st, "status", "UNKNOWN").upper()
                if raw_st == "RUNNING":
                    state_str = "[bold green]RUNNING[/bold green]"
                elif raw_st == "STOPPED":
                    state_str = "[dim]STOPPED[/dim]"
                elif raw_st == "PARTIAL":
                    state_str = "[yellow]PARTIAL[/yellow]"
                else:
                    state_str = f"[dim]{raw_st}[/dim]"

                raw_hl = getattr(st, "health", "UNKNOWN").upper()
                if raw_hl == "HEALTHY":
                    health_str = "[bold green]HEALTHY[/bold green]"
                elif raw_hl == "UNHEALTHY":
                    health_str = "[bold red]UNHEALTHY[/bold red]"
                elif raw_hl == "STARTING":
                    health_str = "[yellow]STARTING[/yellow]"
                else:
                    health_str = f"[dim]{raw_hl}[/dim]"

                url_val = getattr(st, "url", None)
                url_str = url_val if url_val else "-"

            worktree_str = str(env.get("worktree_path", ""))
            table.add_row(name, state_str, health_str, url_str, worktree_str, key=name)

        if registered:
            self.query_one("#btn-start", Button).disabled = False
            self.query_one("#btn-stop", Button).disabled = False
            self.query_one("#btn-inspect", Button).disabled = False

    def _get_selected_env_name(self) -> str | None:
        table = self.query_one("#envs-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
            row_key = cell_key.row_key.value
            return str(row_key) if row_key else None
        except Exception:
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-back":
            self.action_go_back()
        elif bid == "btn-refresh":
            self.action_refresh_all()
        elif bid == "btn-start":
            self.action_start_selected()
        elif bid == "btn-stop":
            self.action_stop_selected()
        elif bid == "btn-inspect":
            self.action_inspect_selected()

    def action_start_selected(self) -> None:
        name = self._get_selected_env_name()
        if not name or self._is_busy:
            return

        self._is_busy = True
        self.query_one("#manage-spinner", LoadingIndicator).display = True
        self._set_action_buttons_disabled(True)
        self.query_one("#manage-feedback", Static).update(f"[yellow]Starting environment '{name}'...[/yellow]")
        self._start_env_worker(name, self._repo_path)

    @work(exclusive=True, thread=True)
    def _start_env_worker(self, env_name: str, repo_path: Path | None) -> None:
        result = start_env(env_name, repo_path)
        self.app.call_from_thread(self._handle_action_completed, "start", result)

    def action_stop_selected(self) -> None:
        name = self._get_selected_env_name()
        if not name or self._is_busy:
            return

        msg = (
            f"Stop containers for environment '{name}'?\n\n"
            "• Named volumes and database data are preserved.\n"
            "• Worktree files and Git branch remain untouched.\n"
            "• Containers can be restarted at any time using Start."
        )

        def _on_confirm(confirmed: bool) -> None:
            if confirmed:
                self._execute_stop(name)

        self.app.push_screen(ConfirmModal("Confirm Stop Environment", msg), _on_confirm)

    def _execute_stop(self, name: str) -> None:
        self._is_busy = True
        self.query_one("#manage-spinner", LoadingIndicator).display = True
        self._set_action_buttons_disabled(True)
        self.query_one("#manage-feedback", Static).update(f"[yellow]Stopping environment '{name}'...[/yellow]")
        self._stop_env_worker(name, self._repo_path)

    @work(exclusive=True, thread=True)
    def _stop_env_worker(self, env_name: str, repo_path: Path | None) -> None:
        result = stop_env(env_name, repo_path)
        self.app.call_from_thread(self._handle_action_completed, "stop", result)

    def _handle_action_completed(self, action_type: str, result: Any) -> None:
        if not self.is_mounted:
            return
        self._is_busy = False
        self.query_one("#manage-spinner", LoadingIndicator).display = False
        self._set_action_buttons_disabled(False)
        feedback = self.query_one("#manage-feedback", Static)

        if result.success:
            if action_type == "start":
                msg = f"[green]Environment '{result.environment}' started successfully.[/green]"
                if result.url:
                    msg += f" Verified URL: [bold]{result.url}[/bold]"
                self.notify(f"Environment '{result.environment}' started.", severity="information")
            else:
                msg = (
                    f"[green]Environment '{result.environment}' stopped.[/green] "
                    f"Named volume '{result.volume_preserved or 'data'}' preserved."
                )
                self.notify(f"Environment '{result.environment}' stopped.", severity="information")
            feedback.update(msg)
            # Re-fetch state
            self.action_refresh_all()
        else:
            err_msg = f"[red]Action '{action_type}' failed:[/red] {result.error}"
            feedback.update(err_msg)
            self.notify(f"Failed to {action_type} environment: {result.error}", severity="error")

    def action_inspect_selected(self) -> None:
        name = self._get_selected_env_name()
        if not name:
            return

        status = self._live_statuses.get(name)
        cached = next((e for e in self._envs_cache if e.get("name") == name), {})

        lines = [
            f"[bold]Environment Name:[/bold]       {name}",
            f"[bold]Worktree Path:[/bold]          {cached.get('worktree_path', '-')}",
            f"[bold]Git Branch:[/bold]             {cached.get('branch', '-')}",
            f"[bold]Base Revision:[/bold]          {cached.get('base_ref', '-')} ({cached.get('base_commit', '-')[:8] if cached.get('base_commit') else '-'})",
            f"[bold]Compose Project:[/bold]        {cached.get('compose_project_name', '-')}",
            f"[bold]Dedicated Volume:[/bold]       {cached.get('dedicated_volume', '-')}",
            f"[bold]Application Service:[/bold]    {cached.get('app_service', '-')}",
            f"[bold]PostgreSQL Service:[/bold]     {cached.get('db_service', '-')}",
            f"[bold]Configured App Port:[/bold]    {cached.get('app_port', '-')}",
            "",
            "[bold underline]Live Runtime State[/bold underline]",
        ]

        if status is None:
            lines.append("[dim]Live verification unavailable (Docker not reachable or check pending).[/dim]")
        else:
            lines.append(f"Observed State:        {getattr(status, 'status', 'UNKNOWN')}")
            lines.append(f"Health Status:         {getattr(status, 'health', 'UNKNOWN')}")
            lines.append(f"Allocated Host Port:   {getattr(status, 'host_port', 'None')}")
            lines.append(f"Verified App URL:      {getattr(status, 'url', 'None')}")
            lines.append(f"Ownership Verified:    {getattr(status, 'ownership_verified', False)}")
            lines.append(f"State Freshness:       {'STALE (cache mismatch)' if getattr(status, 'stale_runtime_state', False) else 'FRESH'}")

            containers = getattr(status, "containers", [])
            lines.append("")
            lines.append(f"[bold underline]Containers ({len(containers)})[/bold underline]")
            if not containers:
                lines.append("[dim]No running containers found for this project.[/dim]")
            else:
                for c in containers:
                    c_name = c.get("Name") or c.get("name") or "unknown"
                    c_svc = c.get("Service") or c.get("service") or "-"
                    c_state = c.get("State") or c.get("state") or "-"
                    c_hl = c.get("health") or "-"
                    lines.append(f"• {c_name} | svc: {c_svc} | state: {c_state} | health: {c_hl}")

        self.app.push_screen(DetailsModal(f"Environment: {name}", "\n".join(lines)))

    def _set_action_buttons_disabled(self, disabled: bool) -> None:
        self.query_one("#btn-start", Button).disabled = disabled
        self.query_one("#btn-stop", Button).disabled = disabled
        self.query_one("#btn-inspect", Button).disabled = disabled
        self.query_one("#btn-refresh", Button).disabled = disabled
