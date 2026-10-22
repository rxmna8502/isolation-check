"""Updates Screen for isolation-check TUI."""
from __future__ import annotations

from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    LoadingIndicator,
    RadioSet,
    RadioButton,
    Static,
)

from isolation_check.tui.adapters import UpdateCheckResult, fetch_pypi_update
from isolation_check.version import get_package_version


class UpdatesScreen(Screen[None]):
    """Screen for checking official PyPI updates without automatic network queries."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("u", "check_updates", "Check PyPI"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._is_busy: bool = False
        self._last_result: UpdateCheckResult | None = None

    def compose(self) -> ComposeResult:
        cur_version = get_package_version()
        yield Header()
        with VerticalScroll(classes="screen-scroll"):
            yield Static("Check for Updates", classes="section-title")
            yield Static(
                "Query official PyPI metadata to check if a newer version is available. "
                "Network access is [bold]only initiated upon explicit user action[/bold].",
                classes="section-subtitle",
            )

            # Current Version Card
            with Container(classes="card"):
                yield Static(f"Installed Version: [bold]{cur_version}[/bold]")
                yield Static(
                    "Network isolation policy: No automatic background network queries are performed.\n"
                    "Click 'Check PyPI Now' below to query the official PyPI package index.",
                    classes="text-muted",
                )

            with Horizontal(classes="action-row"):
                yield Button("Check PyPI Now", variant="primary", id="btn-check-updates")
                yield Button("Back to Home", variant="default", id="btn-back")

            yield LoadingIndicator(id="update-spinner")
            yield Static(id="update-status-msg", classes="text-muted")

            # Result Section (hidden initially)
            with Container(id="update-result-card", classes="card"):
                yield Static("Release Status", classes="card-header")
                yield Static(id="update-details-text", classes="monospace-box")

            # Upgrade Instructions Card
            with Container(id="upgrade-instructions-card", classes="card"):
                yield Static("Upgrade Instructions by Package Manager", classes="card-header")
                yield Static(
                    "Select your installation method below to view the appropriate safe command.\n"
                    "[bold yellow]Note:[/bold yellow] Do not run self-updates inside this active session. "
                    "Exit the TUI and execute the upgrade in your terminal.",
                    classes="text-muted",
                )
                with RadioSet(id="pkg-manager-radio"):
                    yield RadioButton("pip (virtual environment)", id="radio-pip", value=True)
                    yield RadioButton("pipx (isolated tool)", id="radio-pipx")
                    yield RadioButton("uv (uv tool)", id="radio-uv")
                yield Static(id="upgrade-command-display", classes="monospace-box")

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#update-spinner", LoadingIndicator).display = False
        self.query_one("#update-result-card", Container).display = False
        self.query_one("#upgrade-instructions-card", Container).display = False
        self._update_command_box("pip")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_check_updates(self) -> None:
        self._start_update_check()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-back":
            self.action_go_back()
        elif bid == "btn-check-updates":
            self._start_update_check()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        bid = event.pressed.id
        if bid == "radio-pip":
            self._update_command_box("pip")
        elif bid == "radio-pipx":
            self._update_command_box("pipx")
        elif bid == "radio-uv":
            self._update_command_box("uv")

    def _update_command_box(self, manager: str) -> None:
        res = self._last_result
        if res and res.upgrade_commands and manager in res.upgrade_commands:
            cmd = res.upgrade_commands[manager]
        else:
            if manager == "pip":
                cmd = 'pip install --upgrade "isolation-check"'
            elif manager == "pipx":
                cmd = 'pipx upgrade "isolation-check"'
            else:
                cmd = 'uv tool upgrade "isolation-check"'

        lines = [
            f"# Upgrade command for {manager}:",
            cmd,
        ]
        self.query_one("#upgrade-command-display", Static).update("\n".join(lines))

    def _start_update_check(self) -> None:
        if self._is_busy:
            return
        self._is_busy = True
        self.query_one("#update-spinner", LoadingIndicator).display = True
        self.query_one("#btn-check-updates", Button).disabled = True
        self.query_one("#update-status-msg", Static).update("[dim]Connecting to https://pypi.org/pypi/isolation-check/json...[/dim]")
        self._fetch_updates_worker()

    @work(exclusive=True, thread=True)
    def _fetch_updates_worker(self) -> None:
        result = fetch_pypi_update(timeout=8.0)
        self.app.call_from_thread(self._handle_update_completed, result)

    def _handle_update_completed(self, result: UpdateCheckResult) -> None:
        if not self.is_mounted:
            return
        self._is_busy = False
        self.query_one("#update-spinner", LoadingIndicator).display = False
        self.query_one("#btn-check-updates", Button).disabled = False

        self._last_result = result
        status_msg = self.query_one("#update-status-msg", Static)
        result_card = self.query_one("#update-result-card", Container)
        instructions_card = self.query_one("#upgrade-instructions-card", Container)
        details_text = self.query_one("#update-details-text", Static)

        result_card.display = True
        instructions_card.display = True

        if result.is_offline or result.error:
            status_msg.update(f"[yellow]Update Check Notice:[/yellow] {result.error}")
            lines = [
                f"Current Version:   {result.current_version}",
                f"Status:            [yellow]Check Incomplete[/yellow]",
                f"Reason:            {result.error}",
                f"Project URL:       {result.project_url}",
                "",
                "Offline / Unreachable: Could not reach PyPI. Verify your internet connection.",
            ]
            details_text.update("\n".join(lines))
            self.notify("Could not complete update check.", severity="warning")
            return

        lines = [
            f"Current Version:   {result.current_version}",
            f"Latest on PyPI:    {result.latest_version or 'N/A'}",
            f"Latest Stable:     {result.latest_stable or 'None'}",
            f"Public PyPI Link:  {result.project_url}",
            "",
        ]

        if result.is_outdated:
            status_msg.update("[bold yellow]A new version of isolation-check is available.[/bold yellow]")
            lines.append(f"[bold yellow]Update Available:[/bold yellow] v{result.latest_version}")
            if result.is_prerelease:
                lines.append("Note: You are currently on a pre-release version. Newer versions were found.")
            self.notify(f"Update available: v{result.latest_version}", severity="information")
        else:
            status_msg.update("[bold green]isolation-check is up to date![/bold green]")
            lines.append("[bold green]Up to Date:[/bold green] You are running the newest published version.")
            self.notify("isolation-check is up to date.", severity="information")

        details_text.update("\n".join(lines))

        # Re-render active manager command
        active_radio = "pip"
        if self.query_one("#radio-pipx", RadioButton).value:
            active_radio = "pipx"
        elif self.query_one("#radio-uv", RadioButton).value:
            active_radio = "uv"
        self._update_command_box(active_radio)
