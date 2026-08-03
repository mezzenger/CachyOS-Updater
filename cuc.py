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
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

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
DEFAULT_PIPER_MODEL = "/usr/share/piper-voices/en/en_GB/alba/medium/en_GB-alba-medium.onnx"
DEFAULT_PREVIEW_DIR = Path.home() / ".cache" / "update-check-preview"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / APP_NAME / "config.json"
DEFAULT_LOG_PATH = Path.home() / ".local" / "state" / APP_NAME / "app.log"
DEFAULT_TIMEOUT = 120
MAX_DETAIL_ROWS = 18
VOICE_TIMEOUT = 45
PLAYER_TIMEOUT = 60
CHECK_FAILURE_EXIT = 1
SECURITY_BLOCK_EXIT = 3
SUPPORTED_AUR_HELPERS = ("yay", "paru", "pikaur")
AUR_ADVISORY_PATH = Path(__file__).with_name("data") / "aur-security-advisories.json"
AUR_RPC_URL = "https://aur.archlinux.org/rpc/v5/info"
AUR_RPC_BATCH_SIZE = 50
AUR_AUTH_OPTIONS = {
    "yay": ["--sudoloop", "--sudoflags", "-A"],
    "paru": ["--sudoloop", "--sudoflags", "-A"],
    "pikaur": ["--sudoloop", "--sudoflags", "-A"],
}

console = Console()
voice_thread_lock = threading.Lock()


class ConfigError(ValueError):
    """Raised when a configuration file cannot be safely used."""


@dataclass(slots=True)
class Config:
    voice_enabled: bool = True
    piper_model: str = DEFAULT_PIPER_MODEL
    preview_dir: str = str(DEFAULT_PREVIEW_DIR)
    log_file: str = str(DEFAULT_LOG_PATH)
    command_timeout: int = DEFAULT_TIMEOUT
    spoken_name: str = "Cozmik"
    aur_helper: str = "auto"

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
        command_timeout = data.get("command_timeout", DEFAULT_TIMEOUT)
        if isinstance(command_timeout, bool) or not isinstance(command_timeout, int) or command_timeout <= 0:
            raise ConfigError("Configuration field 'command_timeout' must be a positive integer.")
        aur_helper = string("aur_helper", "auto").lower()
        if aur_helper not in ("auto", *SUPPORTED_AUR_HELPERS):
            choices = ", ".join(("auto", *SUPPORTED_AUR_HELPERS))
            raise ConfigError(f"Configuration field 'aur_helper' must be one of: {choices}.")

        return cls(
            voice_enabled=voice_enabled,
            piper_model=string("piper_model", DEFAULT_PIPER_MODEL),
            preview_dir=string("preview_dir", str(DEFAULT_PREVIEW_DIR)),
            log_file=string("log_file", str(DEFAULT_LOG_PATH)),
            command_timeout=command_timeout,
            spoken_name=string("spoken_name", "Cozmik"),
            aur_helper=aur_helper,
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
    available: bool = False
    count: int = 0
    items: list[str] = field(default_factory=list)
    error: str | None = None
    install_allowed: bool = True
    security_status: str = "not-applicable"
    security_warnings: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class AppState:
    results: dict[str, UpdateResult] = field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        return {name: result.count for name, result in self.results.items()}

    def any_updates(self) -> bool:
        return any(result.available for result in self.results.values())

    def failed_results(self) -> list[UpdateResult]:
        return [result for result in self.results.values() if result.available_on_system and result.error]

    def unavailable_results(self) -> list[UpdateResult]:
        return [result for result in self.results.values() if not result.available_on_system]

    def is_healthy(self) -> bool:
        return not self.failed_results()

    def installable_sources(self) -> list[str]:
        return [
            source
            for source, result in self.results.items()
            if result.available and result.install_allowed and not result.error
        ]


@dataclass(slots=True)
class AurSecurityReport:
    package_names: list[str]
    compromised: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    incident_active: bool = False
    advisory_url: str = ""
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
    AUTHENTICATING = "authenticating"
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

    def begin_authentication(self, commands: list[list[str]]) -> None:
        if self.phase != SessionPhase.REVIEWING:
            raise RuntimeError(f"cannot authenticate from session phase {self.phase.value}")
        self.phase = SessionPhase.AUTHENTICATING
        self.commands = [list(command) for command in commands]

    def begin_install(self) -> None:
        if self.phase not in (SessionPhase.REVIEWING, SessionPhase.AUTHENTICATING):
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


def configure_logging(log_path: Path) -> None:
    format_string = "%(asctime)s %(levelname)s %(message)s"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=log_path, level=logging.INFO, format=format_string, force=True)
    except OSError as exc:
        logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=format_string, force=True)
        logging.warning("Cannot open log file %s; using stderr instead: %s", log_path, exc)


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def update_command_needs_authentication(command: list[str]) -> bool:
    """Return whether an update command may need the administrator password."""
    return bool(command) and (command[0] == "sudo" or command[0] in SUPPORTED_AUR_HELPERS)


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


def run_interactive(cmd: list[str]) -> int:
    logging.info("Starting interactive command: %s", " ".join(cmd))
    try:
        return subprocess.call(cmd)
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
    if not isinstance(advisory, dict) or not isinstance(advisory.get("affected_packages"), list):
        raise RuntimeError("bundled AUR advisory data has an invalid format")
    return advisory


def fetch_aur_metadata(package_names: list[str], *, timeout: int) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for start in range(0, len(package_names), AUR_RPC_BATCH_SIZE):
        batch = package_names[start : start + AUR_RPC_BATCH_SIZE]
        query = urllib.parse.urlencode([("arg[]", name) for name in batch])
        request = urllib.request.Request(
            f"{AUR_RPC_URL}?{query}",
            headers={"User-Agent": f"{APP_NAME}/1"},
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
        error=error,
    )


