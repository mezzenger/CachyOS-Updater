# How To Use CachyOS Update Center

## Check for Updates in the Terminal

Run:

```sh
./cuc.py --refresh-only
```

This checks Pacman, AUR, Flatpak, and Snap, then exits.

## Use the Interactive Terminal Dashboard

Run:

```sh
./cuc.py
```

The dashboard shows a summary table and package details. Use the numbered menu
to install all updates, install one source, refresh checks, export JSON, or quit.

## Use the Desktop App

Run:

```sh
./cuc_desktop.py
```

The app follows three steps:

1. Check Pacman, AUR, Flatpak, and Snap.
2. Review package lists and any AUR safety findings.
3. Confirm the selected sources and install.

Each source card shows the status, update count, and a short package preview. If
more packages are available, open the extended package list to see every item.

Use the gear button to change settings. Use the speaker button to repeat the
current status with Piper voice output.

## Install Updates

Install all pending sources:

```sh
./cuc.py --install all
```

Install selected sources:

```sh
./cuc.py --install pacman,aur
./cuc.py --install flatpak
./cuc.py --install snap
```

In the desktop app, use `Review updates` or a per-source review button. The
review dialog is shown before authentication or installation. The output window
streams each command and offers `Cancel update`; cancellation sends SIGINT so
the package manager can stop cleanly.

If the optional Polkit helper is installed, Pacman-only and Snap-only desktop
updates use the system authentication agent. Other privileged flows use the
in-app password dialog and pass credentials to sudo over an anonymous pipe.

## Review AUR Safety Findings

CUC blocks an AUR install when a pending package:

- Appears in the bundled confirmed-incident list.
- Is currently orphaned.
- Is missing from the AUR metadata response.
- Cannot be verified because the safety request failed.

The AUR source card and final review dialog explain the reason. Do not bypass a
block without manually reviewing the package repository, PKGBUILD history, and
install-script diff.

## Export JSON

Print JSON to standard output:

```sh
./cuc.py --json
```

Write JSON to a specific file:

```sh
./cuc.py --refresh-only --export-json /tmp/cuc-summary.json
```

The JSON result contains one object per update source with package counts,
available items, errors, and timestamps.

## Disable Voice for One Run

Run:

```sh
./cuc.py --no-voice
```

Use `--no-cache` when you also want to skip preview-cache writes:

```sh
./cuc.py --refresh-only --no-cache
```

## Change the AUR Helper

Open the desktop app settings and choose `auto`, `yay`, `paru`, or `pikaur`.

You can also edit the config file:

```json
{
  "aur_helper": "paru"
}
```

Use `auto` when you want the app to use the first supported helper found on the
system.

## Adjust Command Timeouts

Some update checks can take longer on slow networks. Override the timeout for
one run:

```sh
./cuc.py --refresh-only --timeout 300
```

To save a permanent value, use the desktop app settings window.
