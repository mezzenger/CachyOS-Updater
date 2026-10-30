# CachyOS Update Center

CachyOS Update Center is a Rust application with a native GTK4/libadwaita interface for checking and
installing updates from Pacman and Flatpak.

## Features

- Checks Pacman and Flatpak in parallel and shows complete package lists.
- Installs all updates or selected sources in the background, with cancellation and logged output.
- Announces completion with a flash on the top status; after Alba finishes,
  the three workflow steps and status brighten together and fade back to normal.
- Supports per-source preferences, stale-result protection, and preflight checks.
- Uses Polkit for Pacman updates; Flatpak runs as the desktop user.
- Shows a green speech waveform that follows Piper volume and gently pulses during checks.
  Respects the desktop animation setting and hides when idle.
- Provides optional Piper announcements, desktop notifications, rotating logs,
  and privacy-safe diagnostic ZIP bundles.

## Quick Start

Requires Rust 1.85 or newer, GTK4, and libadwaita development libraries.
The application and fixed-command Polkit helper run entirely in Rust; Python
is retained in this checkout as a migration reference and regression suite.
The installers and Arch package now select the Rust binaries.

```sh
cargo run --release --locked --bin cuc-desktop
```

## Configuration

The optional configuration file is
`~/.config/cachyos-update-center/config.json`. Invalid configuration is
reported without overwriting the existing file.

```json
{
  "voice_enabled": true,
  "command_timeout": 120,
  "installation_timeout": 3600,
  "max_result_age_minutes": 15,
  "minimum_free_space_mb": 1024,
  "notifications_enabled": true,
  "enabled_sources": {
    "pacman": true,
    "flatpak": true
  }
}
```

Legacy `aur`, `snap`, and AUR advisory settings are ignored when an older
configuration is loaded and are dropped the next time settings are saved.

## Documentation

- [INSTALL.md](INSTALL.md): dependencies and installation.
- [HOWTO.md](HOWTO.md): common workflows.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): update and voice failures.
- [CONTRIBUTING.md](CONTRIBUTING.md): development conventions.
- [ACCESSIBILITY.md](ACCESSIBILITY.md): keyboard and accessibility details.

For read-only JSON output, run `cargo run --release --locked --bin cuc-desktop -- --check`.
The dashboard starts at a compact 476 × 480 and fits its size to the displayed
content. It grows for additional details and returns to the compact layout when
they clear; long content remains scrollable within the screen. Maximization is
remembered, while previous automatic growth does not become the next launch size.
When enabled in Settings, Piper speaks automatically after checks; the speaker
button repeats the status. Install icons open a confirmation, while list icons
show the complete package review with source checkboxes.

## Development Checks

```sh
cargo test --locked
cargo clippy --all-targets -- -D warnings
cargo fmt --check
python3 -m unittest -v test_cuc.py
python3 -m py_compile cuc_backend.py cuc_controller.py cuc_desktop.py
python3 -m py_compile cuc_diagnostics.py cuc_providers.py cuc_privileged.py
```

See [RUST-MIGRATION.md](RUST-MIGRATION.md) for module mapping, security details,
and validation limits. No existing installation is replaced merely by building.
