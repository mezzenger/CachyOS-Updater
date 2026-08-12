# Troubleshooting

## AUR Update Terminal Does Not Open or Authentication Fails

The desktop opens AUR updates in GNOME Console, GNOME Terminal, or xterm. This
keeps password entry and PKGBUILD review outside the desktop process. Install a
supported terminal emulator and confirm the configured AUR helper works there.

If AUR updates still fail:

```sh
sudo -v
yay -Sua
paru -Sua
pikaur -Sua
```

Run the command for your configured helper and confirm it works in a normal
terminal. If it asks package-review questions, resolve those manually or adjust
the helper's config.

## `checkupdates` Is Missing

Pacman checks need `checkupdates` from `pacman-contrib`:

```sh
sudo pacman -S pacman-contrib
```

## No AUR Helper Is Found

Install one supported helper:

```text
yay, paru, pikaur
```

Then set `aur_helper` to `auto` or the exact helper name in the app settings.

## Flatpak or Snap Shows as Not Installed

These sources are optional. Install them only if you use them:

```sh
sudo pacman -S flatpak
sudo pacman -S snapd
```

If you do not use a source, it is normal for the app to mark it unavailable.

## Voice Does Not Play

Check these items:

```sh
command -v piper-tts
test -f /usr/share/piper-voices/en/en_GB/alba/medium/en_GB-alba-medium.onnx
command -v aplay
command -v pw-play
command -v paplay
```

At least one audio player should be available. If the default voice path is not
installed, choose another voice in the desktop settings window.

## JSON Mode Returns a Nonzero Exit Code

`./cuc.py --json` returns a nonzero status when an installed source could not be
checked. The JSON output still contains the error details for that source.

Missing optional sources are reported in the JSON but do not make the overall
check unhealthy.

## AUR Installation Is Blocked

CUC intentionally fails closed when an AUR update matches the confirmed
incident list, is orphaned, is missing from the RPC response, or cannot be
verified. Refresh once to rule out a temporary AUR outage. If the finding
persists, inspect the package's AUR Git history, PKGBUILD, and install scripts
outside CUC before deciding what to do.

The advisory file records its sources:

```text
data/aur-security-advisories.json
```

## Where to Look for Logs

Default log path:

```text
~/.local/state/cachyos-update-center/app.log
```

The path can be changed in the desktop app settings or in the config file.
Desktop logs include provider results, accepted update sources, authentication
status, command starts and exit codes, cancellations, timeouts, preflight
findings, and the final per-source summary. Passwords are never logged.
Logs rotate at 1 MiB with three retained backups.

Use `Export diagnostics` or `Ctrl+D` for a support bundle that removes common
secret assignments, email addresses, home paths, spoken names, and package
item lists.

## Installation Is Blocked by Preflight

The desktop app blocks an update when check results are stale, a selected
provider check failed, the Pacman lock exists, a conflicting package manager is
running, free space is below the configured threshold, or Snap is not ready.
Non-blocking readiness-check errors are shown as warnings.

Process conflicts are scoped to the selected update source. Pacman, Yay, Paru,
and Pikaur share system package state and therefore block Pacman or AUR updates.
Flatpak only blocks another Flatpak update, and Snap only blocks another Snap
update. The warning includes the process ID (PID) so the process can be
identified without guessing:

```sh
ps -p PID -o pid,stat,comm,args
```

Finish or close the reported process and choose `Retry safety check` in the
desktop app. A full update-source refresh is not required. Do not forcibly stop
a package manager while it is writing package data.

For a genuine Snap conflict, inspect Snap's current and recent operations:

```sh
snap changes
```

Use `snap change CHANGE_ID` for the details of a specific operation. Snap may
resume scheduled work after a reboot, so use the reported PID and change status
to distinguish active work from an application error.

## Polkit Shows as Unavailable

Open Settings and review the Native Polkit integration row. Reinstall the
allowlisted helper and policy if needed:

```sh
sudo ./scripts/install-system-helper.sh
```

Pacman and Snap desktop installs are disabled until Polkit is ready. The
terminal frontend remains available for manual sudo authentication.
