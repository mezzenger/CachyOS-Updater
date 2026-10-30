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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from cuc_controller import ExecutionOutcome, UpdateController, UpdateExecutor
from cuc_diagnostics import export_diagnostics as export_diagnostics
from cuc_providers import ProviderRegistry, UpdateProvider

APP_NAME = "cachyos-update-center"
__version__ = "0.6.0"
ALL_CURRENT_STATUS = "Annoyingly current"
DEFAULT_PIPER_MODEL = "/usr/share/piper-voices/en/en_GB/alba/medium/en_GB-alba-medium.onnx"
DEFAULT_PREVIEW_DIR = Path.home() / ".cache" / "update-check-preview"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / APP_NAME / "config.json"
DEFAULT_LOG_PATH = Path.home() / ".local" / "state" / APP_NAME / "app.log"
DEFAULT_WINDOW_STATE_PATH = Path(
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
) / APP_NAME / "window-state.json"
DEFAULT_TIMEOUT = 120
VOICE_TIMEOUT = 45
PLAYER_TIMEOUT = 60
UPDATE_SOURCES = ("pacman", "flatpak")
LEGACY_UPDATE_SOURCES = {"aur", "snap"}
SOURCE_DISPLAY_NAMES = {
    "pacman": "Pacman",
    "flatpak": "Flatpak",
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
    notifications_enabled: bool = True
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
        unknown_sources = set(enabled_sources) - set(UPDATE_SOURCES) - LEGACY_UPDATE_SOURCES
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
            notifications_enabled=boolean("notifications_enabled", True),
            enabled_sources=normalized_sources,
        )

    def save(self, path: Path) -> None:
        # Never silently replace a configuration the application cannot read.
        # This leaves the original file available for manual recovery.
        if path.exists():
            type(self).load(path)
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


def format_update_list(results: Iterable[UpdateResult]) -> str:
    """Format every pending item for display or clipboard export without truncation."""
    sections: list[str] = []
    for result in results:
        noun = "update" if result.count == 1 else "updates"
        items = "\n".join(result.items) or "Package details unavailable"
        sections.append(f"{result.display_name} — {result.count} {noun}\n{items}")
    return "\n\n".join(sections)


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
    reboot_recommended: bool = False

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


def output_recommends_reboot(text: str) -> bool:
    """Return whether update output contains an affirmative reboot recommendation."""
    normalized = " ".join(text.casefold().split())
    if "no reboot recommended" in normalized or "reboot is not recommended" in normalized:
        return False
    return "reboot recommended" in normalized or "reboot is recommended" in normalized


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
    return PolkitReadiness(True, "Native Polkit authentication is ready for Pacman.")


def _running_package_managers(proc_root: Path = Path("/proc")) -> list[RunningPackageManager]:
    manager_names = {"pacman", "flatpak"}
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
    if "pacman" in selected:
        if pacman_lock.exists():
            issues.append(
                PreflightIssue(
                    "pacman-lock",
                    f"Pacman is locked by another operation: {pacman_lock}.",
                    source="pacman",
                )
            )
    conflicting_names: set[str] = set()
    if "pacman" in selected:
        conflicting_names.add("pacman")
    if "flatpak" in selected:
        conflicting_names.add("flatpak")
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


def parse_update_lines(output: str, *, preserve_spacing: bool = False) -> list[str]:
    return [
        line.rstrip() if preserve_spacing else line.strip()
        for line in output.splitlines()
        if line.strip()
    ]


def build_update_command(source: str, config: Config, *, noninteractive: bool = False) -> list[str] | None:
    if source == "pacman":
        command = ["sudo", "-n", "pacman", "-Syu"] if noninteractive else ["sudo", "pacman", "-Syu"]
        return [*command, "--noconfirm"] if noninteractive else command
    if source == "flatpak":
        return ["flatpak", "update", "-y"] if noninteractive else ["flatpak", "update"]
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
    "flatpak": check_flatpak_updates,
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


def compose_summary_speech(
    state: AppState,
    config: Config,
    *,
    reboot_recommended: bool = False,
) -> str:
    failed = state.failed_results()
    if failed:
        return f"Update check incomplete. Could not check {', '.join(result.display_name for result in failed)}."
    if not state.any_updates():
        if reboot_recommended:
            return (
                f"{config.spoken_name}.. Your system is up to date. "
                "A reboot is recommended."
            )
        message = (
            f"{config.spoken_name}.. Your system is currently up to date "
            "and is operating nominally!"
        )
        return message

    parts = []
    for result in state.results.values():
        if result.available:
            parts.append(f"{result.display_name} has {result.count} updates.")
    return " ".join(parts)
