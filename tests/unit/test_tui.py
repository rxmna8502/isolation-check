"""Unit tests for the isolation-check Terminal User Interface (TUI)."""
from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from isolation_check import cli
from isolation_check.create.generator import GeneratedEnvironmentPlan
from isolation_check.create.manager import CreateResult, DryRunResult
from isolation_check.create.spec import CreateSpec
from isolation_check.runtime.lifecycle import EnvironmentStatusResult, LifecycleActionResult
from isolation_check.tui.adapters import (
    PrerequisiteResult,
    ServiceSummary,
    UpdateCheckResult,
    check_all_prerequisites,
    dry_run_create,
    fetch_pypi_update,
    inspect_compose_definition,
    render_config_toml,
    run_collision_check,
    save_config_toml,
    validate_selected_service_pair,
)
from isolation_check.tui.app import IsolationCheckApp, run_tui
from isolation_check.tui.screens.collisions import CollisionsScreen
from isolation_check.tui.screens.create_env import CreateEnvScreen
from isolation_check.tui.screens.get_started import GetStartedScreen
from isolation_check.tui.screens.help_screen import HelpScreen
from isolation_check.tui.screens.home import HomeScreen
from isolation_check.tui.screens.manage_envs import ManageEnvsScreen
from isolation_check.tui.screens.modals import ConfirmModal, DetailsModal, OverwriteConfigModal
from isolation_check.tui.screens.prereqs import PrereqsScreen
from isolation_check.tui.screens.updates import UpdatesScreen
from textual.widgets import Select


