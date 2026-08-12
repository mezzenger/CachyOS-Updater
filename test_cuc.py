import hashlib
import base64
import json
import logging
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import cuc_backend as cuc
import cuc_advisory
import cuc_diagnostics
import cuc_privileged
from cuc_controller import ExecutionOutcome, UpdateExecutor


FIXTURE_DIR = Path(__file__).parent / "tests" / "fixtures"


class ConfigTests(unittest.TestCase):
    def test_application_version_uses_semantic_versioning(self) -> None:
        self.assertRegex(cuc.__version__, r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

    def test_all_current_status_keeps_the_grumpy_success_message(self) -> None:
        self.assertEqual(
            cuc.ALL_CURRENT_STATUS,
            "Annoyingly current",
        )

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

    def test_extended_update_preferences_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            expected = cuc.Config(
                installation_timeout=900,
                max_result_age_minutes=5,
                minimum_free_space_mb=2048,
                notifications_enabled=False,
                advisory_feed_url="https://example.com/advisory.json",
                advisory_signature_url="https://example.com/advisory.json.sig",
                advisory_public_key_file="/etc/cuc/advisory-key.pem",
                advisory_refresh_hours=12,
                enabled_sources={
                    "pacman": True,
                    "aur": False,
                    "flatpak": True,
                    "snap": False,
                },
            )
            expected.save(path)

            self.assertEqual(cuc.Config.load(path), expected)

    def test_config_rejects_unknown_or_non_boolean_source_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"enabled_sources": {"pacman": "yes", "unknown": True}}),
                encoding="utf-8",
            )

            with self.assertRaises(cuc.ConfigError):
                cuc.Config.load(path)

    def test_signed_advisory_settings_require_a_complete_https_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"advisory_feed_url": "https://example.com/advisory.json"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(cuc.ConfigError, "require the feed URL"):
                cuc.Config.load(path)

            path.write_text(
                json.dumps(
                    {
                        "advisory_feed_url": "http://example.com/advisory.json",
                        "advisory_signature_url": "https://example.com/advisory.json.sig",
                        "advisory_public_key_file": "/etc/cuc/advisory-key.pem",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(cuc.ConfigError, "valid HTTPS URL"):
                cuc.Config.load(path)

            path.write_text(
                json.dumps(
                    {
                        "advisory_feed_url": "https://example.com/advisory.json",
                        "advisory_signature_url": "https://example.com/advisory.json.sig",
                        "advisory_public_key_file": "relative-key.pem",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(cuc.ConfigError, "absolute path"):
                cuc.Config.load(path)


class WindowStateTests(unittest.TestCase):
    def test_window_state_round_trip_clamps_unsafe_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "window-state.json"
            cuc.save_window_state(cuc.WindowState(320, 99999, True), path)

            self.assertEqual(cuc.load_window_state(path), cuc.WindowState(460, 16384, True))

    def test_window_state_uses_defaults_for_missing_or_invalid_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "window-state.json"
            self.assertEqual(cuc.load_window_state(path), cuc.WindowState())

            path.write_text(
                json.dumps({"width": True, "height": 700, "maximized": False}),
                encoding="utf-8",
            )
            self.assertEqual(cuc.load_window_state(path), cuc.WindowState())

            path.write_text("{invalid json", encoding="utf-8")
            self.assertEqual(cuc.load_window_state(path), cuc.WindowState())


class StatusTests(unittest.TestCase):
    def test_batch_install_targets_exclude_blocked_and_failed_sources(self) -> None:
        state = cuc.AppState(
            {
                "pacman": cuc.UpdateResult(
                    "pacman", "Pacman", "", available=True, count=2
                ),
                "aur": cuc.UpdateResult(
                    "aur", "AUR", "", available=True, count=1, install_allowed=False
                ),
                "flatpak": cuc.UpdateResult(
                    "flatpak", "Flatpak", "", available=True, count=3, error="check failed"
                ),
                "snap": cuc.UpdateResult("snap", "Snap", ""),
            }
        )

        self.assertEqual(state.installable_sources(), ["pacman"])

    def test_stale_sources_only_include_requested_enabled_results(self) -> None:
        now = datetime(2026, 8, 6, 12, 0, 0)
        state = cuc.AppState(
            {
                "pacman": cuc.UpdateResult(
                    "pacman",
                    "Pacman",
                    "",
                    checked_at=(now - timedelta(minutes=20)).isoformat(),
                ),
                "aur": cuc.UpdateResult(
                    "aur",
                    "AUR",
                    "",
                    enabled=False,
                    checked_at=(now - timedelta(days=1)).isoformat(),
                ),
                "flatpak": cuc.UpdateResult(
                    "flatpak",
                    "Flatpak",
                    "",
                    checked_at=now.isoformat(),
                ),
            }
        )

        self.assertEqual(
            state.stale_sources(15, ["pacman", "aur", "flatpak"], now=now),
            ["pacman"],
        )

    def test_unavailable_optional_source_does_not_fail_check(self) -> None:
        state = cuc.AppState(
            {
                "pacman": cuc.UpdateResult("pacman", "Pacman", ""),
                "snap": cuc.UpdateResult("snap", "Snap", "", available_on_system=False, error="not installed"),
            }
        )
        self.assertTrue(state.is_healthy())

    def test_checker_error_changes_health_and_speech(self) -> None:
        state = cuc.AppState(
            {"pacman": cuc.UpdateResult("pacman", "Pacman", "", error="network unavailable")}
        )
        self.assertFalse(state.is_healthy())
        self.assertIn("incomplete", cuc.compose_summary_speech(state, cuc.Config()).lower())

    def test_voice_skips_unwritable_preview_directory(self) -> None:
        config = cuc.Config(voice_enabled=True, preview_dir="/read-only-preview")
        with patch.object(cuc.Path, "is_file", return_value=True), \
             patch("cuc_backend.command_exists", return_value=True), \
             patch.object(cuc.Path, "mkdir", side_effect=PermissionError("read-only")):
            cuc._speak_worker("status", config)


class ProviderTests(unittest.TestCase):
    def test_polkit_readiness_validates_command_files_ownership_and_mode(self) -> None:
        helper = Path("/test/cuc-privileged")
        policy = Path("/test/cuc.policy")
        with patch("cuc_backend.command_exists", return_value=True), \
             patch.object(cuc.Path, "is_file", return_value=True), \
             patch.object(cuc.Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o100755)), \
             patch.object(cuc.Path, "is_symlink", return_value=False), \
             patch("cuc_backend.os.access", return_value=True):
            readiness = cuc.check_polkit_readiness(helper, policy)

        self.assertTrue(readiness.ready)
        self.assertIn("ready", readiness.message.lower())

    def test_polkit_readiness_rejects_non_root_writable_files(self) -> None:
        with patch("cuc_backend.command_exists", return_value=True), \
             patch.object(cuc.Path, "is_file", return_value=True), \
             patch.object(cuc.Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o100777)), \
             patch.object(cuc.Path, "is_symlink", return_value=False):
            readiness = cuc.check_polkit_readiness(Path("/helper"), Path("/policy"))
        self.assertFalse(readiness.ready)
        self.assertIn("writable", readiness.message)

    def test_configured_aur_helper_is_preferred(self) -> None:
        config = cuc.Config(aur_helper="paru")
        with patch("cuc_backend.command_exists", side_effect=lambda command: command == "paru"):
            self.assertEqual(cuc.find_aur_helper(config), "paru")

    def test_pacman_check_uses_isolated_database(self) -> None:
        captured_environment = {}

        def fake_run_capture(command, **kwargs):
            captured_environment.update(kwargs["env"])
            return __import__("subprocess").CompletedProcess(command, 2, "", "")

        with patch("cuc_backend.command_exists", return_value=True), patch("cuc_backend.run_capture", side_effect=fake_run_capture):
            result = cuc.check_pacman_updates(cuc.Config())

        self.assertFalse(result.available)
        self.assertIn("CHECKUPDATES_DB", captured_environment)
        self.assertFalse(Path(captured_environment["CHECKUPDATES_DB"]).exists())

    def test_snap_parser_keeps_packages_whose_names_start_with_snap(self) -> None:
        output = (FIXTURE_DIR / "snap_updates.txt").read_text(encoding="utf-8")
        items = cuc.parse_snap_updates(output)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("snap-store"))

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
        with patch("cuc_backend.command_exists", side_effect=lambda command: command == "paru"):
            commands = cuc.build_update_commands(
                ["pacman", "aur", "flatpak", "snap"],
                config,
                noninteractive=True,
            )
        self.assertEqual(commands[0], ["sudo", "-n", "pacman", "-Syu", "--noconfirm"])
        self.assertEqual(commands[1][0:3], ["paru", "-Sua", "--noconfirm"])
        self.assertEqual(commands[2], ["flatpak", "update", "-y"])
        self.assertEqual(commands[3], ["sudo", "-n", "snap", "refresh"])


class UpdateSessionTests(unittest.TestCase):
    def test_review_install_cancel_lifecycle(self) -> None:
        session = cuc.UpdateSession()
        self.assertTrue(session.begin_review(["aur"]))
        session.begin_install()
        self.assertTrue(session.request_cancel())
        session.finish("cancelled")
        self.assertEqual(session.phase, cuc.SessionPhase.COMPLETE)
        self.assertEqual(session.message, "cancelled")

    def test_parallel_review_is_rejected(self) -> None:
        session = cuc.UpdateSession()
        self.assertTrue(session.begin_review(["pacman"]))
        self.assertFalse(session.begin_review(["flatpak"]))

    def test_install_summary_reports_partial_outcomes(self) -> None:
        summary = cuc.InstallSummary()
        summary.add("pacman", "succeeded", "done", 0)
        summary.add("aur", "failed", "bad package", 1)
        summary.add("flatpak", "skipped", "after failure")

        self.assertFalse(summary.successful())
        self.assertIn("Succeeded: Pacman", summary.message())
        self.assertIn("Failed: AUR", summary.message())
        self.assertIn("Skipped: Flatpak", summary.message())


class UpdateFlowTests(unittest.TestCase):
    def test_provider_registry_exposes_one_contract_for_every_source(self) -> None:
        self.assertEqual(cuc.PROVIDERS.sources(), cuc.UPDATE_SOURCES)
        for provider in cuc.PROVIDERS.values():
            self.assertTrue(callable(provider.check))
            self.assertTrue(callable(provider.build_command))
            self.assertTrue(callable(provider.disabled_result))

    def test_retry_refresh_preserves_successful_source_results(self) -> None:
        previous_flatpak = cuc.UpdateResult(
            "flatpak", "Flatpak", "", available=True, count=2
        )
        prior = cuc.AppState(
            {
                "pacman": cuc.UpdateResult(
                    "pacman", "Pacman", "", error="network unavailable"
                ),
                "flatpak": previous_flatpak,
            }
        )

        def refreshed_pacman(_config):
            return cuc.UpdateResult("pacman", "Pacman", "", available=True, count=1)

        with patch.object(cuc.PROVIDERS["pacman"], "check", side_effect=refreshed_pacman):
            refreshed = cuc.check_all_parallel(
                cuc.Config(),
                show_progress=False,
                sources=["pacman"],
                prior_state=prior,
            )

        self.assertIs(refreshed.results["flatpak"], previous_flatpak)
        self.assertEqual(refreshed.results["pacman"].count, 1)

    def test_disabled_source_is_skipped_by_parallel_check(self) -> None:
        config = cuc.Config(
            enabled_sources={
                "pacman": True,
                "aur": True,
                "flatpak": True,
                "snap": False,
            }
        )
        with patch("cuc_backend.check_snap_updates") as snap_check:
            state = cuc.check_all_parallel(
                config,
                show_progress=False,
                sources=["snap"],
            )

        snap_check.assert_not_called()
        self.assertFalse(state.results["snap"].enabled)

    def test_preflight_detects_stale_network_lock_disk_and_process_conflicts(self) -> None:
        old = (datetime.now() - timedelta(hours=1)).isoformat()
        state = cuc.AppState(
            {
                "pacman": cuc.UpdateResult(
                    "pacman",
                    "Pacman",
                    "",
                    available=True,
                    count=1,
                    error="network connection timed out",
                    checked_at=old,
                )
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "db.lck"
            lock.touch()
            disk_usage = type("DiskUsage", (), {"free": 100 * 1024 * 1024})()
            with patch("cuc_backend.shutil.disk_usage", return_value=disk_usage), \
                 patch(
                     "cuc_backend._running_package_managers",
                     return_value=[cuc.RunningPackageManager("pacman", 4321)],
                 ):
                issues = cuc.check_update_preflight(
                    ["pacman"],
                    state,
                    cuc.Config(minimum_free_space_mb=1024),
                    pacman_lock=lock,
                )

        codes = {issue.code for issue in issues}
        self.assertTrue(
            {
                "network-unavailable",
                "stale-results",
                "pacman-lock",
                "disk-space",
                "package-manager-running",
            }.issubset(codes)
        )
        conflict = next(issue for issue in issues if issue.code == "package-manager-running")
        self.assertIn("pacman (PID 4321)", conflict.message)

    def test_preflight_only_blocks_package_managers_that_conflict_with_targets(self) -> None:
        state = cuc.AppState(
            {
                "pacman": cuc.UpdateResult("pacman", "Pacman", "", available=True, count=1),
                "aur": cuc.UpdateResult("aur", "AUR", "", available=True, count=1),
                "snap": cuc.UpdateResult("snap", "Snap", "", available=True, count=1),
            }
        )
        running_snap = [cuc.RunningPackageManager("snap", 2468)]
        with patch("cuc_backend._running_package_managers", return_value=running_snap):
            system_issues = cuc.check_update_preflight(["pacman", "aur"], state, cuc.Config())
            snap_issues = cuc.check_update_preflight(["snap"], state, cuc.Config())

        self.assertNotIn("package-manager-running", {issue.code for issue in system_issues})
        snap_conflict = next(
            issue for issue in snap_issues if issue.code == "package-manager-running"
        )
        self.assertIn("snap (PID 2468)", snap_conflict.message)

    def test_running_package_manager_scan_ignores_zombies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            active = proc_root / "1111"
            active.mkdir()
            (active / "comm").write_text("paru\n", encoding="utf-8")
            (active / "status").write_text("Name:\tparu\nState:\tS (sleeping)\n", encoding="utf-8")
            zombie = proc_root / "2222"
            zombie.mkdir()
            (zombie / "comm").write_text("snap\n", encoding="utf-8")
            (zombie / "status").write_text("Name:\tsnap\nState:\tZ (zombie)\n", encoding="utf-8")

            self.assertEqual(
                cuc._running_package_managers(proc_root),
                [cuc.RunningPackageManager("paru", 1111)],
            )

    def test_shared_executor_skips_aur_after_pacman_failure_but_continues_independent_sources(self) -> None:
        calls = []

        def run(source, _command):
            calls.append(source)
            if source == "pacman":
                return ExecutionOutcome(source, "failed", "pacman failed", 1)
            return ExecutionOutcome(source, "succeeded", "done", 0)

        outcomes = UpdateExecutor().execute(
            [
                ("pacman", ["pacman"]),
                ("aur", ["paru"]),
                ("flatpak", ["flatpak"]),
                ("snap", ["snap"]),
            ],
            run,
        )

        self.assertEqual(calls, ["pacman", "flatpak", "snap"])
        self.assertEqual([outcome.status for outcome in outcomes], ["failed", "skipped", "succeeded", "succeeded"])

    def test_shared_executor_stops_everything_after_cancellation(self) -> None:
        outcomes = UpdateExecutor().execute(
            [("pacman", ["pacman"]), ("flatpak", ["flatpak"])],
            lambda source, _command: ExecutionOutcome(source, "cancelled", "cancelled", 130),
        )
        self.assertEqual([outcome.status for outcome in outcomes], ["cancelled", "skipped"])

class AdvisoryUpdateTests(unittest.TestCase):
    def valid_advisory(self, updated_at: str = "2026-08-06") -> dict:
        return {
            "schema_version": 1,
            "updated_at": updated_at,
            "expires_at": "2026-09-06",
            "incident_active": True,
            "advisory_url": "https://example.com/advisory",
            "affected_packages": [
                {
                    "name": "example-package",
                    "source": "https://example.com/source",
                }
            ],
        }

    def test_authenticated_advisory_requires_https_and_matching_digest(self) -> None:
        payload = json.dumps(self.valid_advisory()).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()

        class Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return payload

        with patch("cuc_advisory.urllib.request.urlopen", return_value=Response()):
            downloaded, advisory = cuc_advisory.download_authenticated_advisory(
                "https://example.com/advisory.json",
                digest,
                timeout=10,
            )
        self.assertEqual(downloaded, payload)
        self.assertEqual(advisory["updated_at"], "2026-08-06")

        with self.assertRaises(cuc_advisory.AdvisoryUpdateError):
            cuc_advisory.download_authenticated_advisory(
                "http://example.com/advisory.json",
                digest,
                timeout=10,
            )
        with patch("cuc_advisory.urllib.request.urlopen", return_value=Response()), \
             self.assertRaises(cuc_advisory.AdvisoryUpdateError):
            cuc_advisory.download_authenticated_advisory(
                "https://example.com/advisory.json",
                "0" * 64,
                timeout=10,
            )

    def test_advisory_install_is_atomic_verified_and_rejects_rollback(self) -> None:
        current = self.valid_advisory("2026-08-06")
        older = self.valid_advisory("2026-08-05")
        payload = json.dumps(older).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory, \
             patch(
                 "cuc_advisory.download_authenticated_advisory",
                 return_value=(payload, older),
             ):
            destination = Path(directory) / "advisory.json"
            with self.assertRaises(cuc_advisory.AdvisoryUpdateError):
                cuc_advisory.install_authenticated_advisory(
                    "https://example.com/advisory.json",
                    digest,
                    destination,
                    timeout=10,
                    current_advisory=current,
                )
            self.assertFalse(destination.exists())

            installed = cuc_advisory.install_authenticated_advisory(
                "https://example.com/advisory.json",
                digest,
                destination,
                timeout=10,
            )
            self.assertEqual(installed, older)
            self.assertEqual(cuc_advisory.load_authenticated_cache(destination), older)

    def test_signed_advisory_verifies_ed25519_signature_and_expiry(self) -> None:
        advisory = self.valid_advisory()
        payload = json.dumps(advisory).encode("utf-8")
        private_key = Ed25519PrivateKey.generate()
        signature = base64.b64encode(private_key.sign(payload))

        class Response:
            headers = {}

            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return self.body

        with tempfile.TemporaryDirectory() as directory:
            public_key = Path(directory) / "advisory-key.pem"
            public_key.write_bytes(
                private_key.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
            with patch(
                "cuc_advisory.urllib.request.urlopen",
                side_effect=[Response(payload), Response(signature)],
            ):
                downloaded, verified = cuc_advisory.download_signed_advisory(
                    "https://example.com/advisory.json",
                    "https://example.com/advisory.json.sig",
                    public_key,
                    timeout=10,
                )
        self.assertEqual(downloaded, payload)
        self.assertEqual(verified, advisory)
        self.assertTrue(
            cuc_advisory.advisory_is_expired(
                {**advisory, "expires_at": "2026-08-01"},
                today=date(2026, 8, 8),
            )
        )


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostic_export_redacts_identity_secrets_and_package_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "app.log"
            log_path.write_text(
                f"home={Path.home()} password=hunter2 user@example.com\n",
                encoding="utf-8",
            )
            config = cuc.Config(
                log_file=str(log_path),
                spoken_name="Private Name",
            )
            state = cuc.AppState(
                {
                    "pacman": cuc.UpdateResult(
                        "pacman",
                        "Pacman",
                        "",
                        available=True,
                        count=1,
                        items=["private-package-name"],
                    )
                }
            )
            destination = root / "diagnostics.zip"
            cuc_diagnostics.export_diagnostics(destination, config, state=state)

            with zipfile.ZipFile(destination) as archive:
                log_text = archive.read("app.log").decode("utf-8")
                config_text = archive.read("config.json").decode("utf-8")
                state_text = archive.read("state.json").decode("utf-8")
            self.assertNotIn("hunter2", log_text)
            self.assertNotIn("user@example.com", log_text)
            self.assertNotIn(str(Path.home()), log_text)
            self.assertNotIn("Private Name", config_text)
            self.assertNotIn("private-package-name", state_text)

    def test_logging_uses_a_rotating_file_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cuc.configure_logging(Path(directory) / "app.log")
            self.assertTrue(
                any(
                    isinstance(handler, logging.handlers.RotatingFileHandler)
                    for handler in logging.getLogger().handlers
                )
            )


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

    def test_helper_uses_a_minimal_environment_when_authorized(self) -> None:
        with patch("cuc_privileged.parse_args", return_value=type("Args", (), {"source": "pacman"})()), \
             patch("cuc_privileged.os.geteuid", return_value=0), \
             patch("cuc_privileged.subprocess.call", return_value=0) as call:
            self.assertEqual(cuc_privileged.main(), 0)
        environment = call.call_args.kwargs["env"]
        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertNotIn("SUDO_ASKPASS", environment)


class DesktopSecurityRegressionTests(unittest.TestCase):
    def test_desktop_uses_shared_all_current_status(self) -> None:
        source = Path("cuc_desktop.py").read_text(encoding="utf-8")
        self.assertIn("self.headline.set_text(cuc.ALL_CURRENT_STATUS)", source)

    def test_desktop_uses_no_password_entry_or_inherited_credential_pipe(self) -> None:
        source = Path("cuc_desktop.py").read_text(encoding="utf-8")
        self.assertNotIn("Gtk.PasswordEntry", source)
        self.assertNotIn("CUC_ASKPASS_FD", source)
        self.assertNotIn("pass_fds", source)
        self.assertNotIn("Gtk.Dialog", source)


if __name__ == "__main__":
    unittest.main()
