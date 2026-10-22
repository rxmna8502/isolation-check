"""Main Textual application for isolation-check."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App

from isolation_check.tui.adapters import resolve_local_repo
from isolation_check.tui.screens.collisions import CollisionsScreen
from isolation_check.tui.screens.create_env import CreateEnvScreen
from isolation_check.tui.screens.get_started import GetStartedScreen
from isolation_check.tui.screens.help_screen import HelpScreen
from isolation_check.tui.screens.home import HomeScreen
from isolation_check.tui.screens.manage_envs import ManageEnvsScreen
from isolation_check.tui.screens.prereqs import PrereqsScreen
from isolation_check.tui.screens.updates import UpdatesScreen
from isolation_check.tui.styles import APP_CSS
from isolation_check.version import get_package_version


class IsolationCheckApp(App[None]):
    """Polished, keyboard-driven Terminal User Interface for isolation-check."""

    TITLE = "isolation-check"
    SUB_TITLE = "Local Git Worktree Environment Isolation"
    CSS = APP_CSS

    SCREENS = {
        "home": HomeScreen,
        "get_started": GetStartedScreen,
        "create_env": CreateEnvScreen,
        "manage_envs": ManageEnvsScreen,
        "collisions": CollisionsScreen,
        "prereqs": PrereqsScreen,
        "updates": UpdatesScreen,
        "help": HelpScreen,
    }

    BINDINGS = [
        ("q", "quit_app", "Quit"),
    ]

    def __init__(self, repo_hint: Path | str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.active_repo: Path | None = None
        if repo_hint:
            self.active_repo = Path(repo_hint).resolve()
        else:
            self.active_repo, _ = resolve_local_repo()

    def on_mount(self) -> None:
        self.title = f"isolation-check v{get_package_version()}"
        self.push_screen("home")

    def action_quit_app(self) -> None:
        self.exit(0)


def run_tui(repo_hint: Path | str | None = None) -> int:
    """Entry point to launch the interactive TUI application."""
    app = IsolationCheckApp(repo_hint=repo_hint)
    app.run()
    return 0
