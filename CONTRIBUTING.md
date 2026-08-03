# Contributing

This is a small Python project with no build system. Keep changes focused and
match the existing style.

## Project Files

- `cuc.py`: terminal interface, update checks, installs, config, logging, voice.
- `cuc_desktop.py`: GTK/libadwaita desktop app.
- `cuc_privileged.py`: fixed-command Polkit helper for system-managed sources.
- `test_cuc.py`: unit tests for config and core update behavior.
- `io.github.alvin.Cuc.desktop`: desktop launcher.
- `data/aur-security-advisories.json`: source-attributed AUR incident data.
- `tests/fixtures/`: representative provider output.

## Local Checks

Run these before handing off changes:

```sh
python3 -m unittest -v test_cuc.py
python3 -m py_compile cuc.py
python3 -m py_compile cuc_desktop.py
python3 -m py_compile cuc_privileged.py
```

CI also runs Ruff's high-signal syntax and undefined-name checks.

## Style Notes

- Prefer standard-library code unless a dependency is already used.
- Keep update-source behavior isolated behind the existing checker functions.
- Treat Pacman, AUR, Flatpak, and Snap as independent optional sources.
- Do not overwrite invalid config files.
- Keep desktop update commands non-interactive after the password dialog.
- Treat the AUR advisory list as a backstop, never as proof that another
  package is safe.
- Add an authoritative source URL for each advisory entry.

## Manual Test Checklist

Run the terminal dashboard:

```sh
./cuc.py --refresh-only
```

Run JSON output:

```sh
./cuc.py --json
```

Run the desktop app:

```sh
./cuc_desktop.py
```

In the desktop app, check:

- Refresh button starts a new check.
- Extended package list opens and closes with Enter.
- Settings save and reload correctly.
- AUR installs do not open a separate authentication prompt.
- AUR safety blocks cannot proceed through the review dialog.
- Cancelling an update stops the active process and refreshes status.
