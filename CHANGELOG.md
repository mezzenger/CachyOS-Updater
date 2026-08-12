# Changelog

## 0.5.2 — 2026-08-12

- Remember the desktop window's last normal size and maximized state.

## 0.5.1 — 2026-08-12

- Scope running-package-manager preflight conflicts to update sources that
  actually share package state.
- Report conflicting process IDs, ignore zombie processes, and let desktop
  users retry the safety check without refreshing update results.
- Move signed advisory controls into a dedicated settings group and validate
  complete HTTPS feed configurations and absolute public-key paths.

## 0.5.0 — 2026-08-08

- Fix the GTK response recursion that reset update sessions before execution.
- Require Polkit for desktop Pacman and Snap operations.
- Move interactive AUR review and authentication to a real terminal without
  passing credentials through the desktop process.
- Share dependency-aware execution policy across terminal and desktop flows.
- Add durable session recovery, exception logging, stronger cancellation, and
  richer diagnostic bundles.
- Add optional automatic Ed25519-signed AUR advisory refresh and expiry checks.
- Move the desktop to current libadwaita dialog APIs.
- Add versioned per-user installs and expanded regression coverage.

Engineering assistance for this release was provided by OpenAI Codex.
