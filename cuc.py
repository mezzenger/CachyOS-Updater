#!/usr/bin/env python3
"""
CachyOS Multi-Source Update Center

A Python rewrite of the user's Fish update checker with these upgrades:
- Parallel update checks (pacman, AUR, flatpak, snap)
- Rich terminal interface
- Persistent JSON config
- Structured logging
- Background voice notifications
- JSON export / scripting support
- Safer subprocess handling with timeouts
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from cuc_advisory import (
    AdvisoryUpdateError,
    advisory_is_expired,
    install_authenticated_advisory,
    install_signed_advisory,
    load_authenticated_cache,
    validate_advisory,
)
from cuc_controller import ExecutionOutcome, UpdateController, UpdateExecutor
from cuc_diagnostics import export_diagnostics as export_diagnostics
from cuc_providers import ProviderRegistry, UpdateProvider

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.prompt import Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("This script needs the 'rich' package.")
    print("Install it with: sudo pacman -S python-rich")
    sys.exit(1)

APP_NAME = "cachyos-update-center"
__version__ = "0.5.2"
DEFAULT_PIPER_MODEL = "/usr/share/piper-voices/en/en_GB/alba/medium/en_GB-alba-medium.onnx"
DEFAULT_PREVIEW_DIR = Path.home() / ".cache" / "update-check-preview"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / APP_NAME / "config.json"
DEFAULT_LOG_PATH = Path.home() / ".local" / "state" / APP_NAME / "app.log"
DEFAULT_WINDOW_STATE_PATH = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / APP_NAME / "window-state.json"
DEFAULT_ADVISORY_CACHE = Path.home() / ".cache" / APP_NAME / "aur-security-advisories.json"
DEFAULT_TIMEOUT = 120
MAX_DETAIL_ROWS = 18
VOICE_TIMEOUT = 45
PLAYER_TIMEOUT = 60
CHECK_FAILURE_EXIT = 1
SECURITY_BLOCK_EXIT = 3
UPDATE_SOURCES = ("pacman", "aur", "flatpak", "snap")
SOURCE_DISPLAY_NAMES = {
    "pacman": "Pacman",
    "aur": "AUR",
    "flatpak": "Flatpak",
    "snap": "Snap",
}
SUPPORTED_AUR_HELPERS = ("yay", "paru", "pikaur")
AUR_ADVISORY_PATH = Path(__file__).with_name("data") / "aur-security-advisories.json"
AUR_RPC_URL = "https://aur.archlinux.org/rpc/v5/info"
AUR_RPC_BATCH_SIZE = 50
AUR_NONINTERACTIVE_OPTIONS = {
    "yay": ["--noconfirm", "--sudoloop", "--sudoflags", "-n"],
    "paru": ["--noconfirm", "--sudoloop", "--sudoflags", "-n"],
    "pikaur": ["--noconfirm"],
}

console = Console()
voice_thread_lock = threading.Lock()


class ConfigError(ValueError):
    """Raised when a configuration file cannot be safely used."""


@dataclass(frozen=True, slots=True)
class WindowState:
    width: int = 960
    height: int = 640
    maximized: bool = False


def load_window_state(path: Path = DEFAULT_WINDOW_STATE_PATH) -> WindowState:
    """Load safe desktop geometry, falling back when state is absent or damaged."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        width = payload["width"]
        height = payload["height"]
        maximized = payload["maximized"]
        if (
            not isinstance(payload, dict)
            or isinstance(width, bool)
            or not isinstance(width, int)
            or isinstance(height, bool)
            or not isinstance(height, int)
            or not isinstance(maximized, bool)
        ):
            raise ValueError("invalid window state")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return WindowState()
    return WindowState(
        width=max(460, min(width, 16384)),
        height=max(480, min(height, 16384)),
        maximized=maximized,
    )


def save_window_state(
    state: WindowState,
    path: Path = DEFAULT_WINDOW_STATE_PATH,
) -> None:
    """Atomically persist desktop geometry without touching user configuration."""
    normalized = WindowState(
        width=max(460, min(state.width, 16384)),
        height=max(480, min(state.height, 16384)),
        maximized=bool(state.maximized),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(normalized), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_signed_advisory_settings(
    feed_url: str,
    signature_url: str,
    public_key_file: str,
) -> tuple[str, str, str]:
    """Validate and normalize the optional signed-advisory configuration."""
    values = tuple(value.strip() for value in (feed_url, signature_url, public_key_file))
    if not any(values):
        return values
    if not all(values):
        raise ConfigError(
            "Signed advisory updates require the feed URL, signature URL, and public key file together."
        )
    for label, url in (("feed", values[0]), ("signature", values[1])):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigError(f"The signed advisory {label} URL must be a valid HTTPS URL.")
    key_path = Path(values[2]).expanduser()
    if not key_path.is_absolute():
        raise ConfigError("The signed advisory public key file must use an absolute path.")
    return values[0], values[1], str(key_path)


@dataclass(slots=True)
class Config:
    voice_enabled: bool = True
    piper_model: str = DEFAULT_PIPER_MODEL
    preview_dir: str = str(DEFAULT_PREVIEW_DIR)
    log_file: str = str(DEFAULT_LOG_PATH)
    command_timeout: int = DEFAULT_TIMEOUT
    installation_timeout: int = 3600
    max_result_age_minutes: int = 15
    minimum_free_space_mb: int = 1024
    spoken_name: str = "Cozmik"
    aur_helper: str = "auto"
    notifications_enabled: bool = True
    advisory_cache_file: str = str(DEFAULT_ADVISORY_CACHE)
    advisory_feed_url: str = ""
    advisory_signature_url: str = ""
    advisory_public_key_file: str = ""
    advisory_refresh_hours: int = 24
    enabled_sources: dict[str, bool] = field(
        default_factory=lambda: {source: True for source in UPDATE_SOURCES}
    )

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            if not path.exists():
                return cls()
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"Configuration {path} must contain a JSON object.")

        def string(name: str, default: str) -> str:
            value = data.get(name, default)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"Configuration field '{name}' must be a non-empty string.")
            return value

        def optional_string(name: str, default: str = "") -> str:
            value = data.get(name, default)
            if not isinstance(value, str):
                raise ConfigError(f"Configuration field '{name}' must be a string.")
            return value.strip()

        voice_enabled = data.get("voice_enabled", True)
        if not isinstance(voice_enabled, bool):
            raise ConfigError("Configuration field 'voice_enabled' must be true or false.")

        def positive_integer(name: str, default: int) -> int:
            value = data.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConfigError(f"Configuration field '{name}' must be a positive integer.")
            return value

        def boolean(name: str, default: bool) -> bool:
            value = data.get(name, default)
            if not isinstance(value, bool):
                raise ConfigError(f"Configuration field '{name}' must be true or false.")
            return value

        enabled_sources = data.get(
            "enabled_sources", {source: True for source in UPDATE_SOURCES}
        )
        if not isinstance(enabled_sources, dict):
            raise ConfigError("Configuration field 'enabled_sources' must be an object.")
        unknown_sources = set(enabled_sources) - set(UPDATE_SOURCES)
        if unknown_sources:
            raise ConfigError(
                "Configuration field 'enabled_sources' contains unknown source(s): "
                + ", ".join(sorted(unknown_sources))
            )
        normalized_sources = {
            source: enabled_sources.get(source, True) for source in UPDATE_SOURCES
        }
        if any(not isinstance(value, bool) for value in normalized_sources.values()):
            raise ConfigError("Every 'enabled_sources' value must be true or false.")

        aur_helper = string("aur_helper", "auto").lower()
        if aur_helper not in ("auto", *SUPPORTED_AUR_HELPERS):
            choices = ", ".join(("auto", *SUPPORTED_AUR_HELPERS))
            raise ConfigError(f"Configuration field 'aur_helper' must be one of: {choices}.")

        advisory_settings = validate_signed_advisory_settings(
            optional_string("advisory_feed_url"),
            optional_string("advisory_signature_url"),
            optional_string("advisory_public_key_file"),
        )

        return cls(
            voice_enabled=voice_enabled,
            piper_model=string("piper_model", DEFAULT_PIPER_MODEL),
            preview_dir=string("preview_dir", str(DEFAULT_PREVIEW_DIR)),
            log_file=string("log_file", str(DEFAULT_LOG_PATH)),
            command_timeout=positive_integer("command_timeout", DEFAULT_TIMEOUT),
            installation_timeout=positive_integer("installation_timeout", 3600),
            max_result_age_minutes=positive_integer("max_result_age_minutes", 15),
            minimum_free_space_mb=positive_integer("minimum_free_space_mb", 1024),
            spoken_name=string("spoken_name", "Cozmik"),
            aur_helper=aur_helper,
            notifications_enabled=boolean("notifications_enabled", True),
            advisory_cache_file=string(
                "advisory_cache_file",
                str(DEFAULT_ADVISORY_CACHE),
            ),
            advisory_feed_url=advisory_settings[0],
            advisory_signature_url=advisory_settings[1],
            advisory_public_key_file=advisory_settings[2],
            advisory_refresh_hours=positive_integer("advisory_refresh_hours", 24),
            enabled_sources=normalized_sources,
        )

    def save(self, path: Path) -> None:
        # Never silently replace a configuration the application cannot read.
        # This leaves the original file available for manual recovery.
        if path.exists():
            type(self).load(path)
        (
            self.advisory_feed_url,
            self.advisory_signature_url,
            self.advisory_public_key_file,
        ) = validate_signed_advisory_settings(
            self.advisory_feed_url,
            self.advisory_signature_url,
            self.advisory_public_key_file,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)


