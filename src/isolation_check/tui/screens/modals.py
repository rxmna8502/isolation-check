"""Reusable modal screens for isolation-check TUI."""
from __future__ import annotations

from typing import Callable
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ConfirmModal(ModalScreen[bool]):
    """Generic confirmation dialog."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Confirm"),
    ]

    def __init__(
        self,
        title: str,
        message: str,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        warning_text: str | None = None,
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.message = message
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label
        self.warning_text = warning_text

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Label(self.dialog_title, classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(self.message)
                if self.warning_text:
                    yield Static(f"\n[bold yellow]Notice:[/bold yellow] {self.warning_text}")
            with Horizontal(classes="button-row"):
                yield Button(self.confirm_label, variant="primary", id="btn-confirm")
                yield Button(self.cancel_label, variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class DetailsModal(ModalScreen[None]):
    """Read-only details modal viewer."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def __init__(self, title: str, content: str) -> None:
        super().__init__()
        self.modal_title = title
        self.content = content

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Label(self.modal_title, classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(self.content)
            with Horizontal(classes="button-row"):
                yield Button("Close", variant="primary", id="btn-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class OverwriteConfigModal(ModalScreen[bool]):
    """Modal showing proposed configuration changes before overwriting isolation-check.toml."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, existing_content: str, proposed_content: str) -> None:
        super().__init__()
        self.existing_content = existing_content
        self.proposed_content = proposed_content

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Label("Overwrite Existing Configuration?", classes="modal-title")
            with VerticalScroll(classes="modal-body"):
                yield Static(
                    "[bold yellow]Warning:[/bold yellow] 'isolation-check.toml' already exists in this repository.\n"
                    "Review proposed configuration changes below before confirming:"
                )
                yield Label("\n[bold]Proposed New Configuration:[/bold]")
                yield Static(self.proposed_content, classes="code-preview")
                yield Label("\n[bold]Current Existing Configuration:[/bold]")
                yield Static(self.existing_content, classes="code-preview")
            with Horizontal(classes="button-row"):
                yield Button("Confirm & Overwrite", variant="error", id="btn-overwrite")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-overwrite":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
