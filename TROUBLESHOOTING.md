# Troubleshooting

## AUR Update Fails Because Authentication Cannot Be Found

The desktop app validates the administrator password before updates start. For
supported AUR helpers, it also passes non-interactive sudo flags so the helper
reuses that validated sudo session instead of trying to open its own prompt.
The password is supplied to askpass over an inherited anonymous pipe and is not
placed in the update command's environment.

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
