"""Collisions Screen for isolation-check TUI."""
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
    Input,
    Label,
    LoadingIndicator,
    Static,
)

from isolation_check.tui.adapters import run_collision_check
from isolation_check.tui.screens.modals import DetailsModal


class CollisionsScreen(Screen[None]):
    """Screen for checking port and volume collisions between two environments."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("c", "run_audit", "Audit"),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._is_busy: bool = False
        self._findings_cache: list[Any] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="screen-scroll"):
            yield Static("Check for Collisions", classes="section-title")
            yield Static(
                "Audit two environment configurations for shared Docker volumes or host port overlaps.",
                classes="section-subtitle",
            )

            # Input fields
            with Container(classes="card"):
                yield Label("Environment 1 (Left Path - directory or compose.yaml) *")
                yield Input(placeholder="/path/to/env-a or /path/to/env-a/docker-compose.yaml", id="field-left-path")

                yield Label("Environment 2 (Right Path - directory or compose.yaml) *")
                yield Input(placeholder="/path/to/env-b or /path/to/env-b/docker-compose.yaml", id="field-right-path")

            with Horizontal(classes="action-row"):
                yield Button("Run Collision Audit", variant="primary", id="btn-run-audit")
                yield Button("Back to Home", variant="default", id="btn-back")

            yield LoadingIndicator(id="collision-spinner")
            yield Static(id="collision-feedback", classes="text-muted")

            # Findings Table
            with Container(id="findings-container", classes="card"):
                yield Static("Audit Findings", classes="card-header")
                yield DataTable(id="findings-table")
                yield Static(
                    "\n[bold yellow]Safety & Scope Limitation:[/bold yellow]\n"
                    "• Bind mounts, database data, container processes, and runtime state are not fully evaluated by this static audit.\n"
                    "• A clean result (PASS) indicates no declared port or volume overlaps, but [bold]does NOT prove complete environment isolation[/bold].",
                    classes="card-header",
                )

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#collision-spinner", LoadingIndicator).display = False
        self.query_one("#findings-container", Container).display = False

        table = self.query_one("#findings-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Rule ID", "Status", "Severity", "Summary Message")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_run_audit(self) -> None:
        self._start_audit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-back":
            self.action_go_back()
        elif bid == "btn-run-audit":
            self._start_audit()

    def _start_audit(self) -> None:
        if self._is_busy:
            return

        left = self.query_one("#field-left-path", Input).value.strip()
        right = self.query_one("#field-right-path", Input).value.strip()
        feedback = self.query_one("#collision-feedback", Static)

        if not left or not right:
            feedback.update("[red]Error: Both Left and Right environment paths are required.[/red]")
            return

        feedback.update("[dim]Parsing compose files and auditing port/volume rules...[/dim]")
        self.query_one("#btn-run-audit", Button).disabled = True
        self.query_one("#collision-spinner", LoadingIndicator).display = True
        self._is_busy = True
        self._audit_worker(left, right)

    @work(exclusive=True, thread=True)
    def _audit_worker(self, left: str, right: str) -> None:
        report, findings, error = run_collision_check(left, right)
        self.app.call_from_thread(self._handle_audit_completed, report, findings, error)

    def _handle_audit_completed(self, report: Any, findings: list[Any], error: str | None) -> None:
        if not self.is_mounted:
            return
        self._is_busy = False
        self.query_one("#btn-run-audit", Button).disabled = False
        self.query_one("#collision-spinner", LoadingIndicator).display = False

        feedback = self.query_one("#collision-feedback", Static)
        container = self.query_one("#findings-container", Container)
        table = self.query_one("#findings-table", DataTable)
        table.clear()
        self._findings_cache = findings

        if error:
            feedback.update(f"[red]Audit Failed:[/red] {error}")
            container.display = False
            return

        feedback.update(f"[green]Audit complete.[/green] Evaluated {len(findings)} rule finding(s).")
        container.display = True

        for idx, f in enumerate(findings):
            status = getattr(f, "status", "unknown").lower()
            if status == "pass":
                status_styled = "[bold green]PASS[/bold green]"
            elif status == "warn":
                status_styled = "[bold yellow]WARN[/bold yellow]"
            elif status == "fail":
                status_styled = "[bold red]FAIL[/bold red]"
            else:
                status_styled = "[dim]UNKNOWN[/dim]"

            sev = getattr(f, "severity", "info").upper()
            rule_id = getattr(f, "rule_id", "UNKNOWN")
            msg = getattr(f, "message", "")

            table.add_row(rule_id, status_styled, sev, msg, key=str(idx))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        try:
            idx = int(str(event.row_key.value))
            finding = self._findings_cache[idx]
        except Exception:
            return

        lines = [
            f"[bold]Rule ID:[/bold]     {getattr(finding, 'rule_id', '-')}",
            f"[bold]Status:[/bold]      {getattr(finding, 'status', '-').upper()}",
            f"[bold]Severity:[/bold]    {getattr(finding, 'severity', '-').upper()}",
            f"[bold]Confidence:[/bold]  {getattr(finding, 'confidence', '-')}",
            f"[bold]Basis:[/bold]       {getattr(finding, 'basis', '-')}",
            "",
            f"[bold]Message:[/bold]\n{getattr(finding, 'message', '-')}",
            "",
            f"[bold]Environments:[/bold] {', '.join(getattr(finding, 'environments', []))}",
        ]
        evidence = getattr(finding, "evidence", None)
        if evidence:
            lines.append("")
            lines.append("[bold]Evidence:[/bold]")
            import json
            try:
                if hasattr(evidence, "__iter__") and not isinstance(evidence, str):
                    for e in evidence:
                        lines.append(f"• {e}")
                else:
                    lines.append(str(evidence))
            except Exception:
                lines.append(str(evidence))

        self.app.push_screen(DetailsModal(f"Finding: {getattr(finding, 'rule_id', 'Details')}", "\n".join(lines)))