class TestCliEntrypoint(unittest.TestCase):
    """Tests for CLI vs TUI entry point contracts."""

    def test_interactive_no_args_launches_tui(self) -> None:
        with patch("isolation_check.cli.is_interactive", return_value=True):
            with patch("isolation_check.tui.run_tui", return_value=0) as mock_run_tui:
                code = cli.main([])
                self.assertEqual(code, 0)
                mock_run_tui.assert_called_once()

    def test_non_interactive_no_args_prints_help_and_exits(self) -> None:
        stdout_capture = io.StringIO()
        with patch("isolation_check.cli.is_interactive", return_value=False):
            with patch("sys.stdout", stdout_capture):
                with patch("isolation_check.tui.run_tui") as mock_run_tui:
                    code = cli.main([])
                    self.assertEqual(code, 0)
                    mock_run_tui.assert_not_called()
                    out = stdout_capture.getvalue()
                    self.assertIn("usage: isolation-check", out)
                    self.assertIn("Inspect and prepare isolated local development environments.", out)

    def test_cli_help_flag_prints_help(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with patch("isolation_check.tui.run_tui") as mock_run_tui:
                cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        mock_run_tui.assert_not_called()

    def test_cli_check_without_args_exits_code_2(self) -> None:
        stderr_capture = io.StringIO()
        with patch("sys.stderr", stderr_capture):
            code = cli.main(["check"])
            self.assertEqual(code, 2)
            self.assertIn("Two environment paths ('left' and 'right') are required.", stderr_capture.getvalue())


class TestTuiAppHeadless(unittest.IsolatedAsyncioTestCase):
    """Headless interactive tests for Textual TUI using app.run_test()."""

    async def test_app_startup_no_network_and_home_screen(self) -> None:
        with patch("urllib.request.urlopen") as mock_urlopen:
            app = IsolationCheckApp()
            async with app.run_test() as pilot:
                self.assertIsInstance(app.screen, HomeScreen)
                # Network was not touched on startup
                mock_urlopen.assert_not_called()
                # Verify header title contains version
                header = app.screen.query_one("#header-title")
                self.assertIn("isolation-check v", str(header.render()))

    async def test_navigation_shortcuts_and_back(self) -> None:
        app = IsolationCheckApp()
        async with app.run_test() as pilot:
            # 1 -> Get Started
            await pilot.press("1")
            self.assertIsInstance(app.screen, GetStartedScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, HomeScreen)

            # 2 -> Create Environment
            await pilot.press("2")
            self.assertIsInstance(app.screen, CreateEnvScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, HomeScreen)

            # 3 -> Manage Environments
            await pilot.press("3")
            self.assertIsInstance(app.screen, ManageEnvsScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, HomeScreen)

            # 4 -> Collisions
            await pilot.press("4")
            self.assertIsInstance(app.screen, CollisionsScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, HomeScreen)

            # 5 -> Prerequisites
            await pilot.press("5")
            self.assertIsInstance(app.screen, PrereqsScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, HomeScreen)

            # 6 -> Updates
            await pilot.press("6")
            self.assertIsInstance(app.screen, UpdatesScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, HomeScreen)

            # 7 -> Help
            await pilot.press("7")
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, HomeScreen)

    async def test_narrow_terminal_layout(self) -> None:
        app = IsolationCheckApp()
        # Test narrow 40x20 terminal
        async with app.run_test(size=(40, 20)) as pilot:
            self.assertIsInstance(app.screen, HomeScreen)
            await pilot.press("1")
            # Should push GetStartedScreen without exception
            self.assertIsInstance(app.screen, GetStartedScreen)

    async def test_create_env_dry_run_and_confirm(self) -> None:
        mock_plan = GeneratedEnvironmentPlan(
            project_name="env_feature_x",
            dedicated_volume="env_feature_x_pgdata",
            db_user="postgres",
            db_password="secretpassword",
            db_name="app_dev",
            database_url="postgresql://postgres:secretpassword@db:5432/app_dev",
            redacted_database_url="postgresql://postgres:[REDACTED]@db:5432/app_dev",
            app_port=10042,
        )
        mock_dry_res = DryRunResult(
            valid=True,
            plan=mock_plan,
            base_commit="abc1234567",
        )

        app = IsolationCheckApp()
        async with app.run_test() as pilot:
            await pilot.press("2")
            screen = app.screen
            self.assertIsInstance(screen, CreateEnvScreen)

            # Prefill name, app service, and db service
            screen.query_one("#field-name").value = "feature-x"
            screen.query_one("#field-app-service").value = "web"
            screen.query_one("#field-db-service").value = "db"

            with patch("isolation_check.tui.screens.create_env.dry_run_create", return_value=mock_dry_res):
                # Trigger dry run
                await pilot.press("d")
                for _ in range(20):
                    if screen._dry_run_passed:
                        break
                    await pilot.pause(0.05)

                # Verify plan is displayed and DB password is redacted
                plan_details = screen.query_one("#plan-details")
                self.assertIn("env_feature_x_pgdata", str(plan_details.render()))
                self.assertIn("[REDACTED]", str(plan_details.render()))

                # Create button is now enabled
                btn_create = screen.query_one("#btn-create")
                self.assertFalse(btn_create.disabled)

    async def test_create_env_cancellation_makes_no_changes(self) -> None:
        mock_plan = GeneratedEnvironmentPlan(
            project_name="env_feature_y",
            dedicated_volume="env_feature_y_pgdata",
            db_user="postgres",
            db_password="secretpassword",
            db_name="app_dev",
            database_url="postgresql://postgres:secretpassword@db:5432/app_dev",
            redacted_database_url="postgresql://postgres:[REDACTED]@db:5432/app_dev",
            app_port=10042,
        )
        mock_dry_res = DryRunResult(valid=True, plan=mock_plan)

        app = IsolationCheckApp()
        async with app.run_test() as pilot:
            await pilot.press("2")
            screen = app.screen
            self.assertIsInstance(screen, CreateEnvScreen)

            screen.query_one("#field-name").value = "feature-y"
            screen.query_one("#field-app-service").value = "web"
            screen.query_one("#field-db-service").value = "db"
            with patch("isolation_check.tui.screens.create_env.dry_run_create", return_value=mock_dry_res):
                await pilot.press("d")
                for _ in range(20):
                    if screen._dry_run_passed:
                        break
                    await pilot.pause(0.05)

            with patch("isolation_check.tui.screens.create_env.do_create") as mock_do_create:
                # Press create to show confirm modal
                await pilot.press("c")
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, ConfirmModal)

                # Press cancel button on modal
                await pilot.click("#btn-cancel")
                await pilot.pause(0.1)

                # Verify do_create was never called
                mock_do_create.assert_not_called()
                self.assertIsInstance(app.screen, CreateEnvScreen)

    async def test_manage_envs_list_and_stop_confirmation(self) -> None:
        mock_envs = [
            {
                "name": "env-alpha",
                "worktree_path": "/tmp/env-alpha",
                "compose_project_name": "env_alpha",
                "dedicated_volume": "env_alpha_pgdata",
            }
        ]
        mock_status = EnvironmentStatusResult(
            success=True,
            environment="env-alpha",
            status="RUNNING",
            health="HEALTHY",
            url="http://127.0.0.1:10042",
        )

        with patch("isolation_check.tui.screens.manage_envs.list_registered_environments", return_value=mock_envs):
            with patch("isolation_check.tui.screens.manage_envs.inspect_environment_live", return_value=mock_status):
                app = IsolationCheckApp()
                async with app.run_test() as pilot:
                    await pilot.press("3")
                    await pilot.pause()
                    screen = app.screen
                    self.assertIsInstance(screen, ManageEnvsScreen)

                    # Table should have env-alpha
                    table = screen.query_one("#envs-table")
                    self.assertEqual(table.row_count, 1)

                    # Trigger stop
                    await pilot.press("x")
                    await pilot.pause()

                    # ConfirmModal should be shown explaining volume preservation
                    self.assertIsInstance(app.screen, ConfirmModal)
                    modal = app.screen
                    self.assertIn("Named volumes and database data are preserved", str(modal.message))
                    await pilot.click("#btn-cancel")
                    await pilot.pause()

    async def test_prereqs_screen_rendering(self) -> None:
        mock_prereqs = [
            PrerequisiteResult(
                key="python",
                title="Python Runtime",
                status="PASS",
                current="Python 3.11.0",
                required=">= 3.11.0",
                guidance="Python runtime is supported.",
            ),
            PrerequisiteResult(
                key="docker_cli",
                title="Docker CLI",
                status="FAIL",
                current="Not found",
                required="Docker CLI available",
                guidance="Install Docker Desktop or Docker Engine.",
            ),
        ]

        with patch("isolation_check.tui.screens.prereqs.check_all_prerequisites", return_value=mock_prereqs):
            app = IsolationCheckApp()
            async with app.run_test() as pilot:
                await pilot.press("5")
                await pilot.pause()
                screen = app.screen
                self.assertIsInstance(screen, PrereqsScreen)

                table = screen.query_one("#prereqs-table")
                self.assertEqual(table.row_count, 2)

                guidance = screen.query_one("#guidance-text")
                self.assertIn("Python Runtime", str(guidance.render()))

    async def test_updates_screen_offline_tolerance(self) -> None:
        mock_offline_res = UpdateCheckResult(
            current_version="0.3.0a2",
            is_offline=True,
            error="Connection timed out",
            project_url="https://pypi.org/project/isolation-check/",
            upgrade_commands={"pip": 'pip install --upgrade "isolation-check"'},
        )

        with patch("isolation_check.tui.screens.updates.fetch_pypi_update", return_value=mock_offline_res):
            app = IsolationCheckApp()
            async with app.run_test() as pilot:
                await pilot.press("6")
                screen = app.screen
                self.assertIsInstance(screen, UpdatesScreen)

                # Click check button
                await pilot.click("#btn-check-updates")
                await pilot.pause()

                details = screen.query_one("#update-details-text")
                self.assertIn("Offline / Unreachable", str(details.render()))
                self.assertIn("0.3.0a2", str(details.render()))