@dataclass(slots=True)
class UpdateResult:
    source: str
    display_name: str
    command_hint: str
    available_on_system: bool = True
    enabled: bool = True
    available: bool = False
    count: int = 0
    items: list[str] = field(default_factory=list)
    error: str | None = None
    install_allowed: bool = True
    security_status: str = "not-applicable"
    security_warnings: list[str] = field(default_factory=list)
    security_advisory_url: str = ""
    security_advisory_updated_at: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class AppState:
    results: dict[str, UpdateResult] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        return {name: result.count for name, result in self.results.items()}

    def any_updates(self) -> bool:
        return any(result.enabled and result.available for result in self.results.values())

    def failed_results(self) -> list[UpdateResult]:
        return [
            result
            for result in self.results.values()
            if result.enabled and result.available_on_system and result.error
        ]

    def unavailable_results(self) -> list[UpdateResult]:
        return [
            result
            for result in self.results.values()
            if result.enabled and not result.available_on_system
        ]

    def is_healthy(self) -> bool:
        return not self.failed_results()

    def installable_sources(self) -> list[str]:
        return [
            source
            for source, result in self.results.items()
            if result.enabled and result.available and result.install_allowed and not result.error
        ]

    def stale_sources(
        self,
        max_age_minutes: int,
        sources: Iterable[str] | None = None,
        *,
        now: datetime | None = None,
    ) -> list[str]:
        current_time = now or datetime.now()
        requested = set(sources) if sources is not None else set(self.results)
        stale: list[str] = []
        for source, result in self.results.items():
            if source not in requested or not result.enabled:
                continue
            try:
                checked_at = datetime.fromisoformat(result.checked_at)
            except ValueError:
                stale.append(source)
                continue
            if (current_time - checked_at).total_seconds() > max_age_minutes * 60:
                stale.append(source)
        return stale


@dataclass(slots=True)
class AurSecurityReport:
    package_names: list[str]
    compromised: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    incident_active: bool = False
    advisory_url: str = ""
    advisory_updated_at: str = ""
    error: str | None = None

    def install_allowed(self) -> bool:
        return not (self.compromised or self.orphaned or self.missing or self.error)

    def status(self) -> str:
        if not self.install_allowed():
            return "blocked"
        if self.incident_active:
            return "review-required"
        return "clear"

    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.compromised:
            warnings.append(
                "Known affected AUR package(s): " + ", ".join(self.compromised) + "."
            )
        if self.orphaned:
            warnings.append(
                "Currently orphaned AUR package(s): " + ", ".join(self.orphaned) + "."
            )
        if self.missing:
            warnings.append(
                "Package metadata was not returned by the AUR: " + ", ".join(self.missing) + "."
            )
        if self.error:
            warnings.append(f"AUR safety metadata could not be verified: {self.error}")
        if self.incident_active:
            warnings.append(
                "Arch Linux reports an active AUR malicious-package incident; review PKGBUILD "
                "and install-script changes before continuing."
            )
        return warnings


class SessionPhase(str, Enum):
    IDLE = "idle"
    REVIEWING = "reviewing"
    INSTALLING = "installing"
    CANCELLING = "cancelling"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(slots=True)
class UpdateSession:
    phase: SessionPhase = SessionPhase.IDLE
    targets: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    message: str = ""

    def begin_review(self, targets: Iterable[str]) -> bool:
        if self.phase not in (SessionPhase.IDLE, SessionPhase.COMPLETE, SessionPhase.FAILED):
            return False
        self.phase = SessionPhase.REVIEWING
        self.targets = list(targets)
        self.commands = []
        self.message = ""
        return True

    def begin_install(self) -> None:
        if self.phase != SessionPhase.REVIEWING:
            raise RuntimeError(f"cannot install from session phase {self.phase.value}")
        self.phase = SessionPhase.INSTALLING

    def request_cancel(self) -> bool:
        if self.phase != SessionPhase.INSTALLING:
            return False
        self.phase = SessionPhase.CANCELLING
        return True

    def finish(self, message: str = "") -> None:
        self.phase = SessionPhase.COMPLETE
        self.message = message

    def fail(self, message: str) -> None:
        self.phase = SessionPhase.FAILED
        self.message = message

    def reset(self) -> None:
        self.phase = SessionPhase.IDLE
        self.targets = []
        self.commands = []
        self.message = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "targets": list(self.targets),
            "commands": [list(command) for command in self.commands],
            "message": self.message,
        }


