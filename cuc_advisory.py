"""Authenticated, versioned AUR advisory updates."""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


MAX_ADVISORY_BYTES = 2 * 1024 * 1024


class AdvisoryUpdateError(RuntimeError):
    """Raised when an advisory download cannot be authenticated or validated."""


def validate_advisory(advisory: Any) -> dict[str, Any]:
    if not isinstance(advisory, dict):
        raise AdvisoryUpdateError("The advisory must contain a JSON object.")
    if not isinstance(advisory.get("schema_version"), int) or advisory["schema_version"] < 1:
        raise AdvisoryUpdateError("The advisory schema_version must be a positive integer.")
    updated_at = advisory.get("updated_at")
    if not isinstance(updated_at, str):
        raise AdvisoryUpdateError("The advisory updated_at value is missing.")
    try:
        updated_date = date.fromisoformat(updated_at)
    except ValueError as exc:
        raise AdvisoryUpdateError("The advisory updated_at value must use YYYY-MM-DD.") from exc
    expires_at = advisory.get("expires_at")
    if expires_at is not None:
        if not isinstance(expires_at, str):
            raise AdvisoryUpdateError("The advisory expires_at value must use YYYY-MM-DD.")
        try:
            expiry_date = date.fromisoformat(expires_at)
        except ValueError as exc:
            raise AdvisoryUpdateError("The advisory expires_at value must use YYYY-MM-DD.") from exc
        if expiry_date < updated_date:
            raise AdvisoryUpdateError("The advisory expires before it was updated.")
    if not isinstance(advisory.get("incident_active"), bool):
        raise AdvisoryUpdateError("The advisory incident_active value must be true or false.")
    advisory_url = advisory.get("advisory_url")
    if not isinstance(advisory_url, str) or urllib.parse.urlparse(advisory_url).scheme != "https":
        raise AdvisoryUpdateError("The advisory_url must be an HTTPS URL.")
    affected = advisory.get("affected_packages")
    if not isinstance(affected, list):
        raise AdvisoryUpdateError("The advisory affected_packages value must be a list.")
    seen: set[str] = set()
    for item in affected:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise AdvisoryUpdateError("Every affected package must have a string name.")
        if item["name"] in seen:
            raise AdvisoryUpdateError(f"Duplicate affected package: {item['name']}.")
        seen.add(item["name"])
        source = item.get("source")
        if not isinstance(source, str) or urllib.parse.urlparse(source).scheme != "https":
            raise AdvisoryUpdateError(
                f"Affected package {item['name']} must have an HTTPS source."
            )
    return advisory


def advisory_is_expired(advisory: Mapping[str, Any], *, today: date | None = None) -> bool:
    expires_at = advisory.get("expires_at")
    return bool(expires_at) and date.fromisoformat(str(expires_at)) < (today or date.today())


def _validate_digest(expected_sha256: str) -> str:
    normalized = expected_sha256.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise AdvisoryUpdateError("The expected SHA-256 digest must contain 64 hexadecimal characters.")
    return normalized


def _read_limited(response: Any) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_ADVISORY_BYTES:
        raise AdvisoryUpdateError("The advisory exceeds the 2 MiB size limit.")
    payload = response.read(MAX_ADVISORY_BYTES + 1)
    if len(payload) > MAX_ADVISORY_BYTES:
        raise AdvisoryUpdateError("The advisory exceeds the 2 MiB size limit.")
    return payload


