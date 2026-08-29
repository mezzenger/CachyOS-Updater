# How To Use CachyOS Update Center

## Start the Desktop App

Run the application directly during development:

```sh
./cuc_desktop.py
```

For an installed copy, launch **Cachy Update Center** from the application
menu or run `cuc-desktop` when installed from the Arch package.

The app follows three steps:

1. Check Pacman, AUR, Flatpak, and Snap.
2. Review package lists and any AUR safety findings.
3. Confirm the selected sources and install.

Each source card shows status and update count. Use its list button to open a
scrollable, selectable view of every pending item for that source. The review
also has a `Copy complete list` button for saving or sharing large lists. It
lets you include or omit individual sources and clearly separates sources
blocked by failed checks or AUR safety findings.

## Configure the App

Use the gear button to choose update sources, the preferred AUR helper,
timeouts, minimum free space, desktop notifications, Piper voice, and signed
AUR advisory settings. The Authentication section reports whether the Polkit
helper required for Pacman and Snap is ready.

Keyboard shortcuts are documented in `ACCESSIBILITY.md`. The most common are
`Ctrl+R` to refresh, `Ctrl+U` to review updates, `Ctrl+,` for Settings, and
`Ctrl+D` to export diagnostics.

## Install Updates

Use `Update all` to approve every eligible source with one confirmation. Use
`Review updates` to see the full package list and choose sources first. Each
source tile also offers its own install action.

The output window streams each command and offers `Cancel update`.
Cancellation escalates from SIGINT to SIGTERM and finally SIGKILL only when a
process does not stop. The final summary separates succeeded, failed,
cancelled, timed-out, and skipped sources.

Pacman and Snap use the system Polkit authentication agent. AUR updates open
in a real terminal for interactive PKGBUILD review and sudo authentication.
This terminal is a security boundary for the desktop workflow, not a separate
CUC console application; the desktop process never receives the password.

Before installation, CUC checks result freshness, free space, Pacman locks,
conflicting package managers, provider failures, and Snap readiness when Snap
is selected.

## Retry Failed Checks

If one provider check fails, successful results remain available. Use `Retry
failed` to check only failed providers. If installation is temporarily blocked
by another package-manager process, finish that process and choose `Retry
safety check` without refreshing update results.

## Review AUR Safety Findings

CUC blocks an AUR install when a pending package:

- Appears in the bundled confirmed-incident list.
- Is currently orphaned.
- Is missing from the AUR metadata response.
- Cannot be verified because the safety request failed.

Do not bypass a block without manually reviewing the package repository,
PKGBUILD history, and install-script diff.

## Change the AUR Helper

Open Settings and choose `auto`, `yay`, `paru`, or `pikaur`. With `auto`, CUC
uses the first supported helper found on the system.

## Export Privacy-Safe Diagnostics

Select `Export diagnostics` in the desktop footer or press `Ctrl+D`. The ZIP is
written under the configured preview directory. It contains a redacted log
tail, configuration, source status, and platform details. Credentials, email
addresses, the home path, spoken name, and package item lists are removed.

## Configure Signed AUR Advisory Data

For automatic refresh, configure `advisory_feed_url`,
`advisory_signature_url`, and `advisory_public_key_file` under **AUR security
advisories** in Settings. The signature file must contain the base64-encoded
detached Ed25519 signature for the exact advisory bytes.

Configure all three fields together using HTTPS URLs and an absolute path to a
trusted public-key file. Leave all three blank if you do not operate or trust a
signed advisory service; CUC will use still-valid cached or bundled data.

CUC refreshes no more often than `advisory_refresh_hours`, rejects expired or
rolled-back advisories, and retains valid fallback data when a network refresh
fails.