def audit_aur_updates(items: list[str], config: Config) -> AurSecurityReport:
    package_names = list(dict.fromkeys(filter(None, (aur_package_name(item) for item in items))))
    try:
        advisory = load_aur_advisory()
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
    except Exception as exc:
        result.error = str(exc)
    return result


def build_update_command(source: str, config: Config, *, noninteractive: bool = False) -> list[str] | None:
    if source == "pacman":
        command = ["sudo", "pacman", "-Syu"]
        return [*command, "--noconfirm"] if noninteractive else command
    if source == "aur":
        helper = find_aur_helper(config)
        if not helper:
            return None
        command = [helper, "-Sua"]
        if noninteractive:
            command.extend(["--noconfirm", *AUR_AUTH_OPTIONS.get(helper, [])])
        return command
    if source == "flatpak":
        return ["flatpak", "update", "-y"] if noninteractive else ["flatpak", "update"]
    if source == "snap":
        return ["sudo", "snap", "refresh"]
    return None


def build_update_commands(
    targets: Iterable[str],
    config: Config,
    *,
    noninteractive: bool = False,
) -> list[list[str]]:
    return [
        command
        for source in targets
        if (command := build_update_command(source, config, noninteractive=noninteractive)) is not None
    ]


CHECKERS = {
    "pacman": check_pacman_updates,
    "aur": check_aur_updates,
    "flatpak": check_flatpak_updates,
    "snap": check_snap_updates,
}


def check_all_parallel(config: Config, *, show_progress: bool = True) -> AppState:
    state = AppState()
    tasks: dict[str, int] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
        disable=not show_progress,
    ) as progress:
        with ThreadPoolExecutor(max_workers=len(CHECKERS)) as executor:
            futures = {}
            for source, checker in CHECKERS.items():
                tasks[source] = progress.add_task(f"Checking {source}…", total=None)
                futures[executor.submit(checker, config)] = source

            for future in as_completed(futures):
                source = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # defensive catch
                    result = UpdateResult(
                        source=source,
                        display_name=source.capitalize(),
                        command_hint="",
                        error=str(exc),
                    )
                state.results[source] = result
                progress.update(tasks[source], description=f"[green]Checked {source}")

    # Preserve display order.
    state.results = {name: state.results[name] for name in CHECKERS if name in state.results}
    return state


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

    for source in CHECKERS:
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


def install_source(source: str, state: AppState, config: Config) -> int:
    result = state.results.get(source)
    if result is None:
        console.print(f"[red]Unknown source:[/red] {source}")
        return 1
    if not result.available_on_system:
        console.print(f"[red]Cannot install {source} updates:[/red] {result.error or 'source is not installed.'}")
        return CHECK_FAILURE_EXIT
    if result.error:
        console.print(f"[red]Cannot install {source} updates because its check failed:[/red] {result.error}")
        return CHECK_FAILURE_EXIT
    if not result.available:
        console.print(f"[yellow]No {source} updates to install.[/yellow]")
        return 0
    if not result.install_allowed:
        console.print(f"[bold red]Security check blocked {source} updates.[/bold red]")
        for warning in result.security_warnings:
            console.print(f"[red]•[/red] {warning}")
        return SECURITY_BLOCK_EXIT

    command = build_update_command(source, config)
    if command is None:
        console.print(f"[red]No usable update command was found for {source}.[/red]")
        return CHECK_FAILURE_EXIT
    speak_async(f"Installing {source} updates.", config)
    console.print(Rule(f"Installing {source}", style="cyan"))
    return run_interactive(command)


def install_many(sources: list[str], state: AppState, config: Config) -> int:
    exit_code = 0
    for source in sources:
        code = install_source(source, state, config)
        if code != 0:
            exit_code = code
    return exit_code


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
    invalid = [item for item in selected if item not in CHECKERS]
    if invalid:
        console.print(f"[yellow]Ignoring unknown source(s):[/yellow] {', '.join(invalid)}")
    return list(dict.fromkeys(item for item in selected if item in CHECKERS))


def interactive_loop(state: AppState, config: Config, config_path: Path, *, write_cache: bool) -> int:
    while True:
        render_dashboard(state, config)
        console.print(Panel(MENU_TEXT, border_style="blue", title="Control Deck"))
        choice = Prompt.ask("Choose an action", choices=[str(i) for i in range(10)] + ["e"], default="0")

        if choice == "1":
            install_many(list(CHECKERS), state, config)
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
        return list(CHECKERS)
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CachyOS multi-source update checker and installer")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to JSON config file")
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--refresh-only", action="store_true", help="Check updates once and exit")
    action_group.add_argument("--json", action="store_true", help="Print JSON summary and exit")
    action_group.add_argument("--voice-test", action="store_true", help="Run voice test and exit")
    action_group.add_argument(
        "--install",
        type=str,
        help="Install updates non-interactively: all or comma-separated sources (pacman,aur,flatpak,snap)",
    )
    parser.add_argument("--export-json", type=Path, help="Write JSON summary to a file")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice for this run")
    parser.add_argument("--timeout", type=int, help="Override command timeout in seconds (must be positive)")
    parser.add_argument("--no-cache", action="store_true", help="Do not write preview cache files or play voice")
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
        invalid = [item for item in targets if item not in CHECKERS]
        if invalid:
            console.print(f"[red]Invalid install target(s):[/red] {', '.join(invalid)}")
            return 2
        rc = install_many(targets, state, config)
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