def _mock_valid_service_summaries() -> list[ServiceSummary]:
    web = ServiceSummary(
        name="web",
        image="",
        build_source="build: .",
        ports=[3000],
        environment={"DATABASE_URL": "postgres://db:5432/app"},
        container_name=None,
        entrypoint=None,
        is_postgres_candidate=False,
        postgres_evidence="",
        postgres_unsupported_reason="Application service",
        is_infrastructure=False,
        infra_type="",
        app_option_label="web (build: . | target port: 3000)",
        db_option_label="web",
        detected_db_env_vars=["DATABASE_URL"],
    )
    db = ServiceSummary(
        name="db",
        image="postgres:15-alpine",
        build_source="",
        ports=[5432],
        environment={},
        container_name=None,
        entrypoint=None,
        is_postgres_candidate=True,
        postgres_evidence="Supported PostgreSQL distribution: postgres:15-alpine",
        postgres_unsupported_reason=None,
        is_infrastructure=True,
        infra_type="PostgreSQL: postgres:15-alpine",
        app_option_label="db [Infrastructure: postgres:15-alpine | target port: 5432]",
        db_option_label="db (postgres:15-alpine)",
        detected_db_env_vars=[],
    )
    return [web, db]


class TestGetStartedWizard(unittest.IsolatedAsyncioTestCase):
    """Regression tests for the sequential Get Started configuration wizard."""

    async def test_get_started_sequential_wizard_steps_and_back_preservation(self) -> None:
        """Requirement: sequential wizard showing 1 step at a time, preserving inputs on Back, keyboard navigation."""
        app = IsolationCheckApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.press("1")
            screen = app.screen
            self.assertIsInstance(screen, GetStartedScreen)
            self.assertEqual(screen.current_step, 1)

            # Step 1: Card 1 is visible, Card 2 is hidden
            self.assertTrue(screen.query_one("#step-1-card").display)
            self.assertFalse(screen.query_one("#step-2-card").display)
            self.assertTrue(screen.repo_verified)
            self.assertFalse(screen.query_one("#btn-step1-next").disabled)

            # Move to Step 2
            screen.query_one("#btn-step1-next").scroll_visible()
            await pilot.pause()
            await pilot.click("#btn-step1-next")
            await pilot.pause()
            self.assertEqual(screen.current_step, 2)
            self.assertFalse(screen.query_one("#step-1-card").display)
            self.assertTrue(screen.query_one("#step-2-card").display)

            # Wait for prerequisites
            for _ in range(20):
                if screen.prereqs_checked:
                    break
                await pilot.pause(0.05)

            # Mock prereqs passing if needed
            screen.prereqs_passed = True
            screen.query_one("#btn-step2-next").disabled = False

            # Move to Step 3
            screen.query_one("#btn-step2-next").scroll_visible()
            await pilot.pause()
            await pilot.click("#btn-step2-next")
            await pilot.pause()
            self.assertEqual(screen.current_step, 3)
            self.assertTrue(screen.query_one("#step-3-card").display)

            # Mock compose services and selection
            screen.compose_inspected = True
            screen.discovered_services = ["web", "db"]
            screen.service_summaries = _mock_valid_service_summaries()
            app_select = screen.query_one("#select-app-service")
            db_select = screen.query_one("#select-db-service")
            app_select.set_options([("web", "web"), ("db", "db")])
            db_select.set_options([("web", "web"), ("db", "db")])
            app_select.value = "web"
            db_select.value = "db"
            await pilot.pause()
            screen.service_mapping_confirmed = True
            screen.query_one("#btn-step3-next").disabled = False

            # Move to Step 4
            screen.query_one("#btn-step3-next").scroll_visible()
            await pilot.pause()
            await pilot.click("#btn-step3-next")
            await pilot.pause()
            self.assertEqual(screen.current_step, 4)
            self.assertTrue(screen.query_one("#step-4-card").display)

            # Set connection settings and port
            screen.query_one("#input-db-env-var").value = "DATABASE_URL"
            screen.query_one("#input-app-port").value = "3000"
            await pilot.pause()
            self.assertFalse(screen.query_one("#btn-step4-next").disabled)

            # Move to Step 5
            screen.query_one("#btn-step4-next").scroll_visible()
            await pilot.pause()
            await pilot.click("#btn-step4-next")
            await pilot.pause()
            self.assertEqual(screen.current_step, 5)
            self.assertTrue(screen.query_one("#step-5-card").display)
            self.assertTrue(screen.validation_passed)
            self.assertFalse(screen.query_one("#btn-write-config").disabled)

            # Test Backward preservation via escape: 5 -> 4 -> 3 -> 2 -> 1
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(screen.current_step, 4)
            self.assertEqual(screen.query_one("#input-app-port").value, "3000")
            self.assertEqual(screen.query_one("#input-db-env-var").value, "DATABASE_URL")

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(screen.current_step, 3)
            self.assertEqual(screen.query_one("#select-app-service").value, "web")
            self.assertEqual(screen.query_one("#select-db-service").value, "db")

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(screen.current_step, 2)

            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(screen.current_step, 1)
            self.assertTrue(screen.repo_verified)

            # Escape on Step 1 exits wizard back to HomeScreen
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, HomeScreen)

    async def test_get_started_failed_repository_validation_and_details(self) -> None:
        """Requirement: summarize errors in plain language, put sanitized technical output behind Show details."""
        app = IsolationCheckApp()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.press("1")
            screen = app.screen
            self.assertIsInstance(screen, GetStartedScreen)

            # Enter invalid repo path
            screen.query_one("#input-repo-path").value = "C:\\definitely\\nonexistent\\repo\\path"
            await pilot.pause()
            await pilot.click("#btn-verify-repo")
            await pilot.pause()

            self.assertFalse(screen.repo_verified)
            self.assertTrue(screen.query_one("#btn-step1-next").disabled)

            # Plain language summary is displayed
            status_text = str(screen.query_one("#lbl-repo-status").render())
            self.assertIn("not a valid Git repository root", status_text)

            # Show details button is visible
            btn_details = screen.query_one("#btn-repo-details")
            self.assertTrue(btn_details.display)

            # Click Show details -> box-repo-details becomes visible
            await pilot.click("#btn-repo-details")
            await pilot.pause()
            self.assertTrue(screen.query_one("#box-repo-details").display)
            self.assertEqual(btn_details.label, "Hide details")

            # Click Hide details -> box-repo-details becomes hidden
            await pilot.click("#btn-repo-details")
            await pilot.pause()
            self.assertFalse(screen.query_one("#box-repo-details").display)
            self.assertEqual(btn_details.label, "Show details")

    async def test_get_started_invalidation_on_changed_inputs(self) -> None:
        """Requirement: Invalidate dependent validation when earlier inputs change."""
        app = IsolationCheckApp()
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.press("1")
            screen = app.screen
            self.assertIsInstance(screen, GetStartedScreen)

            # Verified repository enables Next
            self.assertTrue(screen.repo_verified)
            self.assertFalse(screen.query_one("#btn-step1-next").disabled)

            # Modify repository path input
            screen.query_one("#input-repo-path").value = "/modified/repo/path"
            await pilot.pause()

            # Next is now immediately disabled and repo_verified is invalidated
            self.assertFalse(screen.repo_verified)
            self.assertTrue(screen.query_one("#btn-step1-next").disabled)
            status_text = str(screen.query_one("#lbl-repo-status").render())
            self.assertIn("changed", status_text.lower())

    async def test_get_started_prerequisites_blocking_failure_blocks_next(self) -> None:
        """Requirement: Clearly distinguish warnings from blocking failures; block progression on failure."""
        mock_failures = [
            PrerequisiteResult(
                key="git",
                title="Git CLI",
                status="FAIL",
                current="Missing",
                required="Git 2.20+",
                guidance="Install Git CLI from https://git-scm.com/",
            ),
            PrerequisiteResult(
                key="platform",
                title="Host OS Platform",
                status="WARN",
                current="Windows",
                required="Linux host recommended",
                guidance="Container runtime targets Linux hosts.",
            ),
        ]

        with patch("isolation_check.tui.screens.get_started.check_all_prerequisites", return_value=mock_failures):
            app = IsolationCheckApp()
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.press("1")
                screen = app.screen
                self.assertIsInstance(screen, GetStartedScreen)

                # Move to Step 2
                screen.query_one("#btn-step1-next").scroll_visible()
                await pilot.pause()
                await pilot.click("#btn-step1-next")
                await pilot.pause()
                self.assertEqual(screen.current_step, 2)

                # Wait for check
                for _ in range(20):
                    if screen.prereqs_checked:
                        break
                    await pilot.pause(0.05)

                # Blocking failure prevents Next
                self.assertFalse(screen.prereqs_passed)
                self.assertTrue(screen.query_one("#btn-step2-next").disabled)
                status_text = str(screen.query_one("#lbl-prereqs-status").render())
                self.assertIn("Progression Blocked", status_text)

    async def test_get_started_save_new_configuration_real_helper(self) -> None:
        """Requirement: Save a new configuration successfully using REAL save helper in a temporary repository."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_repo = Path(tmp)
            app = IsolationCheckApp()
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.press("1")
                screen = app.screen
                self.assertIsInstance(screen, GetStartedScreen)

                screen.repo_path = temp_repo
                screen.repo_verified = True
                screen.prereqs_passed = True
                screen.compose_inspected = True
                screen.discovered_services = ["web", "db"]
                screen.service_summaries = _mock_valid_service_summaries()
                screen.query_one("#select-app-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-db-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-app-service").value = "web"
                screen.query_one("#select-db-service").value = "db"
                screen.query_one("#input-app-port").value = "3000"
                await pilot.pause()
                screen.service_mapping_confirmed = True

                screen._set_step(5)
                await pilot.pause()

                # In a fresh repo, no existing config is detected
                self.assertFalse((temp_repo / "isolation-check.toml").exists())
                self.assertTrue(screen.validation_passed)

                # Click write configuration -> writes directly without modal
                await pilot.click("#btn-write-config")
                await pilot.pause(0.1)

                self.assertTrue(screen.written_successfully)
                self.assertTrue((temp_repo / "isolation-check.toml").is_file())
                written = (temp_repo / "isolation-check.toml").read_text(encoding="utf-8")
                self.assertIn('app_service = "web"', written)
                self.assertIn('db_service = "db"', written)
                self.assertIn("app_port = 3000", written)

                status_text = str(screen.query_one("#lbl-review-status").render())
                self.assertIn("Configuration successfully written!", status_text)

    async def test_get_started_confirm_overwrite_real_helper(self) -> None:
        """Requirement: Confirm an overwrite and verify saved contents using REAL save helper."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_repo = Path(tmp)
            initial_cfg = '# original configuration\n[create]\napp_service = "old_web"\n'
            (temp_repo / "isolation-check.toml").write_text(initial_cfg, encoding="utf-8")

            app = IsolationCheckApp()
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.press("1")
                screen = app.screen
                self.assertIsInstance(screen, GetStartedScreen)

                screen.repo_path = temp_repo
                screen.repo_verified = True
                screen.prereqs_passed = True
                screen.compose_inspected = True
                screen.discovered_services = ["web", "db"]
                screen.service_summaries = _mock_valid_service_summaries()
                screen.query_one("#select-app-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-db-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-app-service").value = "web"
                screen.query_one("#select-db-service").value = "db"
                screen.query_one("#input-app-port").value = "3000"
                await pilot.pause()
                screen.service_mapping_confirmed = True

                screen._set_step(5)
                await pilot.pause()

                # Verify overwrite diff was computed against existing file on disk
                notice_text = str(screen.query_one("#lbl-review-notice").render())
                self.assertIn("Existing 'isolation-check.toml' detected", notice_text)

                # Click Write config -> opens OverwriteConfigModal
                await pilot.click("#btn-write-config")
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, OverwriteConfigModal)

                # Confirm overwrite -> triggers _execute_write() with REAL save_config_toml
                await pilot.click("#btn-overwrite")
                await pilot.pause(0.1)

                # Must not crash! Screen returns to GetStartedScreen with success state
                self.assertIsInstance(app.screen, GetStartedScreen)
                self.assertTrue(screen.written_successfully)

                # Verify new contents were written to disk
                saved_cfg = (temp_repo / "isolation-check.toml").read_text(encoding="utf-8")
                self.assertIn('app_service = "web"', saved_cfg)
                self.assertNotIn('app_service = "old_web"', saved_cfg)

                status_text = str(screen.query_one("#lbl-review-status").render())
                self.assertIn("Configuration successfully written!", status_text)

    async def test_get_started_cancel_overwrite_real_helper(self) -> None:
        """Requirement: Cancel an overwrite and verify the original file is unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_repo = Path(tmp)
            initial_cfg = '# original configuration\n[create]\napp_service = "old_web"\n'
            (temp_repo / "isolation-check.toml").write_text(initial_cfg, encoding="utf-8")

            app = IsolationCheckApp()
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.press("1")
                screen = app.screen
                self.assertIsInstance(screen, GetStartedScreen)

                screen.repo_path = temp_repo
                screen.repo_verified = True
                screen.prereqs_passed = True
                screen.compose_inspected = True
                screen.discovered_services = ["web", "db"]
                screen.service_summaries = _mock_valid_service_summaries()
                screen.query_one("#select-app-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-db-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-app-service").value = "web"
                screen.query_one("#select-db-service").value = "db"
                screen.query_one("#input-app-port").value = "3000"
                await pilot.pause()
                screen.service_mapping_confirmed = True

                screen._set_step(5)
                await pilot.pause()

                # Click Write config -> opens OverwriteConfigModal
                await pilot.click("#btn-write-config")
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, OverwriteConfigModal)

                # Cancel modal -> returns to Step 5 without writing
                await pilot.click("#btn-cancel")
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, GetStartedScreen)
                self.assertFalse(screen.written_successfully)

                # Verify original file is completely unchanged
                self.assertEqual((temp_repo / "isolation-check.toml").read_text(encoding="utf-8"), initial_cfg)

    async def test_get_started_simulate_write_failure_inline_error_no_crash(self) -> None:
        """Requirement: Simulate a write failure and verify an inline error without a crash."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_repo = Path(tmp)
            initial_cfg = '# original configuration\n[create]\napp_service = "old_web"\n'
            (temp_repo / "isolation-check.toml").write_text(initial_cfg, encoding="utf-8")

            app = IsolationCheckApp()
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.press("1")
                screen = app.screen
                self.assertIsInstance(screen, GetStartedScreen)

                screen.repo_path = temp_repo
                screen.repo_verified = True
                screen.prereqs_passed = True
                screen.compose_inspected = True
                screen.discovered_services = ["web", "db"]
                screen.service_summaries = _mock_valid_service_summaries()
                screen.query_one("#select-app-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-db-service").set_options([("web", "web"), ("db", "db")])
                screen.query_one("#select-app-service").value = "web"
                screen.query_one("#select-db-service").value = "db"
                screen.query_one("#input-app-port").value = "3000"
                await pilot.pause()
                screen.service_mapping_confirmed = True

                screen._set_step(5)
                await pilot.pause()

                # Simulate a filesystem write error via atomic_write_text
                with patch("isolation_check.tui.adapters.atomic_write_text", side_effect=PermissionError("Access denied simulated")):
                    # Click Write config -> opens OverwriteConfigModal
                    await pilot.click("#btn-write-config")
                    await pilot.pause(0.1)
                    self.assertIsInstance(app.screen, OverwriteConfigModal)

                    # Confirm overwrite
                    await pilot.click("#btn-overwrite")
                    await pilot.pause(0.1)

                    # Must return to GetStartedScreen without crashing
                    self.assertIsInstance(app.screen, GetStartedScreen)
                    self.assertFalse(screen.written_successfully)

                    # Inline error displayed on screen
                    status_text = str(screen.query_one("#lbl-review-status").render())
                    self.assertIn("Failed to save configuration:", status_text)
                    self.assertIn("Permission denied writing 'isolation-check.toml'", status_text)

                    # Original file must be preserved
                    self.assertEqual((temp_repo / "isolation-check.toml").read_text(encoding="utf-8"), initial_cfg)