def download_authenticated_advisory(
    url: str,
    expected_sha256: str,
    *,
    timeout: int,
) -> tuple[bytes, dict[str, Any]]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise AdvisoryUpdateError("Advisory updates require an HTTPS URL.")
    expected = _validate_digest(expected_sha256)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "cachyos-update-center-advisory-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            if urllib.parse.urlparse(final_url).scheme != "https":
                raise AdvisoryUpdateError("The advisory download redirected away from HTTPS.")
            payload = _read_limited(response)
    except AdvisoryUpdateError:
        raise
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise AdvisoryUpdateError(f"Could not download the advisory: {exc}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise AdvisoryUpdateError(
            f"Advisory digest mismatch: expected {expected}, received {actual}."
        )
    try:
        advisory = validate_advisory(json.loads(payload.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AdvisoryUpdateError(f"The advisory is not valid UTF-8 JSON: {exc}") from exc
    return payload, advisory


def download_signed_advisory(
    url: str,
    signature_url: str,
    public_key_file: Path,
    *,
    timeout: int,
) -> tuple[bytes, dict[str, Any]]:
    """Download and verify an advisory using a detached Ed25519 signature."""
    for candidate in (url, signature_url):
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme != "https" or not parsed.hostname:
            raise AdvisoryUpdateError("Signed advisory URLs must use HTTPS.")
    try:
        public_key = serialization.load_pem_public_key(public_key_file.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise AdvisoryUpdateError(f"Could not load the advisory public key: {exc}") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise AdvisoryUpdateError("The advisory public key must be Ed25519.")

    def fetch(candidate: str, maximum: int) -> bytes:
        request = urllib.request.Request(
            candidate,
            headers={"User-Agent": "cachyos-update-center-signed-advisory"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final_url = getattr(response, "geturl", lambda: candidate)()
                if urllib.parse.urlparse(final_url).scheme != "https":
                    raise AdvisoryUpdateError("The advisory download redirected away from HTTPS.")
                payload = response.read(maximum + 1)
        except AdvisoryUpdateError:
            raise
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise AdvisoryUpdateError(f"Could not download signed advisory data: {exc}") from exc
        if len(payload) > maximum:
            raise AdvisoryUpdateError("Signed advisory data exceeds its size limit.")
        return payload

    payload = fetch(url, MAX_ADVISORY_BYTES)
    encoded_signature = fetch(signature_url, 4096).strip()
    try:
        signature = base64.b64decode(encoded_signature, validate=True)
        public_key.verify(signature, payload)
    except (ValueError, InvalidSignature) as exc:
        raise AdvisoryUpdateError("The advisory signature is invalid.") from exc
    try:
        advisory = validate_advisory(json.loads(payload.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AdvisoryUpdateError(f"The advisory is not valid UTF-8 JSON: {exc}") from exc
    if advisory_is_expired(advisory):
        raise AdvisoryUpdateError("The signed advisory has expired.")
    return payload, advisory


def install_signed_advisory(
    url: str,
    signature_url: str,
    public_key_file: Path,
    destination: Path,
    *,
    timeout: int,
    current_advisory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload, advisory = download_signed_advisory(
        url,
        signature_url,
        public_key_file,
        timeout=timeout,
    )
    digest = hashlib.sha256(payload).hexdigest()
    return _install_verified_payload(
        payload,
        advisory,
        digest,
        destination,
        current_advisory=current_advisory,
    )


def install_authenticated_advisory(
    url: str,
    expected_sha256: str,
    destination: Path,
    *,
    timeout: int,
    current_advisory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload, advisory = download_authenticated_advisory(
        url,
        expected_sha256,
        timeout=timeout,
    )
    return _install_verified_payload(
        payload,
        advisory,
        _validate_digest(expected_sha256),
        destination,
        current_advisory=current_advisory,
    )


def _install_verified_payload(
    payload: bytes,
    advisory: dict[str, Any],
    digest: str,
    destination: Path,
    *,
    current_advisory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if current_advisory:
        current = validate_advisory(dict(current_advisory))
        if advisory["schema_version"] < current["schema_version"]:
            raise AdvisoryUpdateError(
                "The downloaded advisory uses an older schema version than the current advisory."
            )
        if date.fromisoformat(advisory["updated_at"]) < date.fromisoformat(current["updated_at"]):
            raise AdvisoryUpdateError("The downloaded advisory is older than the current advisory.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=destination.name + ".",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        temporary.replace(destination)
        digest_path = destination.with_suffix(destination.suffix + ".sha256")
        digest_path.write_text(_validate_digest(digest) + "\n", encoding="ascii")
        digest_path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return advisory


def load_authenticated_cache(destination: Path) -> dict[str, Any] | None:
    digest_path = destination.with_suffix(destination.suffix + ".sha256")
    if not destination.is_file() or not digest_path.is_file():
        return None
    try:
        payload = destination.read_bytes()
        expected = _validate_digest(digest_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, AdvisoryUpdateError):
        return None
    if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected):
        return None
    try:
        return validate_advisory(json.loads(payload.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError, AdvisoryUpdateError):
        return None
