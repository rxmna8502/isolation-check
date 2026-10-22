"""Screens for isolation-check TUI."""
from __future__ import annotations

from isolation_check.tui.screens.collisions import CollisionsScreen
from isolation_check.tui.screens.create_env import CreateEnvScreen
from isolation_check.tui.screens.get_started import GetStartedScreen
from isolation_check.tui.screens.help_screen import HelpScreen
from isolation_check.tui.screens.home import HomeScreen
from isolation_check.tui.screens.manage_envs import ManageEnvsScreen
from isolation_check.tui.screens.modals import ConfirmModal, DetailsModal, OverwriteConfigModal
from isolation_check.tui.screens.prereqs import PrereqsScreen
from isolation_check.tui.screens.updates import UpdatesScreen

__all__ = [
    "HomeScreen",
    "GetStartedScreen",
    "CreateEnvScreen",
    "ManageEnvsScreen",
    "CollisionsScreen",
    "PrereqsScreen",
    "UpdatesScreen",
    "HelpScreen",
    "ConfirmModal",
    "DetailsModal",
    "OverwriteConfigModal",
]