@dataclass(slots=True)
class PreflightIssue:
    code: str
    message: str
    blocking: bool = True
    source: str | None = None


@dataclass(frozen=True, slots=True)
class RunningPackageManager:
    name: str
    pid: int


@dataclass(slots=True)
class PolkitReadiness:
    ready: bool
    message: str


@dataclass(slots=True)
class InstallOutcome:
    source: str
    status: str
    message: str
    exit_code: int | None = None


@dataclass(slots=True)
class InstallSummary:
    outcomes: list[InstallOutcome] = field(default_factory=list)

    def add(
        self,
        source: str,
        status: str,
        message: str,
        exit_code: int | None = None,
    ) -> None:
        self.outcomes.append(InstallOutcome(source, status, message, exit_code))

    def extend_execution(self, outcomes: Iterable[ExecutionOutcome]) -> None:
        for outcome in outcomes:
            self.add(outcome.source, outcome.status, outcome.message, outcome.exit_code)

    def by_status(self, status: str) -> list[InstallOutcome]:
        return [outcome for outcome in self.outcomes if outcome.status == status]

    def successful(self) -> bool:
        return bool(self.outcomes) and all(
            outcome.status == "succeeded" for outcome in self.outcomes
        )

    def message(self) -> str:
        if not self.outcomes:
            return "No update sources were run."
        groups = []
        for status in ("succeeded", "failed", "cancelled", "timed-out", "skipped"):
            sources = [
                SOURCE_DISPLAY_NAMES.get(outcome.source, outcome.source)
                for outcome in self.by_status(status)
            ]
            if sources:
                groups.append(f"{status.capitalize()}: {', '.join(sources)}")
        return " · ".join(groups)


