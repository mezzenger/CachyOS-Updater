# CachyOS Update Center

CachyOS Update Center is a native GTK/libadwaita application for checking and
installing updates from Pacman, the AUR, Flatpak, and Snap.

## Features

- Checks Pacman, AUR, Flatpak, and Snap in parallel.
- Shows pending package lists by source.
- Installs all updates or selected update sources.
- Supports `yay`, `paru`, and `pikaur` for AUR checks.
- Audits pending AUR packages against a source-attributed incident list and live
  AUR maintainer metadata before enabling installation.
- Optional Piper voice announcements.
- GTK app can install all eligible updates directly or show the full package
  list for optional review, with cancellable, streamed update output.
- Supports per-source preferences, source selection during review, stale-result
  protection, and preflight checks for locks, disk space, conflicting package
  managers, connectivity failures, and Snap readiness.
- Preserves successful check results when retrying failed providers and reports
  per-source installation outcomes.
- Uses native desktop notifications and structured desktop update logging.
- Uses Polkit for desktop system updates and delegates AUR authentication and
  PKGBUILD review to a real terminal; the desktop never handles passwords.
- Routes update sources through a shared controller and provider registry.
- Supports authenticated, rollback-protected AUR advisory updates.
- Supports automatic Ed25519-signed advisory refresh with expiry enforcement.
- Rotates logs and exports privacy-safe diagnostic ZIP bundles.
- Provides documented keyboard shortcuts and accessible names for icon controls.

## Quick Start

Run the desktop dashboard:

```sh
./cuc_desktop.py
```

Use `Update all`, `Review updates`, and the per-source actions to install
updates. AUR authentication and PKGBUILD review still open in a real terminal
so the desktop process never handles passwords.

## Documentation

- [CHANGELOG.md](CHANGELOG.md): release history and user-visible changes.
- [CONTRIBUTORS.md](CONTRIBUTORS.md): project and engineering credits.
- [INSTALL.md](INSTALL.md): dependencies, desktop launcher setup, and install notes.
- [HOWTO.md](HOWTO.md): common user workflows.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): authentication, missing tools, voice, and update failures.
- [CONTRIBUTING.md](CONTRIBUTING.md): development commands and project conventions.
- [ACCESSIBILITY.md](ACCESSIBILITY.md): keyboard shortcuts and accessibility review.

## Configuration

The optional configuration file is:

```text
~/.config/cachyos-update-center/config.json
```

It is only written when settings are changed interactively. Invalid
configuration is reported without overwriting the existing file.

Common settings:

```json
{
  "voice_enabled": true,
  "piper_model": "/usr/share/piper-voices/en/en_GB/alba/medium/en_GB-alba-medium.onnx",
  "preview_dir": "/home/alvin/.cache/update-check-preview",
  "log_file": "/home/alvin/.local/state/cachyos-update-center/app.log",
  "command_timeout": 120,
  "installation_timeout": 3600,
  "max_result_age_minutes": 15,
  "minimum_free_space_mb": 1024,
  "spoken_name": "Cozmik",
  "aur_helper": "auto",
  "notifications_enabled": true,
  "advisory_cache_file": "/home/alvin/.cache/cachyos-update-center/aur-security-advisories.json",
  "advisory_feed_url": "",
  "advisory_signature_url": "",
  "advisory_public_key_file": "",
  "advisory_refresh_hours": 24,
  "enabled_sources": {
    "pacman": true,
    "aur": true,
    "flatpak": true,
    "snap": true
  }
}
```

Set `aur_helper` to `auto`, `yay`, `paru`, or `pikaur`. With `auto`, the first
available supported helper is selected.

The desktop settings window exposes these values and reports whether the
optional native Polkit integration is ready. Disabled sources remain visible
on the dashboard but are not contacted.

When all three signed-advisory settings are configured, CUC refreshes the feed
at the configured interval, verifies its detached base64 Ed25519 signature,
rejects rollbacks, and blocks AUR installation after advisory expiry.
Settings requires the two feed locations to use HTTPS and the public key to use
an absolute file path. Leave all three fields blank unless they were supplied
by a trusted advisory publisher.

The desktop remembers its last normal window size and maximized state in the
XDG state directory. Damaged or out-of-range window state is ignored safely.

## Architecture

- `cuc_backend.py`: shared models, configuration, provider implementations,
  security checks, logging, and voice support.
- `cuc_providers.py`: common provider contract and ordered registry.
- `cuc_controller.py`: frontend-independent parallel checks, retries, merging,
  and command planning.
- `cuc_advisory.py`: authenticated advisory validation and atomic cache updates.
- `cuc_diagnostics.py`: redacted diagnostic bundle creation.

## Authenticated Advisory Updates

Configure automatic Ed25519 advisory updates in the desktop settings. CUC
requires HTTPS, validates the schema and source URLs, verifies exact feed bytes,
rejects version/date rollbacks, and installs the cache atomically. An invalid
cache is ignored in favor of still-valid cached or bundled advisory data.

## AUR Safety

Arch Linux announced an active malicious-package incident in June 2026. CUC
checks every pending AUR package against:

- The source-attributed confirmed package list in
  `data/aur-security-advisories.json`.
- Current AUR RPC metadata, including whether the package is orphaned.

Confirmed, orphaned, missing, or unverifiable packages are blocked from CUC's
install actions. A clear result is not an endorsement: review PKGBUILD and
install-script diffs before accepting any AUR update. The bundled list is a
backstop and cannot detect every malicious package.

## Development Checks

```sh
python3 -m unittest -v test_cuc.py
python3 -m py_compile cuc_backend.py
python3 -m py_compile cuc_advisory.py
python3 -m py_compile cuc_controller.py
python3 -m py_compile cuc_desktop.py
python3 -m py_compile cuc_diagnostics.py
python3 -m py_compile cuc_providers.py
python3 -m py_compile cuc_privileged.py
python3 -m py_compile scripts/sign-advisory.py
```
