"""Prerequisites Screen for isolation-check TUI."""
from __future__ import annotations

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
    LoadingIndicator,
    Static,
)

from isolation_check.tui.adapters import check_all_prerequisites
from isolation_check.tui.screens.modals import DetailsModal


class PrereqsScreen(Screen[None]):
    """Screen for verifying runtime prerequisites and viewing actionable guidance."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "rerun_checks", "Re-run"),
        ("i", "inspect_selected", "Details"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._is_busy: bool = False
        self._results_cache: list[Any] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="screen-scroll"):
            yield Static("Prerequisite Checks", classes="section-title")
            yield Static(
                "Verify required developer tools, Docker capabilities, and environment runtime boundaries.",
                classes="section-subtitle",
            )

            # Table
            with Container(classes="card"):
                yield DataTable(id="prereqs-table")

            # Selected item guidance
            with Container(id="guidance-container", classes="card"):
                yield Static("Item Guidance & Repair Steps", classes="card-header")
                yield Static(id="guidance-text", classes="monospace-box")

            # Action buttons
            with Horizontal(classes="action-row"):
                yield Button("Re-run Checks", variant="primary", id="btn-rerun")
                yield Button("View Details", variant="default", id="btn-details")
                yield Button("Back to Home", variant="default", id="btn-back")

            yield LoadingIndicator(id="prereq-spinner")

            # Scope Notice
            with Container(classes="card"):
                yield Static(
                    "[bold yellow]Supported Runtime Scope Notice:[/bold yellow]\n"
                    "• Target Engine: Linux Docker engine with Docker Compose v2.20+ supporting sequence overrides.\n"
                    "• Supported Topologies: Exactly one application service and one PostgreSQL service.\n"
                    "• Cross-Platform Note: Opening this TUI on Windows or macOS does [bold]NOT[/bold] imply native "
                    "Docker container execution support on non-Linux hosts.",
                    classes="card-header",
                )

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prereq-spinner", LoadingIndicator).display = False
        table = self.query_one("#prereqs-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Component", "Status", "Detected Version", "Requirement")

        self.action_rerun_checks()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_rerun_checks(self) -> None:
        if self._is_busy:
            return
        self._is_busy = True
        self.query_one("#prereq-spinner", LoadingIndicator).display = True
        self.query_one("#btn-rerun", Button).disabled = True
        self.query_one("#guidance-text", Static).update("[dim]Probing system prerequisites...[/dim]")
        self._run_checks_worker()

    @work(exclusive=True, thread=True)
    def _run_checks_worker(self) -> None:
        results = check_all_prerequisites()
        self.app.call_from_thread(self._handle_checks_completed, results)

    def _handle_checks_completed(self, results: list[Any]) -> None:
        if not self.is_mounted:
            return
        self._is_busy = False
        try:
            self.query_one("#prereq-spinner", LoadingIndicator).display = False
            self.query_one("#btn-rerun", Button).disabled = False
        except Exception:
            return

        self._results_cache = results
        table = self.query_one("#prereqs-table", DataTable)
        table.clear()

        has_failures = False
        for idx, item in enumerate(results):
            status = getattr(item, "status", "FAIL").upper()
            if status == "PASS":
                status_styled = "[bold green]PASS[/bold green]"
            elif status == "WARN":
                status_styled = "[bold yellow]WARN[/bold yellow]"
            elif status == "INFO":
                status_styled = "[bold blue]INFO[/bold blue]"
            else:
                status_styled = "[bold red]FAIL[/bold red]"
                has_failures = True

            table.add_row(
                getattr(item, "title", "-"),
                status_styled,
                getattr(item, "current", "-"),
                getattr(item, "required", "-"),
                key=str(idx),
            )

        if results:
            self._update_guidance(0)

        if has_failures:
            self.notify("One or more prerequisites are missing or failed.", severity="warning")
        else:
            self.notify("All prerequisite checks passed.", severity="information")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            idx = int(str(event.row_key.value))
            self._update_guidance(idx)
        except Exception:
            pass

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            if event.row_key:
                idx = int(str(event.row_key.value))
                self._update_guidance(idx)
        except Exception:
            pass

    def _update_guidance(self, idx: int) -> None:
        if 0 <= idx < len(self._results_cache):
            item = self._results_cache[idx]
            lines = [
                f"[bold]{getattr(item, 'title', '')}[/bold] ({getattr(item, 'status', '')})",
                f"Current:      {getattr(item, 'current', '')}",
                f"Required:     {getattr(item, 'required', '')}",
                "",
                f"[bold]Actionable Guidance:[/bold]\n{getattr(item, 'guidance', 'No guidance provided.')}",
            ]
            details = getattr(item, "details", None)
            if details:
                lines.append(f"\n[bold]Output Details:[/bold]\n{details}")
            self.query_one("#guidance-text", Static).update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-back":
            self.action_go_back()
        elif bid == "btn-rerun":
            self.action_rerun_checks()
        elif bid == "btn-details":
            self.action_inspect_selected()

    def action_inspect_selected(self) -> None:
        table = self.query_one("#prereqs-table", DataTable)
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
            idx = int(str(cell_key.row_key.value))
            if 0 <= idx < len(self._results_cache):
                item = self._results_cache[idx]
                lines = [
                    f"[bold]Component:[/bold]   {getattr(item, 'title', '')}",
                    f"[bold]Status:[/bold]      {getattr(item, 'status', '')}",
                    f"[bold]Current:[/bold]     {getattr(item, 'current', '')}",
                    f"[bold]Required:[/bold]    {getattr(item, 'required', '')}",
                    "",
                    f"[bold]Guidance:[/bold]\n{getattr(item, 'guidance', '')}",
                ]
                details = getattr(item, "details", None)
                if details:
                    lines.append(f"\n[bold]Diagnostic Output:[/bold]\n{details}")
                self.app.push_screen(DetailsModal(f"Prerequisite: {getattr(item, 'title', '')}", "\n".join(lines)))
        except Exception:
            pass