class TestTuiAdapters(unittest.TestCase):
    """Unit tests for TUI data adapters."""

    def test_render_config_toml(self) -> None:
        toml = render_config_toml(
            app_service="web",
            db_service="db",
            db_env_var="DATABASE_URL",
            app_port=3000,
            compose_files=["docker-compose.yaml"],
            publish_db=False,
        )
        self.assertIn('app_service = "web"', toml)
        self.assertIn('db_service = "db"', toml)
        self.assertIn('db_env_var = "DATABASE_URL"', toml)
        self.assertIn("app_port = 3000", toml)
        self.assertIn("publish_db = false", toml)

    def test_collision_check_adapter(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        fixture_a = repo_root / "tests" / "fixtures" / "compose" / "1_shared_custom_volume" / "env_a"
        fixture_b = repo_root / "tests" / "fixtures" / "compose" / "1_shared_custom_volume" / "env_b"

        report, findings, error = run_collision_check(str(fixture_a), str(fixture_b))
        self.assertIsNone(error)
        self.assertIsNotNone(report)
        # VOL001 should be present
        rule_ids = [f.rule_id for f in findings]
        self.assertIn("VOL001", rule_ids)
        self.assertIn("COVERAGE", rule_ids)

    def test_prerequisites_check_bounded(self) -> None:
        results = check_all_prerequisites()
        keys = [r.key for r in results]
        self.assertIn("python", keys)
        self.assertIn("git", keys)
        self.assertIn("docker_cli", keys)
        self.assertIn("docker_daemon", keys)
        self.assertIn("compose_version", keys)
        self.assertIn("compose_capabilities", keys)
        self.assertIn("platform", keys)


    def test_dry_run_does_not_reserve_port_but_describes_host_port(self) -> None:
        """Requirement 7: dry-run does not allocate or reserve a host port; the report describes host port in preview."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        ports_dir = repo_root / ".isolation-check" / "ports"
        ports_before = set(ports_dir.glob("*.json")) if ports_dir.is_dir() else set()

        spec = CreateSpec(
            name="test-env-dry",
            repo=repo_root,
            base_ref="HEAD",
            branch="test-branch-dry",
            worktree_path=repo_root / "test-wt-dry",
            compose_files=["docker-compose.yaml"],
            app_service="web",
            db_service="db",
            db_env_var="DATABASE_URL",
            app_port=3000,
            publish_db=False,
            dry_run=True,
            format="text",
        )

        mock_plan = GeneratedEnvironmentPlan(
            project_name="test-env-dry",
            dedicated_volume="test-env-dry_pgdata",
            db_user="postgres",
            db_password="secretpassword",
            db_name="postgres",
            database_url="postgresql://postgres:secretpassword@db:5432/postgres",
            redacted_database_url="postgresql://postgres:[REDACTED]@db:5432/postgres",
            app_port=3000,
            override_compose_rel_path=".isolation-check/compose.override.yaml",
            env_rel_path=".isolation-check/isolation.env",
            manifest_rel_path=".isolation-check/manifest.json",
        )

        with patch("isolation_check.create.manager.validate_spec_and_config") as mock_val:
            mock_val.return_value = MagicMock(raw_compose={}, app_port=3000)
            with patch("isolation_check.create.manager.resolve_commit_hash", return_value="12345678"):
                with patch("isolation_check.create.manager.plan_generated_environment", return_value=mock_plan):
                    with patch("isolation_check.runtime.port_allocator.reserve_loopback_port") as mock_reserve:
                        # 1. Run dry-run
                        dry_res = dry_run_create(spec)
                        self.assertTrue(dry_res.valid)
                        mock_reserve.assert_not_called()

                        # Ensure no files were created in ports_dir
                        ports_after = set(ports_dir.glob("*.json")) if ports_dir.is_dir() else set()
                        self.assertEqual(ports_before, ports_after)

                        # 2. Check JSON representation describes host port and binding policy
                        d = dry_res.to_dict()
                        self.assertEqual(d["proposed"]["app_port"], 3000)
                        self.assertEqual(d["proposed"]["binding_policy"], "loopback-only")
                        self.assertEqual(d["proposed"]["bind_address"], "127.0.0.1")

                        # 3. Check CLI output describes host port
                        stdout_cap = io.StringIO()
                        with patch("sys.stdout", stdout_cap):
                            from isolation_check.create.manager import execute_dry_run
                            execute_dry_run(spec)
                        cli_out = stdout_cap.getvalue()
                        self.assertIn("App host binding:  127.0.0.1 loopback-only (assigned dynamically at runtime)", cli_out)

    def test_save_config_toml_contracts(self) -> None:
        """Requirement: Verify the helper's return contract on every handled path."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)

            # 1. Successful new write -> (True, None)
            new_content = '[create]\napp_service = "web"\n'
            ok, err = save_config_toml(repo_path, new_content)
            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual((repo_path / "isolation-check.toml").read_text(encoding="utf-8"), new_content)

            # 2. Successful overwrite -> (True, None)
            updated_content = '[create]\napp_service = "updated_web"\n'
            ok, err = save_config_toml(repo_path, updated_content)
            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual((repo_path / "isolation-check.toml").read_text(encoding="utf-8"), updated_content)

            # 3. Empty or whitespace content -> (False, "Configuration content cannot be empty.")
            ok, err = save_config_toml(repo_path, "   \n  ")
            self.assertFalse(ok)
            self.assertIn("cannot be empty", str(err))

            # 4. Non-existent directory -> (False, "Target directory does not exist...")
            non_existent = repo_path / "does_not_exist_sub"
            ok, err = save_config_toml(non_existent, new_content)
            self.assertFalse(ok)
            self.assertIn("does not exist", str(err))

            # 5. Filesystem / Permission error -> (False, sanitized error message)
            with patch("isolation_check.tui.adapters.atomic_write_text", side_effect=PermissionError("Access denied")):
                ok, err = save_config_toml(repo_path, '[create]\napp_service = "fail"\n')
                self.assertFalse(ok)
                self.assertIn("Permission denied", str(err))
                # Verify original file preserved
                self.assertEqual((repo_path / "isolation-check.toml").read_text(encoding="utf-8"), updated_content)


class TestServiceSelectionUX(unittest.IsolatedAsyncioTestCase):
    """Tests for Get Started wizard service selection UX, classification, and validation."""

    def _create_mixed_stack_compose(self, repo_dir: Path) -> Path:
        compose_content = """
services:
  web:
    build: .
    ports:
      - "3000:3000"
    environment:
      - DATABASE_URL=postgres://postgres:password@postgres:5432/app
  postgres:
    image: postgres:15-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_PASSWORD: password
  kafka:
    image: confluentinc/cp-kafka:7.3.0
    ports:
      - "9092:9092"
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  mailpit:
    image: axllent/mailpit:latest
    ports:
      - "1025:1025"
      - "8025:8025"
  custom-db:
    image: myorg/custom-storage:latest
    ports:
      - "5433:5433"
  fixed-pg:
    image: postgres:15-alpine
    container_name: fixed_postgres_container
    ports:
      - "5434:5432"
  entrypoint-pg:
    image: postgres:15-alpine
    entrypoint: ["/custom-init.sh"]
    ports:
      - "5435:5432"
  postgis:
    image: postgis/postgis:15-3.3
    ports:
      - "5436:5432"
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    ports:
      - "5437:5432"
  crunchy:
    image: crunchydata/crunchy-postgres:ubi8-15.2-0
    ports:
      - "5438:5432"
  custom-pg:
    image: postgres-custom:latest
    ports:
      - "5439:5432"
"""
        compose_file = repo_dir / "docker-compose.yaml"
        compose_file.write_text(compose_content.strip(), encoding="utf-8")
        return compose_file

    def test_mixed_stack_classification_backend_rules(self) -> None:
        """Requirement: Classify services from resolved Compose model using shared backend rules."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            self._create_mixed_stack_compose(repo_path)

            services, ports_map, summaries, err = inspect_compose_definition(repo_path, ["docker-compose.yaml"])
            self.assertIsNone(err)
            self.assertEqual(len(services), 12)

            by_name = {s.name: s for s in summaries}

            # 1. postgres must be the ONLY qualifying candidate
            self.assertTrue(by_name["postgres"].is_postgres_candidate)
            self.assertIn("Supported PostgreSQL distribution", by_name["postgres"].postgres_evidence)
            self.assertIn("postgres:15-alpine", by_name["postgres"].postgres_evidence)

            # 2. Unrelated and unverified variants must NOT be accepted as PostgreSQL
            for unrelated in (
                "web", "kafka", "redis", "mailpit", "custom-db", "fixed-pg",
                "entrypoint-pg", "postgis", "timescaledb", "crunchy", "custom-pg"
            ):
                self.assertFalse(
                    by_name[unrelated].is_postgres_candidate,
                    f"Service '{unrelated}' was erroneously classified as a PostgreSQL candidate.",
                )
                self.assertIsNotNone(
                    by_name[unrelated].postgres_unsupported_reason,
                    f"Service '{unrelated}' lacked an unsupported reason.",
                )

            # 3. Verify specific unsupported reasons and exact repo matching
            self.assertIn("container_name", by_name["fixed-pg"].postgres_unsupported_reason)
            self.assertIn("entrypoint", by_name["entrypoint-pg"].postgres_unsupported_reason)
            self.assertIn("not recognized", by_name["custom-db"].postgres_unsupported_reason.lower())
            self.assertIn("PostGIS variants are unverified", by_name["postgis"].postgres_unsupported_reason)
            self.assertIn("TimescaleDB variants are unverified", by_name["timescaledb"].postgres_unsupported_reason)
            self.assertIn("Crunchy Data variants are unverified", by_name["crunchy"].postgres_unsupported_reason)
            self.assertIn("custom or unofficial PostgreSQL image", by_name["custom-pg"].postgres_unsupported_reason)

            # 4. Infrastructure labeling
            self.assertTrue(by_name["kafka"].is_infrastructure)
            self.assertIn("Message Broker", by_name["kafka"].infra_type)
            self.assertIn("[Infrastructure:", by_name["kafka"].app_option_label)

            self.assertTrue(by_name["redis"].is_infrastructure)
            self.assertIn("Cache", by_name["redis"].infra_type)
            self.assertIn("[Infrastructure:", by_name["redis"].app_option_label)

            self.assertTrue(by_name["mailpit"].is_infrastructure)
            self.assertIn("Mail Utility", by_name["mailpit"].infra_type)
            self.assertIn("[Infrastructure:", by_name["mailpit"].app_option_label)

            self.assertTrue(by_name["postgis"].is_infrastructure)
            self.assertIn("PostGIS", by_name["postgis"].infra_type)

            self.assertTrue(by_name["timescaledb"].is_infrastructure)
            self.assertIn("TimescaleDB", by_name["timescaledb"].infra_type)

            self.assertTrue(by_name["crunchy"].is_infrastructure)
            self.assertIn("Crunchy", by_name["crunchy"].infra_type)

            # 5. Application service source/ports
            self.assertFalse(by_name["web"].is_infrastructure)
            self.assertIn("build:", by_name["web"].app_option_label)
            self.assertIn("target port: 3000", by_name["web"].app_option_label)
            self.assertEqual(by_name["web"].detected_db_env_vars, ["DATABASE_URL"])

    def test_validate_selected_service_pair_rules(self) -> None:
        """Requirement: Validate selected pair through backend rules before Next or Save."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            self._create_mixed_stack_compose(repo_path)
            _, _, summaries, _ = inspect_compose_definition(repo_path, ["docker-compose.yaml"])

            # Valid pair
            valid, msg, env_var = validate_selected_service_pair("web", "postgres", summaries)
            self.assertTrue(valid)
            self.assertIn("Validated Service Mapping", msg)
            self.assertEqual(env_var, "DATABASE_URL")

            # Unrelated / unverified services rejected as PostgreSQL
            for bad_db in (
                "kafka", "redis", "mailpit", "custom-db", "fixed-pg", "entrypoint-pg",
                "postgis", "timescaledb", "crunchy", "custom-pg", "web"
            ):
                valid, msg, _ = validate_selected_service_pair("web", bad_db, summaries)
                self.assertFalse(valid, f"Expected '{bad_db}' to be rejected as db_service.")
                self.assertTrue(len(msg) > 0)

            # Infrastructure rejected as application when it lacks DB connection config
            for bad_app in ("kafka", "redis", "mailpit", "postgis", "timescaledb", "crunchy"):
                valid, msg, _ = validate_selected_service_pair(bad_app, "postgres", summaries)
                self.assertFalse(valid, f"Expected '{bad_app}' to be rejected as app_service.")
                self.assertIn("infrastructure", msg.lower())

            # Same service rejected
            valid, msg, _ = validate_selected_service_pair("postgres", "postgres", summaries)
            self.assertFalse(valid)
            self.assertIn("cannot be the same container", msg)

    async def test_get_started_wizard_mixed_stack_ui(self) -> None:
        """Requirement: Test mixed stack in TUI: dropdown contents, confirmation, revalidation, 80x24 navigation."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            self._create_mixed_stack_compose(repo_path)

            app = IsolationCheckApp()
            # Test responsive layout at standard 80x24 terminal
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("1")
                await pilot.pause()
                screen = app.screen
                self.assertIsInstance(screen, GetStartedScreen)

                screen.repo_path = repo_path
                screen.repo_verified = True
                screen.prereqs_passed = True
                screen.query_one("#input-compose-files").value = "docker-compose.yaml"

                # Advance to Step 3 (triggers _inspect_compose_action)
                screen._set_step(3)

                # Wait for inspection worker
                for _ in range(40):
                    if screen.compose_inspected:
                        break
                    await pilot.pause(0.05)
                self.assertTrue(screen.compose_inspected)

                db_select = screen.query_one("#select-db-service")
                app_select = screen.query_one("#select-app-service")
                next_btn = screen.query_one("#btn-step3-next")
                confirm_btn = screen.query_one("#btn-confirm-services")
                unsupported_box = screen.query_one("#box-unsupported-databases")

                # 1. PostgreSQL selector must contain ONLY supported PostgreSQL candidates
                db_option_values = [
                    opt[1] for opt in db_select._options
                    if opt[1] != Select.BLANK and opt[1] is not Select.BLANK and opt[1] is not Select.NULL
                ]
                self.assertEqual(db_option_values, ["postgres"])
                self.assertNotIn("kafka", db_option_values)
                self.assertNotIn("redis", db_option_values)
                self.assertNotIn("mailpit", db_option_values)
                self.assertNotIn("web", db_option_values)
                self.assertNotIn("custom-db", db_option_values)
                self.assertNotIn("postgis", db_option_values)
                self.assertNotIn("timescaledb", db_option_values)
                self.assertNotIn("crunchy", db_option_values)
                self.assertNotIn("custom-pg", db_option_values)

                # 2. Unsupported databases callout must be visible and explain reasons
                self.assertTrue(unsupported_box.display)
                box_text = str(unsupported_box.render())
                self.assertIn("custom-db", box_text)
                self.assertIn("fixed-pg", box_text)
                self.assertIn("entrypoint-pg", box_text)
                self.assertIn("postgis", box_text)
                self.assertIn("timescaledb", box_text)
                self.assertIn("crunchy", box_text)
                self.assertIn("custom-pg", box_text)

                # 3. Application selector must NOT default to the first alphabetic service and confirm it
                self.assertTrue(app_select.is_blank())
                self.assertFalse(screen.service_mapping_confirmed)
                self.assertTrue(next_btn.disabled)

                # Attempting Next without confirmation must be blocked
                next_btn.scroll_visible(animate=False, immediate=True)
                await pilot.pause()
                await pilot.click("#btn-step3-next")
                await pilot.pause()
                self.assertEqual(screen.current_step, 3)

                # 4. Verify opening dropdown keeps navigation accessible (SelectOverlay bounded)
                db_select.action_show_overlay()
                await pilot.pause()
                # Verify overlay is open
                self.assertTrue(db_select.expanded)
                # Escape closes overlay without breaking navigation
                await pilot.press("escape")
                await pilot.pause()
                self.assertFalse(db_select.expanded)

                # 5. Select valid application and confirm
                app_select.value = "web"
                db_select.value = "postgres"
                await pilot.pause()

                # Confirmation is required
                self.assertFalse(screen.service_mapping_confirmed)
                self.assertTrue(next_btn.disabled)

                confirm_btn.scroll_visible(animate=False, immediate=True)
                await pilot.pause()
                await pilot.click("#btn-confirm-services")
                await pilot.pause()

                self.assertTrue(screen.service_mapping_confirmed)
                self.assertFalse(next_btn.disabled)
                status_text = str(screen.query_one("#lbl-services-status").render())
                self.assertIn("Validated Service Mapping", status_text)

                # 6. Selection change invalidates confirmation and downstream steps
                app_select.value = "redis"
                await pilot.pause()

                self.assertFalse(screen.service_mapping_confirmed)
                self.assertTrue(next_btn.disabled)

                # Attempting to confirm redis as app is rejected
                confirm_btn.scroll_visible(animate=False, immediate=True)
                await pilot.pause()
                await pilot.click("#btn-confirm-services")
                await pilot.pause()
                self.assertFalse(screen.service_mapping_confirmed)
                self.assertTrue(next_btn.disabled)
                status_text = str(screen.query_one("#lbl-services-status").render())
                self.assertIn("infrastructure", status_text.lower())

                # 7. Revalidate previous selection when re-inspecting
                # Set a stale invalid db selection
                db_select.set_options([("redis", "redis"), ("postgres", "postgres")])
                db_select.value = "redis"

                screen._inspect_compose_action()
                for _ in range(30):
                    await pilot.pause(0.05)

                # Re-inspection must reject the stale "redis" selection and reset to BLANK
                self.assertNotEqual(db_select.value, "redis")
                guidance_text = str(screen.query_one("#lbl-db-guidance").render())
                self.assertIn("reset", guidance_text.lower())

                # 8. Navigation reachable at 80x24: buttons exist and can be focused
                confirm_btn.focus()
                await pilot.pause()
                self.assertTrue(confirm_btn.has_focus)
                back_btn = screen.query_one("#btn-step3-back")
                back_btn.focus()
                await pilot.pause()
                self.assertTrue(back_btn.has_focus)
                self.assertTrue(next_btn.disabled)

                # Once confirmed, next_btn is enabled and can be focused
                app_select.value = "web"
                db_select.value = "postgres"
                confirm_btn.scroll_visible(animate=False, immediate=True)
                await pilot.pause()
                await pilot.click("#btn-confirm-services")
                await pilot.pause()
                self.assertFalse(next_btn.disabled)
                next_btn.focus()
                await pilot.pause()
                self.assertTrue(next_btn.has_focus)


if __name__ == "__main__":
    unittest.main()