def configure_logging(log_path: Path) -> None:
    format_string = "%(asctime)s %(levelname)s %(message)s"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        logging.basicConfig(
            handlers=[handler],
            level=logging.INFO,
            format=format_string,
            force=True,
        )
    except OSError as exc:
        logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=format_string, force=True)
        logging.warning("Cannot open log file %s; using stderr instead: %s", log_path, exc)

    def log_uncaught_exception(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback)
            return
        logging.critical(
            "Uncaught application exception",
            exc_info=(exc_type, exc_value, traceback),
        )

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        logging.critical(
            "Uncaught worker exception in %s",
            args.thread.name if args.thread else "unknown thread",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_uncaught_exception
    threading.excepthook = log_thread_exception


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def check_polkit_readiness(
    helper_path: Path = Path("/usr/lib/cachyos-update-center/cuc-privileged"),
    policy_path: Path = Path("/usr/share/polkit-1/actions/io.github.alvin.Cuc.policy"),
) -> PolkitReadiness:
    if not command_exists("pkexec"):
        return PolkitReadiness(False, "pkexec is not installed; use the terminal frontend for system updates.")
    if not helper_path.is_file():
        return PolkitReadiness(False, "The CUC privileged helper is not installed.")
    if not policy_path.is_file():
        return PolkitReadiness(False, "The CUC Polkit policy is not installed.")
    try:
        helper_stat = helper_path.stat()
        policy_stat = policy_path.stat()
    except OSError as exc:
        return PolkitReadiness(False, f"Could not inspect the Polkit integration: {exc}")
    if helper_path.is_symlink() or policy_path.is_symlink():
        return PolkitReadiness(False, "The CUC Polkit files must not be symbolic links.")
    if helper_stat.st_uid != 0 or policy_stat.st_uid != 0:
        return PolkitReadiness(False, "The CUC Polkit files are not owned by root.")
    unsafe_write_bits = stat.S_IWGRP | stat.S_IWOTH
    if helper_stat.st_mode & unsafe_write_bits or policy_stat.st_mode & unsafe_write_bits:
        return PolkitReadiness(False, "The CUC Polkit files are writable by non-root users.")
    if not os.access(helper_path, os.X_OK):
        return PolkitReadiness(False, "The CUC privileged helper is not executable.")
    return PolkitReadiness(True, "Native Polkit authentication is ready for Pacman and Snap.")


def _running_package_managers(proc_root: Path = Path("/proc")) -> list[RunningPackageManager]:
    manager_names = {"pacman", "yay", "paru", "pikaur", "flatpak", "snap"}
    running: list[RunningPackageManager] = []
    try:
        process_directories = list(proc_root.iterdir())
    except OSError:
        return []
    for process_directory in process_directories:
        if not process_directory.name.isdigit() or process_directory.name == str(os.getpid()):
            continue
        try:
            command = (process_directory / "comm").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if command in manager_names:
            try:
                status = (process_directory / "status").read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                status = ""
            state_line = next(
                (line for line in status.splitlines() if line.startswith("State:")),
                "",
            )
            if state_line.split()[1:2] == ["Z"]:
                continue
            running.append(RunningPackageManager(command, int(process_directory.name)))
    return sorted(running, key=lambda process: (process.name, process.pid))


def check_update_preflight(
    targets: Iterable[str],
    state: AppState,
    config: Config,
    *,
    pacman_lock: Path = Path("/var/lib/pacman/db.lck"),
    disk_path: Path = Path("/"),
    proc_root: Path = Path("/proc"),
) -> list[PreflightIssue]:
    selected = list(dict.fromkeys(targets))
    issues: list[PreflightIssue] = []
    for source in selected:
        result = state.results.get(source)
        if result is None:
            issues.append(
                PreflightIssue(
                    "missing-result",
                    f"No update result is available for {SOURCE_DISPLAY_NAMES.get(source, source)}.",
                    source=source,
                )
            )
        elif result.error:
            detail = result.error.lower()
            code = (
                "network-unavailable"
                if any(
                    marker in detail
                    for marker in ("network", "resolve", "fetch", "timed out", "connection")
                )
                else "source-check-failed"
            )
            issues.append(
                PreflightIssue(
                    code,
                    f"{result.display_name} cannot proceed because its check failed: {result.error}",
                    source=source,
                )
            )
    stale = state.stale_sources(config.max_result_age_minutes, selected)
    if stale:
        issues.append(
            PreflightIssue(
                "stale-results",
                "Refresh required: update results are stale for "
                + ", ".join(SOURCE_DISPLAY_NAMES.get(source, source) for source in stale)
                + ".",
            )
        )
    if "pacman" in selected or "aur" in selected:
        if pacman_lock.exists():
            issues.append(
                PreflightIssue(
                    "pacman-lock",
                    f"Pacman is locked by another operation: {pacman_lock}.",
                    source="pacman",
                )
            )
    conflicting_names: set[str] = set()
    if {"pacman", "aur"} & set(selected):
        conflicting_names.update(("pacman", "yay", "paru", "pikaur"))
    if "flatpak" in selected:
        conflicting_names.add("flatpak")
    if "snap" in selected:
        conflicting_names.add("snap")
    running = [
        process
        for process in _running_package_managers(proc_root)
        if process.name in conflicting_names
    ]
    if running:
        process_details = ", ".join(
            f"{process.name} (PID {process.pid})" for process in running
        )
        issues.append(
            PreflightIssue(
                "package-manager-running",
                "A conflicting package manager is still running: " + process_details + ".",
            )
        )
    try:
        free_mb = shutil.disk_usage(disk_path).free // (1024 * 1024)
        if free_mb < config.minimum_free_space_mb:
            issues.append(
                PreflightIssue(
                    "disk-space",
                    f"Only {free_mb} MiB is free; at least "
                    f"{config.minimum_free_space_mb} MiB is required.",
                )
            )
    except OSError as exc:
        issues.append(
            PreflightIssue(
                "disk-check",
                f"Free disk space could not be checked: {exc}",
                blocking=False,
            )
        )
    if "snap" in selected and command_exists("systemctl"):
        try:
            snap_status = subprocess.run(
                ["systemctl", "is-active", "--quiet", "snapd.socket"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=min(config.command_timeout, 10),
            )
            if snap_status.returncode:
                issues.append(
                    PreflightIssue(
                        "snap-service",
                        "Snap is selected but snapd.socket is not active.",
                        source="snap",
                    )
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(
                PreflightIssue(
                    "snap-service-check",
                    f"Snap service readiness could not be checked: {exc}",
                    blocking=False,
                    source="snap",
                )
            )
    return issues


def run_capture(
    cmd: list[str],
    *,
    timeout: int,
    ok_returncodes: Iterable[int] = (0,),
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    logging.info("Running command: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if proc.returncode not in set(ok_returncodes):
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"Command failed with exit code {proc.returncode}."
        raise RuntimeError(detail)
    return proc


def run_interactive(cmd: list[str], *, timeout: int | None = None) -> int:
    logging.info("Starting interactive command: %s", " ".join(cmd))
    try:
        if timeout is None:
            return subprocess.call(cmd)
        process = subprocess.Popen(cmd, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logging.error("Interactive command timed out after %s seconds: %s", timeout, " ".join(cmd))
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            return 124
    except OSError as exc:
        logging.error("Could not start interactive command %s: %s", cmd[0] if cmd else "", exc)
        console.print(f"[bold red]Could not start update command:[/bold red] {exc}")
        return 127
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted by user.[/bold yellow]")
        return 130


def acquire_lock(lockfile: Path, *, stale_after_seconds: int = 120) -> bool:
    try:
        fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - lockfile.stat().st_mtime
            if age > stale_after_seconds:
                lockfile.unlink(missing_ok=True)
                fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
                os.close(fd)
                logging.warning("Removed stale voice lock: %s", lockfile)
                return True
        except Exception:
            pass
        return False
    except OSError as exc:
        logging.warning("Cannot create voice lock %s: %s", lockfile, exc)
        return False


def release_lock(lockfile: Path) -> None:
    try:
        lockfile.unlink(missing_ok=True)
    except Exception:
        pass


def _speak_worker(text: str, config: Config) -> None:
    if not config.voice_enabled:
        return

    model = Path(config.piper_model)
    preview_dir = Path(config.preview_dir)
    lockfile = preview_dir / "voice.lock"

    if not model.is_file() or not command_exists("piper-tts"):
        return

    try:
        preview_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logging.warning("Voice skipped because its preview directory is unavailable: %s", exc)
        return
    if not acquire_lock(lockfile):
        logging.warning("Voice skipped because lock is active: %s", lockfile)
        return

    wav_path: str | None = None
    try:
        # Prefer the exact raw Piper -> aplay pipeline that already worked
        # in manual testing on the user's machine.
        if command_exists("aplay") and command_exists("bash"):
            pipeline = 'piper-tts --model "$1" --output-raw 2>/dev/null | aplay -q -r 22050 -f S16_LE -t raw -'
            pipe_run = subprocess.run(
                ["bash", "-lc", pipeline, "bash", str(model)],
                input=f"{text}\n",
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=VOICE_TIMEOUT,
            )
            if pipe_run.returncode == 0:
                return
            logging.warning("Raw voice pipeline failed with exit %s", pipe_run.returncode)

        # Fallback: create a file and try common players.
        with tempfile.NamedTemporaryFile(
            prefix="update-check-voice-",
            suffix=".wav",
            dir=preview_dir,
            delete=False,
        ) as temp_wav:
            wav_path = temp_wav.name

        synth = subprocess.run(
            ["piper-tts", "--model", str(model), "--output_file", wav_path],
            input=f"{text}\n",
            text=True,
            capture_output=True,
            check=False,
            timeout=VOICE_TIMEOUT,
        )
        if synth.returncode != 0:
            logging.warning("Voice synthesis failed: %s", synth.stderr.strip())
            return

        for player in (["pw-play"], ["paplay"], ["aplay", "-q"]):
            if not command_exists(player[0]):
                continue
            play = subprocess.run(
                player + [wav_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=PLAYER_TIMEOUT,
            )
            if play.returncode == 0:
                return
            logging.warning("Audio player failed: %s (exit %s)", " ".join(player), play.returncode)

        logging.warning("All audio players failed for %s", wav_path)
    except Exception as exc:
        logging.warning("Voice playback failed: %s", exc)
    finally:
        if wav_path:
            Path(wav_path).unlink(missing_ok=True)
        release_lock(lockfile)


def speak_async(text: str, config: Config) -> None:
    def launcher() -> None:
        with voice_thread_lock:
            _speak_worker(text, config)

    thread = threading.Thread(target=launcher, daemon=True)
    thread.start()


def speak_sync(text: str, config: Config) -> None:
    with voice_thread_lock:
        _speak_worker(text, config)


def check_pacman_updates(config: Config) -> UpdateResult:
    result = UpdateResult(
        source="pacman",
        display_name="Pacman",
        command_hint="sudo pacman -S pacman-contrib",
    )
    if not command_exists("checkupdates"):
        result.available_on_system = False
        result.error = "checkupdates not found. Install pacman-contrib."
        return result

    try:
        with tempfile.TemporaryDirectory(prefix="cuc-checkupdates-") as database:
            env = {**os.environ, "CHECKUPDATES_DB": database}
            proc = run_capture(
                ["checkupdates"],
                timeout=config.command_timeout,
                ok_returncodes=(0, 2),
                env=env,
            )
        items = parse_update_lines(proc.stdout)
        result.items = items
        result.count = len(items)
        result.available = result.count > 0
    except Exception as exc:
        result.error = str(exc)
    return result


def check_flatpak_updates(config: Config) -> UpdateResult:
    result = UpdateResult(
        source="flatpak",
        display_name="Flatpak",
        command_hint="sudo pacman -S flatpak",
    )
    if not command_exists("flatpak"):
        result.available_on_system = False
        result.error = "Flatpak is not installed."
        return result

    try:
        proc = run_capture(
            ["flatpak", "remote-ls", "--updates"],
            timeout=config.command_timeout,
            ok_returncodes=(0,),
        )
        items = parse_update_lines(proc.stdout, preserve_spacing=True)
        result.items = items
        result.count = len(items)
        result.available = result.count > 0
    except Exception as exc:
        result.error = str(exc)
    return result


def parse_snap_updates(output: str) -> list[str]:
    """Return data rows from ``snap refresh --list`` output."""
    items: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line == "All snaps up to date.":
            continue
        # The table header starts with the exact column name "Name". Package
        # names such as snap-store and snapcraft are legitimate data rows.
        if line.split(maxsplit=1)[0].lower() == "name":
            continue
        items.append(raw.rstrip())
    return items


def check_snap_updates(config: Config) -> UpdateResult:
    result = UpdateResult(
        source="snap",
        display_name="Snap",
        command_hint="sudo pacman -S snapd",
    )
    if not command_exists("snap"):
        result.available_on_system = False
        result.error = "Snap is not installed."
        return result

    try:
        proc = run_capture(["snap", "refresh", "--list"], timeout=config.command_timeout, ok_returncodes=(0,))
        items = parse_snap_updates(proc.stdout)
        result.items = items
        result.count = len(items)
        result.available = result.count > 0
    except Exception as exc:
        result.error = str(exc)
    return result


def find_aur_helper(config: Config) -> str | None:
    if config.aur_helper != "auto":
        return config.aur_helper if command_exists(config.aur_helper) else None
    for helper in SUPPORTED_AUR_HELPERS:
        if command_exists(helper):
            return helper
    return None


def parse_update_lines(output: str, *, preserve_spacing: bool = False) -> list[str]:
    return [
        line.rstrip() if preserve_spacing else line.strip()
        for line in output.splitlines()
        if line.strip()
    ]


def aur_package_name(update_line: str) -> str:
    """Extract the package name from common AUR-helper ``-Qua`` output."""
    return update_line.strip().split(maxsplit=1)[0] if update_line.strip() else ""


def load_aur_advisory(path: Path = AUR_ADVISORY_PATH) -> dict[str, Any]:
    try:
        advisory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read bundled advisory data: {exc}") from exc
    try:
        return validate_advisory(advisory)
    except AdvisoryUpdateError as exc:
        raise RuntimeError(f"bundled AUR advisory data has an invalid format: {exc}") from exc


def load_effective_aur_advisory(config: Config) -> dict[str, Any]:
    cache_path = Path(config.advisory_cache_file)
    cached = load_authenticated_cache(cache_path)
    bundled = load_aur_advisory()
    current = cached or bundled
    signed_feed = (
        config.advisory_feed_url,
        config.advisory_signature_url,
        config.advisory_public_key_file,
    )
    refresh_due = True
    try:
        refresh_due = (
            time.time() - cache_path.stat().st_mtime
            > config.advisory_refresh_hours * 3600
        )
    except OSError:
        pass
    if all(signed_feed) and refresh_due:
        try:
            current = install_signed_advisory(
                config.advisory_feed_url,
                config.advisory_signature_url,
                Path(config.advisory_public_key_file),
                cache_path,
                timeout=min(config.command_timeout, 30),
                current_advisory=current,
            )
            logging.info("Installed refreshed signed AUR advisory dated %s", current["updated_at"])
        except AdvisoryUpdateError as exc:
            logging.warning("Signed AUR advisory refresh failed: %s", exc)
    if advisory_is_expired(current):
        raise RuntimeError(
            "AUR advisory data has expired; install a current signed advisory before updating AUR packages."
        )
    return current


def fetch_aur_metadata(package_names: list[str], *, timeout: int) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for start in range(0, len(package_names), AUR_RPC_BATCH_SIZE):
        batch = package_names[start : start + AUR_RPC_BATCH_SIZE]
        query = urllib.parse.urlencode([("arg[]", name) for name in batch])
        request = urllib.request.Request(
            f"{AUR_RPC_URL}?{query}",
            headers={"User-Agent": f"{APP_NAME}/{__version__}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise RuntimeError("AUR returned an invalid metadata response")
        for item in payload["results"]:
            if isinstance(item, dict) and isinstance(item.get("Name"), str):
                metadata[item["Name"]] = item
    return metadata


def evaluate_aur_security(
    package_names: list[str],
    metadata: Mapping[str, Mapping[str, Any]],
    advisory: Mapping[str, Any],
    *,
    error: str | None = None,
) -> AurSecurityReport:
    affected = {
        str(item.get("name"))
        for item in advisory.get("affected_packages", [])
        if isinstance(item, dict) and item.get("name")
    }
    return AurSecurityReport(
        package_names=package_names,
        compromised=sorted(name for name in package_names if name in affected),
        orphaned=sorted(
            name for name in package_names
            if name in metadata and metadata[name].get("Maintainer") is None
        ),
        missing=sorted(name for name in package_names if name not in metadata) if not error else [],
        incident_active=bool(advisory.get("incident_active", False)),
        advisory_url=str(advisory.get("advisory_url", "")),
        advisory_updated_at=str(advisory.get("updated_at", "")),
        error=error,
    )


def audit_aur_updates(items: list[str], config: Config) -> AurSecurityReport:
    package_names = list(dict.fromkeys(filter(None, (aur_package_name(item) for item in items))))
    try:
        advisory = load_effective_aur_advisory(config)
    except RuntimeError as exc:
        return AurSecurityReport(package_names=package_names, error=str(exc))
    if not package_names:
        return evaluate_aur_security([], {}, advisory)
    try:
        metadata = fetch_aur_metadata(package_names, timeout=min(config.command_timeout, 20))
    except (OSError, ValueError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return evaluate_aur_security(package_names, {}, advisory, error=str(exc))
    return evaluate_aur_security(package_names, metadata, advisory)


def check_aur_updates(config: Config) -> UpdateResult:
    result = UpdateResult(
        source="aur",
        display_name="AUR",
        command_hint="Install an AUR helper such as yay, paru, or pikaur.",
    )
    helper = find_aur_helper(config)
    if helper is None:
        result.available_on_system = False
        if config.aur_helper == "auto":
            result.error = "No supported AUR helper found. Install yay, paru, or pikaur."
        else:
            result.error = f"Configured AUR helper '{config.aur_helper}' was not found."
        return result

    try:
        proc = run_capture([helper, "-Qua"], timeout=config.command_timeout, ok_returncodes=(0, 1))
        if proc.returncode != 0 and (proc.stderr or "").strip():
            raise RuntimeError(proc.stderr.strip())
        items = parse_update_lines(proc.stdout)
        result.items = items
        result.count = len(items)
        result.available = result.count > 0
        result.command_hint = f"{helper} -Sua"
        report = audit_aur_updates(items, config)
        result.install_allowed = report.install_allowed()
        result.security_status = report.status()
        result.security_warnings = report.warnings()
        result.security_advisory_url = report.advisory_url
        result.security_advisory_updated_at = report.advisory_updated_at
    except Exception as exc:
        result.error = str(exc)
    return result


def build_update_command(source: str, config: Config, *, noninteractive: bool = False) -> list[str] | None:
    if source == "pacman":
        command = ["sudo", "-n", "pacman", "-Syu"] if noninteractive else ["sudo", "pacman", "-Syu"]
        return [*command, "--noconfirm"] if noninteractive else command
    if source == "aur":
        helper = find_aur_helper(config)
        if not helper:
            return None
        command = [helper, "-Sua"]
        if noninteractive:
            command.extend(AUR_NONINTERACTIVE_OPTIONS.get(helper, ["--noconfirm"]))
        return command
    if source == "flatpak":
        return ["flatpak", "update", "-y"] if noninteractive else ["flatpak", "update"]
    if source == "snap":
        return ["sudo", "-n", "snap", "refresh"] if noninteractive else ["sudo", "snap", "refresh"]
    return None


def build_update_commands(
    targets: Iterable[str],
    config: Config,
    *,
    noninteractive: bool = False,
) -> list[list[str]]:
    return [
        command
        for _source, command in UPDATE_CONTROLLER.build_plan(
            targets,
            config,
            noninteractive=noninteractive,
        )
        if command is not None
    ]


CHECKERS = {
    "pacman": check_pacman_updates,
    "aur": check_aur_updates,
    "flatpak": check_flatpak_updates,
    "snap": check_snap_updates,
}


def _disabled_result(source: str) -> UpdateResult:
    return UpdateResult(
        source=source,
        display_name=SOURCE_DISPLAY_NAMES[source],
        command_hint="Enable this source in Settings to check it.",
        enabled=False,
    )


PROVIDERS = ProviderRegistry(
    UpdateProvider(
        source=source,
        display_name=SOURCE_DISPLAY_NAMES[source],
        check=checker,
        build_command=lambda config, noninteractive, source=source: build_update_command(
            source,
            config,
            noninteractive=noninteractive,
        ),
        disabled_result=lambda source=source: _disabled_result(source),
    )
    for source, checker in CHECKERS.items()
)


UPDATE_CONTROLLER = UpdateController(
    PROVIDERS,
    lambda source, display_name, error: UpdateResult(
        source=source,
        display_name=display_name,
        command_hint="",
        error=error,
    ),
)
UPDATE_EXECUTOR = UpdateExecutor()


def check_all_parallel(
    config: Config,
    *,
    show_progress: bool = True,
    sources: Iterable[str] | None = None,
    prior_state: AppState | None = None,
) -> AppState:
    requested_sources = list(sources) if sources is not None else list(PROVIDERS)
    requested = set(requested_sources)
    selected = [
        source
        for source in PROVIDERS
        if source in requested and config.enabled_sources.get(source, True)
    ]
    tasks: dict[str, int] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
        disable=not show_progress,
    ) as progress:
        for source in selected:
            tasks[source] = progress.add_task(f"Checking {source}…", total=None)

        def finish_task(source: str, _result: UpdateResult) -> None:
            progress.update(tasks[source], description=f"[green]Checked {source}")

        results = UPDATE_CONTROLLER.check(
            config,
            sources=requested_sources,
            prior_results=prior_state.results if prior_state else None,
            on_result=finish_task,
        )

    return AppState(results)


def write_preview_files(state: AppState, config: Config) -> Path:
    preview_dir = Path(config.preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)

    all_lines: list[str] = []
    all_lines.append("CachyOS Update Preview")
    all_lines.append("======================")
    all_lines.append("")

    for source, result in state.results.items():
        txt_path = preview_dir / f"{source}.txt"
        header = f"{result.display_name.upper()} ({result.count})"
        block: list[str] = [header, "-" * len(header)]
        if result.error:
            block.append(f"Error: {result.error}")
        elif result.available:
            block.extend(result.items)
        else:
            block.append(f"No {source} updates available.")
        txt_path.write_text("\n".join(block) + "\n", encoding="utf-8")
        all_lines.extend(block)
        all_lines.append("")

    (preview_dir / "all.txt").write_text("\n".join(all_lines), encoding="utf-8")
    (preview_dir / "summary.json").write_text(
        json.dumps({k: v.to_dict() for k, v in state.results.items()}, indent=2),
        encoding="utf-8",
    )
    return preview_dir


def write_preview_files_safely(state: AppState, config: Config) -> Path | None:
    try:
        return write_preview_files(state, config)
    except OSError as exc:
        logging.warning("Cannot write preview files: %s", exc)
        console.print(f"[yellow]Preview cache was not written:[/yellow] {exc}")
        return None


def make_header(config: Config, state: AppState) -> Panel:
    total_updates = sum(r.count for r in state.results.values())
    failed = state.failed_results()
    unavailable = state.unavailable_results()
    if failed:
        subtitle = f"Update check incomplete: {', '.join(result.display_name for result in failed)} failed"
        border_style = "red"
    elif unavailable:
        subtitle = f"{total_updates} pending update(s); unavailable: {', '.join(result.display_name for result in unavailable)}"
        border_style = "yellow"
    elif total_updates == 0:
        subtitle = "System status: fully armed and annoyingly current"
        border_style = "green"
    else:
        subtitle = f"{total_updates} pending update(s) across all sources"
        border_style = "yellow"

    title = Text("CachyOS Multi-Source Update Center", style="bold cyan")
    body = Group(
        Align.center(title),
        Align.center(Text(subtitle, style="bold white")),
        Align.center(Text(f"Voice: {'ON' if config.voice_enabled else 'OFF'}   •   Preview cache: {config.preview_dir}", style="dim")),
    )
    return Panel(body, border_style=border_style, box=box.DOUBLE)


def render_summary_table(state: AppState) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Source", style="bold cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Notes", overflow="fold")

    for result in state.results.values():
        if not result.available_on_system:
            status = "[red]Missing[/red]"
            notes = result.error or result.command_hint
        elif result.error:
            status = "[red]Error[/red]"
            notes = result.error
        elif result.available:
            status = "[yellow]Updates available[/yellow]"
            notes = f"Ready to install from {result.display_name}."
        else:
            status = "[green]Up to date[/green]"
            notes = "Nothing pending."
        table.add_row(result.display_name, status, str(result.count), notes)
    return table


def render_detail_panel(result: UpdateResult) -> Panel:
    if result.error:
        body = Text(result.error, style="bold red")
        title = f"{result.display_name} details"
        return Panel(body, title=title, border_style="red")

    if not result.items:
        return Panel(Text(f"No {result.source} updates available.", style="green"), title=f"{result.display_name} details")

    table = Table(box=box.MINIMAL_DOUBLE_HEAD, expand=True, show_header=True)
    table.add_column("Pending items", overflow="fold")
    visible_items = result.items[:MAX_DETAIL_ROWS]
    for item in visible_items:
        table.add_row(item)
    if result.count > MAX_DETAIL_ROWS:
        table.add_row(f"… and {result.count - MAX_DETAIL_ROWS} more")
    return Panel(table, title=f"{result.display_name} details", border_style="cyan")


def render_dashboard(state: AppState, config: Config) -> None:
    console.clear()
    console.print(make_header(config, state))
    console.print()
    console.print(render_summary_table(state))
    console.print()

    for source in PROVIDERS:
        if source in state.results:
            console.print(render_detail_panel(state.results[source]))
            console.print()


def compose_summary_speech(state: AppState, config: Config) -> str:
    failed = state.failed_results()
    if failed:
        return f"Update check incomplete. Could not check {', '.join(result.display_name for result in failed)}."
    if not state.any_updates():
        return f"{config.spoken_name}.. Your system is currently up to date and is operating nominally!"

    parts = []
    for result in state.results.values():
        if result.available:
            parts.append(f"{result.display_name} has {result.count} updates.")
    return " ".join(parts)


def _source_install_problem(source: str, state: AppState) -> tuple[str, str, int] | None:
    result = state.results.get(source)
    if result is None:
        return "failed", f"Unknown source: {source}.", CHECK_FAILURE_EXIT
    if not result.enabled:
        return "skipped", f"{result.display_name} is disabled.", 0
    if not result.available_on_system:
        return "failed", result.error or f"{result.display_name} is not installed.", CHECK_FAILURE_EXIT
    if result.error:
        return "failed", f"Its check failed: {result.error}", CHECK_FAILURE_EXIT
    if not result.available:
        return "skipped", f"No {result.display_name} updates are pending.", 0
    if not result.install_allowed:
        detail = " ".join(result.security_warnings) or "Security review blocked installation."
        return "failed", detail, SECURITY_BLOCK_EXIT
    return None

def install_many(
    sources: list[str],
    state: AppState,
    config: Config,
    *,
    noninteractive: bool = False,
) -> int:
    summary = InstallSummary()
    runnable: list[str] = []
    for source in dict.fromkeys(sources):
        problem = _source_install_problem(source, state)
        if problem:
            status, message, exit_code = problem
            summary.add(source, status, message, exit_code)
        else:
            runnable.append(source)

    if not runnable:
        style = "yellow" if any(outcome.status == "failed" for outcome in summary.outcomes) else "green"
        console.print(f"[{style}]{summary.message()}[/{style}]")
        failures = [
            outcome.exit_code or CHECK_FAILURE_EXIT
            for outcome in summary.outcomes
            if outcome.status == "failed"
        ]
        return failures[-1] if failures else 0

    if noninteractive and any(source in ("pacman", "aur", "snap") for source in runnable):
        try:
            authorization = subprocess.run(
                ["sudo", "-n", "-v"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=min(config.command_timeout, 30),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            console.print(f"[red]Could not validate non-interactive sudo authorization: {exc}[/red]")
            return CHECK_FAILURE_EXIT
        if authorization.returncode:
            console.print(
                "[red]Non-interactive installation requires a current sudo credential. "
                "Run 'sudo -v' in this terminal, then retry.[/red]"
            )
            return CHECK_FAILURE_EXIT

    blocking_issues = [
        issue for issue in check_update_preflight(runnable, state, config) if issue.blocking
    ]
    if blocking_issues:
        for source in runnable:
            summary.add(source, "failed", "Preflight blocked installation: " + blocking_issues[0].message)
        console.print(f"[bold red]{summary.message()}[/bold red]")
        return CHECK_FAILURE_EXIT

    plan = [
        (source, command)
        for source, command in UPDATE_CONTROLLER.build_plan(
            runnable,
            config,
            noninteractive=noninteractive,
        )
        if command is not None
    ]
    planned_sources = {source for source, _command in plan}
    for source in runnable:
        if source not in planned_sources:
            summary.add(source, "failed", "No usable update command was found.", CHECK_FAILURE_EXIT)

    def run_one(source: str, command: list[str]) -> ExecutionOutcome:
        speak_async(f"Installing {source} updates.", config)
        console.print(Rule(f"Installing {source}", style="cyan"))
        return_code = run_interactive(command, timeout=config.installation_timeout)
        if return_code == 0:
            return ExecutionOutcome(source, "succeeded", "Completed successfully.", 0)
        if return_code == 124:
            return ExecutionOutcome(source, "timed-out", "Installation timed out.", return_code)
        if return_code == 130:
            return ExecutionOutcome(source, "cancelled", "Interrupted by the user.", return_code)
        return ExecutionOutcome(source, "failed", f"Exited with status {return_code}.", return_code)

    summary.extend_execution(UPDATE_EXECUTOR.execute(plan, run_one))
    style = "green" if summary.successful() else "yellow"
    console.print(f"[{style}]{summary.message()}[/{style}]")
    failures = [
        outcome.exit_code or CHECK_FAILURE_EXIT
        for outcome in summary.outcomes
        if outcome.status not in ("succeeded", "skipped")
    ]
    return failures[-1] if failures else 0


def install_source(source: str, state: AppState, config: Config) -> int:
    return install_many([source], state, config)


def export_json(state: AppState, destination: Path | None = None) -> str:
    payload = json.dumps({k: v.to_dict() for k, v in state.results.items()}, indent=2)
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
    return payload


def check_exit_code(state: AppState) -> int:
    return CHECK_FAILURE_EXIT if not state.is_healthy() else 0


MENU_TEXT = """
[bold cyan]Actions[/bold cyan]
  [bold]1[/bold]  Install all updates
  [bold]2[/bold]  Install pacman only
  [bold]3[/bold]  Install AUR only
  [bold]4[/bold]  Install flatpak only
  [bold]5[/bold]  Install snap only
  [bold]6[/bold]  Install selected sources
  [bold]7[/bold]  Refresh update check
  [bold]8[/bold]  Toggle voice
  [bold]9[/bold]  Test voice
  [bold]e[/bold]  Export JSON summary
  [bold]0[/bold]  Quit
""".strip()


def prompt_selected_sources(state: AppState) -> list[str]:
    choices = [source for source, result in state.results.items() if result.available]
    if not choices:
        console.print("[green]Nothing to install.[/green]")
        return []

    console.print("[bold cyan]Available sources:[/bold cyan] " + ", ".join(choices))
    raw = Prompt.ask("Enter one or more sources separated by commas", default=",".join(choices))
    selected = [item.strip().lower() for item in raw.split(",") if item.strip()]
    invalid = [item for item in selected if item not in PROVIDERS]
    if invalid:
        console.print(f"[yellow]Ignoring unknown source(s):[/yellow] {', '.join(invalid)}")
    return list(dict.fromkeys(item for item in selected if item in PROVIDERS))


def interactive_loop(state: AppState, config: Config, config_path: Path, *, write_cache: bool) -> int:
    while True:
        render_dashboard(state, config)
        console.print(Panel(MENU_TEXT, border_style="blue", title="Control Deck"))
        choice = Prompt.ask("Choose an action", choices=[str(i) for i in range(10)] + ["e"], default="0")

        if choice == "1":
            install_many(list(PROVIDERS), state, config)
            state = check_all_parallel(config)
            if write_cache:
                write_preview_files_safely(state, config)
            speak_sync(compose_summary_speech(state, config), config)
        elif choice == "2":
            install_source("pacman", state, config)
            state = check_all_parallel(config)
            if write_cache:
                write_preview_files_safely(state, config)
        elif choice == "3":
            install_source("aur", state, config)
            state = check_all_parallel(config)
            if write_cache:
                write_preview_files_safely(state, config)
        elif choice == "4":
            install_source("flatpak", state, config)
            state = check_all_parallel(config)
            if write_cache:
                write_preview_files_safely(state, config)
        elif choice == "5":
            install_source("snap", state, config)
            state = check_all_parallel(config)
            if write_cache:
                write_preview_files_safely(state, config)
        elif choice == "6":
            selected = prompt_selected_sources(state)
            if selected:
                install_many(selected, state, config)
                state = check_all_parallel(config)
                if write_cache:
                    write_preview_files_safely(state, config)
        elif choice == "7":
            state = check_all_parallel(config)
            if write_cache:
                write_preview_files_safely(state, config)
            speak_async(compose_summary_speech(state, config), config)
        elif choice == "8":
            config.voice_enabled = not config.voice_enabled
            config.save(config_path)
            status = "enabled" if config.voice_enabled else "disabled"
            console.print(f"[bold green]Voice {status}.[/bold green]")
            if config.voice_enabled:
                speak_sync("Voice enabled.", config)
            time.sleep(0.2)
        elif choice == "9":
            console.print(f"VOICE_ENABLED={config.voice_enabled}")
            console.print(f"PIPER_MODEL={config.piper_model}")
            console.print(f"VOICE_LOCK={Path(config.preview_dir) / 'voice.lock'}")
            speak_sync("Voice test successful.", config)
            time.sleep(0.2)
        elif choice == "e":
            if not write_cache:
                console.print("[yellow]JSON export is disabled by --no-cache.[/yellow]")
            else:
                preview_dir = Path(config.preview_dir)
                json_path = preview_dir / "summary.json"
                export_json(state, json_path)
                console.print(f"[green]Exported JSON summary to[/green] {json_path}")
                time.sleep(0.8)
        elif choice == "0":
            speak_sync("Update complete.", config)
            console.print("[bold green]Update complete.[/bold green]")
            return 0


def parse_install_targets(raw: str) -> list[str]:
    value = raw.strip().lower()
    if value == "all":
        return list(PROVIDERS)
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CachyOS multi-source update checker and installer")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to JSON config file")
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--refresh-only", action="store_true", help="Check updates once and exit")
    action_group.add_argument("--json", action="store_true", help="Print JSON summary and exit")
    action_group.add_argument("--voice-test", action="store_true", help="Run voice test and exit")
    action_group.add_argument(
        "--update-advisory",
        metavar="HTTPS_URL",
        help="Download and install a versioned AUR advisory after SHA-256 authentication",
    )
    action_group.add_argument(
        "--install",
        type=str,
        help=(
            "Install updates with non-interactive package confirmation: all or comma-separated "
            "sources (pacman,aur,flatpak,snap); sudo must already be authorized"
        ),
    )
    parser.add_argument("--export-json", type=Path, help="Write JSON summary to a file")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice for this run")
    parser.add_argument("--timeout", type=int, help="Override command timeout in seconds (must be positive)")
    parser.add_argument("--no-cache", action="store_true", help="Do not write preview cache files or play voice")
    parser.add_argument(
        "--advisory-sha256",
        metavar="DIGEST",
        help="Trusted SHA-256 digest for --update-advisory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        return 2

    if args.no_voice:
        config.voice_enabled = False
    if args.no_cache:
        config.voice_enabled = False
    if args.timeout is not None:
        if args.timeout <= 0:
            console.print("[red]--timeout must be a positive integer.[/red]")
            return 2
        config.command_timeout = args.timeout

    configure_logging(Path(config.log_file))
    logging.info("Application started")

    if args.update_advisory:
        if not args.advisory_sha256:
            console.print("[red]--update-advisory requires --advisory-sha256.[/red]")
            return 2
        try:
            advisory = install_authenticated_advisory(
                args.update_advisory,
                args.advisory_sha256,
                Path(config.advisory_cache_file),
                timeout=config.command_timeout,
                current_advisory=load_effective_aur_advisory(config),
            )
        except AdvisoryUpdateError as exc:
            logging.error("Advisory update rejected: %s", exc)
            console.print(f"[red]Advisory update rejected:[/red] {exc}")
            return 2
        console.print(
            "[green]Authenticated AUR advisory installed:[/green] "
            f"version {advisory['schema_version']}, updated {advisory['updated_at']}"
        )
        return 0
    if args.advisory_sha256:
        console.print("[red]--advisory-sha256 requires --update-advisory.[/red]")
        return 2

    if args.voice_test:
        console.print(f"VOICE_ENABLED={config.voice_enabled}")
        console.print(f"PIPER_MODEL={config.piper_model}")
        console.print(f"VOICE_LOCK={Path(config.preview_dir) / 'voice.lock'}")
        speak_sync("Voice test successful.", config)
        return 0

    state = check_all_parallel(config, show_progress=not args.json)
    if not args.no_cache and not args.json:
        write_preview_files_safely(state, config)

    if args.export_json:
        export_json(state, args.export_json)

    if args.json:
        console.print_json(export_json(state))
        return check_exit_code(state)

    if args.install:
        targets = parse_install_targets(args.install)
        invalid = [item for item in targets if item not in PROVIDERS]
        if invalid:
            console.print(f"[red]Invalid install target(s):[/red] {', '.join(invalid)}")
            return 2
        rc = install_many(targets, state, config, noninteractive=True)
        refreshed = check_all_parallel(config)
        if not args.no_cache:
            write_preview_files_safely(refreshed, config)
        speak_sync(compose_summary_speech(refreshed, config), config)
        return rc or check_exit_code(refreshed)

    if args.refresh_only:
        render_dashboard(state, config)
        speak_sync(compose_summary_speech(state, config), config)
        return check_exit_code(state)

    speak_async(compose_summary_speech(state, config), config)
    return interactive_loop(state, config, args.config, write_cache=not args.no_cache)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted by user.[/bold yellow]")
        raise SystemExit(130)
