# Install Guide

This project targets CachyOS and other Arch-based systems.

## Dependencies

```sh
sudo pacman -S rust base-devel pacman-contrib gtk4 libadwaita polkit
```

Flatpak support is optional:

```sh
sudo pacman -S flatpak
```

Optional voice announcements require `piper-tts` and an installed Piper voice.

## Run or Install

Build both Rust executables as your desktop user:

```sh
cargo build --release --locked
./target/release/cuc-desktop
```

GTK4 and libadwaita are linked from the system libraries. Python and
python-gobject are not runtime dependencies. Python 3 is used only for the
legacy tests and an independent ZIP verification test.

Install for the current user (builds before switching the versioned launcher):

```sh
./scripts/install-user.sh
```

Remove that installation with `./scripts/uninstall-user.sh`.

## Polkit Helper

Pacman uses a fixed-command Polkit helper. Review and install it with:

```sh
sudo ./scripts/install-system-helper.sh
```

Reinstall the helper when migrating from Python: the Rust helper supports
cancellation through a control pipe, and the Python helper does not. The
installer uses the already built release binary and does not compile as root.
No installer runs automatically during a build or test.

The helper accepts only the `pacman` action. Flatpak runs as the desktop user.

## Arch Package

```sh
makepkg -si
```

The included `PKGBUILD` snapshots this local checkout rather than downloading
an unpublished release tag. It builds and installs the Rust desktop app, helper, launcher,
icons, and Polkit integration.
