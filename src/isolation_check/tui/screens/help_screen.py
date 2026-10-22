"""Help Screen for isolation-check TUI."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static


class HelpScreen(Screen[None]):
    """Documentation screen explaining isolation-check workflows, safety rules, and CLI examples."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="screen-scroll"):
            yield Static("Documentation & Workflow Guide", classes="section-title")
            yield Static(
                "Learn the core lifecycle workflow, safety guarantees, and direct CLI usage.",
                classes="section-subtitle",
            )

            # Workflow Card
            with Container(classes="card"):
                yield Static("The Isolation Lifecycle Workflow", classes="card-header")
                workflow_diagram = (
                    "┌────────┐     ┌──────┐     ┌────────┐     ┌──────┐\n"
                    "│ create │ ──> │  up  │ ──> │ status │ ──> │ down │\n"
                    "└────────┘     └──────┘     └────────┘     └──────┘"
                )
                yield Static(workflow_diagram, classes="monospace-box")
                yield Static(
                    "\n"
                    "1. [bold cyan]create[/bold cyan]\n"
                    "   • Validates the base revision, branch, and Compose topology.\n"
                    "   • Creates a separate Git worktree and checkouts the target branch.\n"
                    "   • Generates an override compose file with loopback-only (127.0.0.1) port binding,\n"
                    "     a dedicated PostgreSQL volume, and a unique project namespace.\n"
                    "   • Redacts all database credentials and secret environment variables.\n\n"
                    "2. [bold cyan]up[/bold cyan]\n"
                    "   • Allocates an unconflicted candidate loopback host port.\n"
                    "   • Starts containers in the background via Docker Compose.\n"
                    "   • Waits for database readiness and verifies HTTP accessibility of the app.\n\n"
                    "3. [bold cyan]status[/bold cyan]\n"
                    "   • Queries freshly observed Docker container states.\n"
                    "   • Clearly separates RUNNING state from HEALTHY state.\n"
                    "   • Displays allocated port, verified application URL, and container health.\n\n"
                    "4. [bold cyan]down[/bold cyan]\n"
                    "   • Safely stops running containers without deleting data.\n"
                    "   • [bold]Named volumes and PostgreSQL data are strictly preserved.[/bold]\n"
                    "   • Worktree files, git commits, and logs remain completely intact.\n"
                )

            # Direct CLI Examples
            with Container(classes="card"):
                yield Static("Direct CLI Usage", classes="card-header")
                yield Static(
                    "All functionality can also be executed directly from terminal commands or CI scripts:\n",
                    classes="text-muted",
                )
                cli_examples = (
                    "# Static collision check between two environments:\n"
                    "isolation-check check /path/to/env-a /path/to/env-b\n\n"
                    "# Create an isolated environment (dry run first):\n"
                    "isolation-check create feat-login --dry-run\n"
                    "isolation-check create feat-login --repo . --app-service web --db-service db\n\n"
                    "# Start containers and verify endpoint:\n"
                    "isolation-check up feat-login\n\n"
                    "# Inspect environment state:\n"
                    "isolation-check status feat-login\n\n"
                    "# Stop containers safely:\n"
                    "isolation-check down feat-login\n\n"
                    "# Output machine-readable JSON:\n"
                    "isolation-check status feat-login --format json"
                )
                yield Static(cli_examples, classes="monospace-box")

            # Scope & Safety Boundaries
            with Container(classes="card"):
                yield Static("Scope & Safety Boundaries", classes="card-header")
                yield Static(
                    "• [bold]Supported Scope:[/bold] Linux Docker engine, Docker Compose v2.20+, PostgreSQL service, "
                    "and one primary application container.\n"
                    "• [bold]Loopback Isolation:[/bold] Ports are bound strictly to 127.0.0.1. Containers are never exposed publicly.\n"
                    "• [bold]Non-Destructive:[/bold] The tool will never run volume wipes, migrations, database clones, or container execs.\n"
                    "• [bold]Audit Limitations:[/bold] A clean collision check (PASS) verifies declared volumes and ports, but does NOT "
                    "guarantee total isolation if workloads share host bind mounts or external network interfaces."
                )

            with Horizontal(classes="action-row"):
                yield Button("Back to Home", variant="primary", id="btn-back")

        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.action_go_back()
