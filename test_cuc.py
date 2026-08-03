import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cuc
import cuc_privileged


FIXTURE_DIR = Path(__file__).parent / "tests" / "fixtures"


class ConfigTests(unittest.TestCase):
    def test_missing_config_uses_defaults_without_creating_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertEqual(cuc.Config.load(path).aur_helper, "auto")
            self.assertFalse(path.exists())

    def test_invalid_config_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(cuc.ConfigError):
                cuc.Config.load(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_invalid_utf8_config_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_bytes(b"\xff\xfe")
            with self.assertRaises(cuc.ConfigError):
                cuc.Config.load(path)

    def test_save_does_not_overwrite_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(cuc.ConfigError):
                cuc.Config(voice_enabled=False).save(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_save_can_update_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            cuc.Config(voice_enabled=False).save(path)
            config = cuc.Config.load(path)
            config.voice_enabled = True
            config.save(path)
            self.assertTrue(cuc.Config.load(path).voice_enabled)

    def test_config_validates_boolean_timeout_and_aur_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"voice_enabled": "false", "command_timeout": 0, "aur_helper": "invalid"}),
                encoding="utf-8",
            )
            with self.assertRaises(cuc.ConfigError):
                cuc.Config.load(path)


class StatusTests(unittest.TestCase):
    def test_unavailable_optional_source_does_not_fail_check(self) -> None:
        state = cuc.AppState(
            {
                "pacman": cuc.UpdateResult("pacman", "Pacman", ""),
                "snap": cuc.UpdateResult("snap", "Snap", "", available_on_system=False, error="not installed"),
            }
        )
        self.assertTrue(state.is_healthy())
        self.assertEqual(cuc.check_exit_code(state), 0)

    def test_checker_error_changes_exit_code_and_speech(self) -> None:
        state = cuc.AppState(
            {"pacman": cuc.UpdateResult("pacman", "Pacman", "", error="network unavailable")}
        )
        self.assertEqual(cuc.check_exit_code(state), cuc.CHECK_FAILURE_EXIT)
        self.assertIn("incomplete", cuc.compose_summary_speech(state, cuc.Config()).lower())

    def test_voice_skips_unwritable_preview_directory(self) -> None:
        config = cuc.Config(voice_enabled=True, preview_dir="/read-only-preview")
        with patch.object(cuc.Path, "is_file", return_value=True), \
             patch("cuc.command_exists", return_value=True), \
             patch.object(cuc.Path, "mkdir", side_effect=PermissionError("read-only")):
            cuc._speak_worker("status", config)


