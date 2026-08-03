# CachyOS Update Center

CachyOS Update Center is a small Python updater for checking and installing
updates from Pacman, the AUR, Flatpak, and Snap.

The project has two front ends:

- `cuc.py`: terminal dashboard, JSON output, and scripted installs.
- `cuc_desktop.py`: native GTK/libadwaita dashboard for GNOME-style desktops.

## Features

- Checks Pacman, AUR, Flatpak, and Snap in parallel.
- Shows pending package lists by source.
- Installs all updates or selected update sources.
- Supports `yay`, `paru`, and `pikaur` for AUR checks.
- Audits pending AUR packages against a source-attributed incident list and live
  AUR maintainer metadata before enabling installation.
- Writes preview files and JSON summaries for scripts.
- Optional Piper voice announcements.
- GTK app provides a check, review, and install workflow with cancellable,
  streamed update output.

## Quick Start

Run a one-time terminal check:

```sh
./cuc.py --refresh-only
```

Run the interactive terminal dashboard:

```sh
./cuc.py
```

Run the desktop dashboard:

```sh
./cuc_desktop.py
```

Install every available source from the terminal:

```sh
./cuc.py --install all
```

Print machine-readable JSON:

```sh
./cuc.py --json
```

## Documentation

- [INSTALL.md](INSTALL.md): dependencies, desktop launcher setup, and install notes.
- [HOWTO.md](HOWTO.md): common user workflows.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): authentication, missing tools, voice, and update failures.
- [CONTRIBUTING.md](CONTRIBUTING.md): development commands and project conventions.

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
  "spoken_name": "Cozmik",
  "aur_helper": "auto"
}
```

Set `aur_helper` to `auto`, `yay`, `paru`, or `pikaur`. With `auto`, the first
available supported helper is selected.

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
python3 -m py_compile cuc.py
python3 -m py_compile cuc_desktop.py
python3 -m py_compile cuc_privileged.py
```
