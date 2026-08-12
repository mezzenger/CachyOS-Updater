# Repository Guidelines

## Project Structure & Module Organization

This is a small Python project with no build system. Source files live at the
repository root:

- `cuc.py`: terminal dashboard, JSON output, update checks, installs, config,
  logging, and voice support.
- `cuc_controller.py`: frontend-independent check, retry, merge, and command
  planning orchestration.
- `cuc_providers.py`: shared provider contract and ordered registry.
- `cuc_advisory.py`: authenticated and versioned AUR advisory updates.
- `cuc_diagnostics.py`: privacy-safe diagnostic bundle creation.
- `cuc_desktop.py`: GTK/libadwaita desktop dashboard.
- `test_cuc.py`: unit tests for config handling and core update behavior.
- `io.github.alvin.Cuc.desktop`: desktop launcher.
- `README.md`, `INSTALL.md`, `HOWTO.md`, `TROUBLESHOOTING.md`, and
  `CONTRIBUTING.md`: user and contributor documentation.

Avoid committing generated files such as `__pycache__/` contents or local backup
archives under `backups/`.

## Build, Test, and Development Commands

Run the terminal checker:

```sh
./cuc.py --refresh-only
```

Run the interactive terminal dashboard:

```sh
./cuc.py
```

Run the desktop app:

```sh
./cuc_desktop.py
```

Run the automated checks before handoff:

```sh
python3 -m unittest -v test_cuc.py
python3 -m py_compile cuc.py
python3 -m py_compile cuc_advisory.py
python3 -m py_compile cuc_controller.py
python3 -m py_compile cuc_desktop.py
python3 -m py_compile cuc_diagnostics.py
python3 -m py_compile cuc_providers.py
python3 -m py_compile cuc_privileged.py
python3 -m py_compile scripts/sign-advisory.py
```

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation and clear, descriptive names.
Keep behavior for Pacman, AUR, Flatpak, and Snap isolated behind the existing
checker/provider functions. Prefer standard-library code unless a dependency is
already part of the project. Preserve defensive config behavior: invalid config
files must be reported, not overwritten.

## Testing Guidelines

Tests use the Python standard-library `unittest` framework. Add focused tests to
`test_cuc.py`, naming test methods with `test_...`. Mock subprocesses, filesystem
state, and optional system tools instead of requiring Pacman, AUR helpers,
Flatpak, Snap, GTK, or Piper to be installed in CI-like environments.

## Commit & Pull Request Guidelines

This checkout does not expose readable Git history, so no strict commit pattern
can be inferred. Use concise, imperative commit subjects such as `Add AUR helper
validation` and include context in the body for behavior changes.

Pull requests should describe the user-visible change, list the commands run,
link related issues when applicable, and include screenshots or short notes for
desktop UI changes. Mention any manual checks involving real update sources.

## Security & Configuration Tips

Do not hard-code user-specific paths except documented defaults. Be careful with
commands that install updates or request authentication. Desktop Pacman and Snap
operations must use the fixed-command Polkit helper, and AUR authentication must
remain outside the desktop process. Keep logs and preview
files in the documented XDG locations.
