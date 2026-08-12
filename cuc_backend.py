"""CachyOS Update Center shared application backend.

This module contains configuration, provider, security, update-planning,
logging, diagnostics, and voice support for the GTK desktop application.
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
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
    install_signed_advisory,
    load_authenticated_cache,
    validate_advisory,
)
from cuc_controller import ExecutionOutcome, UpdateController, UpdateExecutor
from cuc_diagnostics import export_diagnostics as export_diagnostics
from cuc_providers import ProviderRegistry, UpdateProvider

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
VOICE_TIMEOUT = 45
PLAYER_TIMEOUT = 60
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
        return PolkitReadiness(False, "pkexec is not installed; update system packages manually.")
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
    show_progress: bool = False,
    sources: Iterable[str] | None = None,
    prior_state: AppState | None = None,
) -> AppState:
    """Compatibility wrapper for non-GTK callers of the shared controller."""
    del show_progress
    requested_sources = list(sources) if sources is not None else list(PROVIDERS)
    results = UPDATE_CONTROLLER.check(
        config,
        sources=requested_sources,
        prior_results=prior_state.results if prior_state else None,
    )
    return AppState(results)


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
