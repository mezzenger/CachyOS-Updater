# Install Guide

This project is designed for CachyOS or another Arch-based system with Pacman.

## Required Runtime Packages

Install the base Python dependencies:

```sh
sudo pacman -S python python-cryptography pacman-contrib
```

For the desktop app, install GTK/libadwaita Python bindings:

```sh
sudo pacman -S python-gobject gtk4 libadwaita
```

## Optional Update Sources

Install only the sources you want to use:

```sh
sudo pacman -S flatpak
sudo pacman -S snapd
```

For AUR support, install one supported helper:

```sh
yay --version
paru --version
pikaur --version
```

The app checks helpers in this order when `aur_helper` is `auto`:

```text
yay, paru, pikaur
```

## Optional Voice Support

Voice announcements need Piper and at least one installed Piper voice:

```sh
sudo pacman -S piper-tts
```

The default voice path is:

```text
/usr/share/piper-voices/en/en_GB/alba/medium/en_GB-alba-medium.onnx
```

If that file is not installed, use the desktop app settings window to choose a
different voice, or edit `piper_model` in the configuration file.

## Desktop Launcher

Run the app directly during development:

```sh
./cuc_desktop.py
```

For a reproducible per-user installation, run:

```sh
./scripts/install-user.sh
```

This creates a versioned application directory plus a `current` symlink and
launcher under the standard XDG user data location. Remove it with:

```sh
./scripts/uninstall-user.sh
```

## Optional Polkit Helper

Pacman and Snap use the desktop's native Polkit authentication flow. Review
the allowlisted helper and policy first, then install them as root:

```sh
sudo ./scripts/install-system-helper.sh
```

The helper accepts only `pacman` and `snap` as fixed action names and runs
fixed, absolute command lines. AUR helpers continue to run as the desktop user
because AUR packages must never be built as root.

## File Locations

Default config:

```text
~/.config/cachyos-update-center/config.json
```

Default log:

```text
~/.local/state/cachyos-update-center/app.log
```

Default preview cache:

```text
~/.cache/update-check-preview/
```

## Arch Package

The repository includes a `PKGBUILD` that installs the desktop application,
shared backend modules, advisory and diagnostic support, the launcher, and the
Polkit helper and policy:

```sh
makepkg -si
```

Release builds expect a matching Git tag such as `v0.5.2`. Review the PKGBUILD
and package contents before installation.