class ProviderTests(unittest.TestCase):
    def test_update_authentication_requirement_matches_command(self) -> None:
        self.assertTrue(cuc.update_command_needs_authentication(["sudo", "pacman", "-Syu"]))
        self.assertTrue(cuc.update_command_needs_authentication(["paru", "-Sua"]))
        self.assertFalse(cuc.update_command_needs_authentication(["flatpak", "update", "-y"]))
        self.assertFalse(cuc.update_command_needs_authentication([]))

    def test_configured_aur_helper_is_preferred(self) -> None:
        config = cuc.Config(aur_helper="paru")
        with patch("cuc.command_exists", side_effect=lambda command: command == "paru"):
            self.assertEqual(cuc.find_aur_helper(config), "paru")

    def test_pacman_check_uses_isolated_database(self) -> None:
        captured_environment = {}

        def fake_run_capture(command, **kwargs):
            captured_environment.update(kwargs["env"])
            return __import__("subprocess").CompletedProcess(command, 2, "", "")

        with patch("cuc.command_exists", return_value=True), patch("cuc.run_capture", side_effect=fake_run_capture):
            result = cuc.check_pacman_updates(cuc.Config())

        self.assertFalse(result.available)
        self.assertIn("CHECKUPDATES_DB", captured_environment)
        self.assertFalse(Path(captured_environment["CHECKUPDATES_DB"]).exists())

    def test_snap_parser_keeps_packages_whose_names_start_with_snap(self) -> None:
        output = (FIXTURE_DIR / "snap_updates.txt").read_text(encoding="utf-8")
        items = cuc.parse_snap_updates(output)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("snap-store"))

    def test_run_interactive_reports_command_launch_failure(self) -> None:
        with patch("cuc.subprocess.call", side_effect=FileNotFoundError("missing command")):
            self.assertEqual(cuc.run_interactive(["missing"]), 127)

    def test_provider_output_fixtures(self) -> None:
        pacman = cuc.parse_update_lines((FIXTURE_DIR / "pacman_updates.txt").read_text(encoding="utf-8"))
        aur = cuc.parse_update_lines((FIXTURE_DIR / "aur_updates.txt").read_text(encoding="utf-8"))
        flatpak = cuc.parse_update_lines(
            (FIXTURE_DIR / "flatpak_updates.txt").read_text(encoding="utf-8"),
            preserve_spacing=True,
        )
        self.assertEqual(len(pacman), 2)
        self.assertEqual(cuc.aur_package_name(aur[0]), "google-chrome")
        self.assertEqual(len(flatpak), 2)

    def test_aur_security_blocks_confirmed_and_orphaned_packages(self) -> None:
        advisory = {
            "incident_active": True,
            "affected_packages": [{"name": "gnome-randr-rust"}],
        }
        metadata = {
            "gnome-randr-rust": {"Maintainer": "restored-maintainer"},
            "orphan-example": {"Maintainer": None},
        }
        report = cuc.evaluate_aur_security(
            ["gnome-randr-rust", "orphan-example"],
            metadata,
            advisory,
        )
        self.assertFalse(report.install_allowed())
        self.assertEqual(report.status(), "blocked")
        self.assertEqual(report.compromised, ["gnome-randr-rust"])
        self.assertEqual(report.orphaned, ["orphan-example"])

    def test_aur_security_fails_closed_when_metadata_is_unavailable(self) -> None:
        report = cuc.evaluate_aur_security(
            ["example"],
            {},
            {"incident_active": True, "affected_packages": []},
            error="network unavailable",
        )
        self.assertFalse(report.install_allowed())
        self.assertIn("could not be verified", " ".join(report.warnings()))

    def test_update_command_builder_is_frontend_independent(self) -> None:
        config = cuc.Config(aur_helper="paru")
        with patch("cuc.command_exists", side_effect=lambda command: command == "paru"):
            commands = cuc.build_update_commands(
                ["pacman", "aur", "flatpak", "snap"],
                config,
                noninteractive=True,
            )
        self.assertEqual(commands[0], ["sudo", "pacman", "-Syu", "--noconfirm"])
        self.assertEqual(commands[1][0:3], ["paru", "-Sua", "--noconfirm"])
        self.assertEqual(commands[2], ["flatpak", "update", "-y"])
        self.assertEqual(commands[3], ["sudo", "snap", "refresh"])


class UpdateSessionTests(unittest.TestCase):
    def test_review_install_cancel_lifecycle(self) -> None:
        session = cuc.UpdateSession()
        self.assertTrue(session.begin_review(["aur"]))
        session.begin_authentication([["paru", "-Sua"]])
        session.begin_install()
        self.assertTrue(session.request_cancel())
        session.finish("cancelled")
        self.assertEqual(session.phase, cuc.SessionPhase.COMPLETE)
        self.assertEqual(session.message, "cancelled")

    def test_parallel_review_is_rejected(self) -> None:
        session = cuc.UpdateSession()
        self.assertTrue(session.begin_review(["pacman"]))
        self.assertFalse(session.begin_review(["flatpak"]))


class PrivilegedHelperTests(unittest.TestCase):
    def test_helper_uses_only_allowlisted_absolute_commands(self) -> None:
        for command in cuc_privileged.COMMANDS.values():
            self.assertTrue(command[0].startswith("/usr/bin/"))

    def test_helper_refuses_to_run_without_root_authorization(self) -> None:
        with patch("cuc_privileged.parse_args", return_value=type("Args", (), {"source": "pacman"})()), \
             patch("cuc_privileged.os.geteuid", return_value=1000), \
             patch("cuc_privileged.subprocess.call") as call:
            self.assertEqual(cuc_privileged.main(), 77)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
