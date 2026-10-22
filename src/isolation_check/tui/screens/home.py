"""Home screen for isolation-check TUI."""
from __future__ import annotations

from pathlib import Path
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from isolation_check.tui.adapters import resolve_local_repo
from isolation_check.version import get_package_version


MENU_ITEMS = [
    ("get_started", "1. Get started", "Guided setup wizard to configure isolation-check.toml"),
    ("create_env", "2. Create an environment", "Safely prepare a Git worktree with dedicated PostgreSQL storage"),
    ("manage_envs", "3. Manage environments", "Inspect status, start (up), and non-destructively stop (down) environments"),
    ("collisions", "4. Check for collisions", "Audit two Compose configurations for volume or port collisions"),
    ("prereqs", "5. Check prerequisites", "Verify Python, Git, Docker, Compose version, and sequence override tags"),
    ("updates", "6. Check for updates", "Query official PyPI metadata and view copyable upgrade commands"),
    ("help", "7. Help", "Workflow guide, safety boundaries, and CLI command reference"),
    ("exit", "8. Exit", "Quit the isolation-check application"),
]


class HomeMenuListItem(ListItem):
    """A selectable menu option item."""

    def __init__(self, key: str, title: str, description: str) -> None:
        super().__init__()
        self.item_key = key
        self.item_title = title
        self.item_desc = description

    def compose(self) -> ComposeResult:
        with Vertical(classes="menu-item"):
            yield Label(self.item_title, classes="menu-item-title")
            yield Label(self.item_desc, classes="menu-item-desc")


class HomeScreen(Screen[None]):
    """Main menu navigation screen."""

    BINDINGS = [
        Binding("1", "select_item('get_started')", "Get started", show=False),
        Binding("2", "select_item('create_env')", "Create environment", show=False),
        Binding("3", "select_item('manage_envs')", "Manage environments", show=False),
        Binding("4", "select_item('collisions')", "Check collisions", show=False),
        Binding("5", "select_item('prereqs')", "Check prerequisites", show=False),
        Binding("6", "select_item('updates')", "Check updates", show=False),
        Binding("7", "select_item('help')", "Help", show=False),
        Binding("8", "select_item('exit')", "Exit", show=False),
        Binding("q", "quit_app", "Quit", priority=True),
    ]

    def compose(self) -> ComposeResult:
        ver = get_package_version()
        repo_root, _ = resolve_local_repo()
        repo_label = str(repo_root) if repo_root else "No Git repository found in CWD"

        with Horizontal(id="header-container"):
            yield Label(f"isolation-check v{ver}", id="header-title")
            yield Label(f"Repo: {repo_label}", id="header-status")

        with VerticalScroll(classes="main-scroll"):
            yield Label("Local Git Worktree Environment Isolation", classes="screen-title")
            yield Label(
                "Prepare and verify isolated development environments for parallel Git worktrees.\n"
                "Use the arrow keys or number shortcuts to navigate, and press Enter to select.",
                classes="screen-desc",
            )

            with ListView(id="menu-list"):
                for key, title, desc in MENU_ITEMS:
                    yield HomeMenuListItem(key, title, desc)

        with Horizontal(id="footer-hints"):
            yield Label("[↑/↓] Navigate   [Enter] Select   [1-8] Jump   [q] Quit")

    def on_mount(self) -> None:
        menu = self.query_one("#menu-list", ListView)
        menu.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, HomeMenuListItem):
            self._handle_navigation(event.item.item_key)

    def action_select_item(self, key: str) -> None:
        self._handle_navigation(key)

    def action_quit_app(self) -> None:
        self.app.exit(0)

    def _handle_navigation(self, key: str) -> None:
        if key == "get_started":
            self.app.push_screen("get_started")
        elif key == "create_env":
            self.app.push_screen("create_env")
        elif key == "manage_envs":
            self.app.push_screen("manage_envs")
        elif key == "collisions":
            self.app.push_screen("collisions")
        elif key == "prereqs":
            self.app.push_screen("prereqs")
        elif key == "updates":
            self.app.push_screen("updates")
        elif key == "help":
            self.app.push_screen("help")
        elif key == "exit":
            self.app.exit(0)
