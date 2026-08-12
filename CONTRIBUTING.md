# Contributing

This is a small Python project with no build system. Keep changes focused and
match the existing style.

## Project Files

- `cuc_backend.py`: shared models, configuration, provider implementations,
  security checks, logging, and voice support.
- `cuc_controller.py`: frontend-independent orchestration and command planning.
- `cuc_providers.py`: common provider contract and ordered registry.
- `cuc_advisory.py`: authenticated advisory download and cache validation.
- `cuc_diagnostics.py`: privacy-safe diagnostic bundle export.
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
python3 -m py_compile cuc_backend.py cuc_advisory.py cuc_controller.py cuc_desktop.py
python3 -m py_compile cuc_diagnostics.py cuc_providers.py cuc_privileged.py
xmllint --noout packaging/io.github.alvin.Cuc.policy
bash -n PKGBUILD scripts/*.sh
```

CI also runs Ruff, MyPy on the GTK-independent modules, coverage reporting,
ShellCheck, XML validation, and PKGBUILD syntax validation.

## Style Notes

- Prefer standard-library code unless a dependency is already used.
- Keep update-source behavior behind the provider contract and registry.
- Treat Pacman, AUR, Flatpak, and Snap as independent optional sources.
- Do not overwrite invalid config files.
- Never collect administrator passwords in the desktop process or pass
  credential-bearing file descriptors to update helpers.
- Keep Pacman and Snap behind the fixed-command Polkit helper. Run AUR helpers
  as the desktop user in a real terminal so package review remains interactive.
- Treat the AUR advisory list as a backstop, never as proof that another
  package is safe.
- Add an authoritative source URL for each advisory entry.
- Keep the Ed25519 advisory private key offline and outside the repository. Use
  `scripts/sign-advisory.py` to produce the detached base64 signature; publish
  only the public key, advisory JSON, and `.sig` file.

## Manual Test Checklist

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
