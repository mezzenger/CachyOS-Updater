"""Privacy-safe diagnostic bundle creation."""

from __future__ import annotations

import json
import platform
import re
import shutil
import sys
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|access[_-]?token|api[_-]?key|secret|authorization|cookie)"
    r"\s*[:=]\s*\S+"
)
BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+\S+")
EMAIL_ADDRESS = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def sanitize_diagnostic_text(text: str, *, home: Path | None = None) -> str:
    sanitized = text
    home_path = home or Path.home()
    if str(home_path):
        sanitized = sanitized.replace(str(home_path), "$HOME")
    sanitized = SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", sanitized)
    sanitized = BEARER_TOKEN.sub("Bearer <redacted>", sanitized)
    sanitized = EMAIL_ADDRESS.sub("<redacted-email>", sanitized)
    return sanitized


def _safe_config(config: Any) -> dict[str, Any]:
    payload = (
        asdict(config)
        if is_dataclass(config) and not isinstance(config, type)
        else dict(config)
    )
    payload["spoken_name"] = "<redacted>"
    for field_name in (
        "piper_model",
        "preview_dir",
        "log_file",
        "advisory_cache_file",
    ):
        if field_name in payload:
            payload[field_name] = sanitize_diagnostic_text(str(payload[field_name]))
    if payload.get("advisory_public_key_file"):
        payload["advisory_public_key_file"] = "<redacted-path>"
    for field_name in ("advisory_feed_url", "advisory_signature_url"):
        if payload.get(field_name):
            payload[field_name] = "<redacted-url>"
    return payload


def _safe_state(state: Any | None) -> dict[str, Any]:
    if state is None:
        return {}
    results: dict[str, Any] = {}
    for source, result in state.results.items():
        results[source] = {
            "enabled": result.enabled,
            "available_on_system": result.available_on_system,
            "available": result.available,
            "count": result.count,
            "error": sanitize_diagnostic_text(result.error or "") or None,
            "install_allowed": result.install_allowed,
            "security_status": result.security_status,
            "security_warning_count": len(result.security_warnings),
            "checked_at": result.checked_at,
        }
    return results


def export_diagnostics(
    destination: Path,
    config: Any,
    *,
    state: Any | None = None,
    log_tail_bytes: int = 256 * 1024,
    app_version: str = "unknown",
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    system = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "application_version": app_version,
        "commands": {
            command: shutil.which(command) is not None
            for command in ("pacman", "checkupdates", "yay", "paru", "pikaur", "flatpak", "snap", "pkexec")
        },
    }
    log_text = ""
    log_path = Path(config.log_file)
    try:
        with log_path.open("rb") as log_file:
            log_file.seek(0, 2)
            size = log_file.tell()
            log_file.seek(max(0, size - log_tail_bytes))
            log_text = log_file.read().decode("utf-8", errors="replace")
    except OSError as exc:
        log_text = f"Log unavailable: {exc}\n"
    log_text = sanitize_diagnostic_text(log_text)
    session_text = ""
    session_path = log_path.with_name("last-session.json")
    try:
        session_text = sanitize_diagnostic_text(session_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        session_text = f"Session state unavailable: {exc}\n"

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("system.json", json.dumps(system, indent=2) + "\n")
        archive.writestr("config.json", json.dumps(_safe_config(config), indent=2) + "\n")
        archive.writestr("state.json", json.dumps(_safe_state(state), indent=2) + "\n")
        archive.writestr("app.log", log_text)
        archive.writestr("last-session.json", session_text)
        archive.writestr(
            "README.txt",
            "CUC diagnostic bundle. Credentials, email addresses, the home path, "
            "spoken name, and package item lists are intentionally omitted or redacted.\n",
        )
    return destination
