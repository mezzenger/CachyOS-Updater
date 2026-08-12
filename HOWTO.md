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
The main review dialog lets you include or omit individual update sources and
clearly separates sources blocked by failed checks or AUR safety findings.

Use the gear button to change settings. Use the speaker button to repeat the
current status with Piper voice output.

Settings can disable providers you do not use, adjust result freshness and
installation timeouts, set a minimum free-space threshold, and turn desktop
notifications on or off. The Authentication section reports whether the
Polkit helper required for desktop system updates is ready.

Keyboard shortcuts are documented in `ACCESSIBILITY.md`. The most common are
`Ctrl+R` to refresh, `Ctrl+U` to review updates, `Ctrl+,` for Settings, and
`Ctrl+D` to export diagnostics.

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

In the desktop app, use `Update all` to approve every eligible source with one
confirmation, without opening the optional review first. Use `Review updates`
or a per-source review button to see the full package list and choose sources
before installing. Each source tile also has an `Install now` button for
installing only that source without opening its review first. The output window
streams each command and offers `Cancel update`; cancellation escalates from
SIGINT to SIGTERM and finally SIGKILL only when a process does not stop. Each source also has a configurable
maximum runtime. The final summary separates succeeded, failed, cancelled,
timed-out, and skipped sources.

Pacman and Snap desktop updates use the system Polkit authentication agent. AUR
updates open in a real terminal for interactive PKGBUILD review and sudo
authentication. The desktop process never receives or forwards the password.

Before installation, CUC checks that results are fresh, free space is above the
configured threshold, Pacman is not locked, another package manager is not
running, provider checks did not report a network failure, and Snap is ready
when selected.

## Retry Failed Checks

If one provider check fails, successfully checked provider results remain
available. Use `Retry failed` to check only the failed providers without
discarding the successful results.

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

## Export Privacy-Safe Diagnostics

Select `Export diagnostics` in the desktop footer or press `Ctrl+D`. The ZIP is
written under the configured preview directory. It contains a redacted log
tail, configuration, source status, and platform details. Credentials, email
addresses, the home-directory path, spoken name, and package item lists are
removed.

## Update AUR Advisory Data

Obtain both the HTTPS advisory URL and its SHA-256 digest through separate
trusted channels, then run:

```sh
./cuc.py --update-advisory HTTPS_URL --advisory-sha256 TRUSTED_SHA256
```

Never copy both values from an untrusted message or page; the separate digest
is what authenticates the downloaded advisory bytes.

For automatic refresh, configure `advisory_feed_url`,
`advisory_signature_url`, and `advisory_public_key_file` in Settings. The
signature file must contain the base64-encoded detached Ed25519 signature for
the exact advisory bytes. CUC refreshes no more often than
`advisory_refresh_hours`, rejects expired or rolled-back advisories, and keeps a
still-valid cached or bundled advisory if the network refresh fails.

These controls are grouped under **AUR security advisories**. Configure all
three signed-feed fields together using HTTPS URLs and an absolute path to a
trusted public-key file. Leave all three blank if you do not operate or trust a
signed advisory service; CUC will continue using its cached or bundled data.
