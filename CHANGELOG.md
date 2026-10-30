# Compact dashboard and completion feedback — 0.7.3

- Start with a compact 476 × 480 dashboard, fitting its size to the displayed
  content and available screen space instead of reopening at an expanded size.
- Install approved updates in the background with a short status, cancellation,
  and full command output in the application log.
- Highlight active Check, Review and Install steps in bright green; pulse busy
  steps every 0.8 seconds and remember when Review has been opened.
- Replace the completion dialog with Alba's announcement and a flash on the
  verified up-to-date status. Refresh the dashboard before starting speech.
- After queued speech, synthesis and playback finish, brighten the three steps
  and top status together, then fade to their normal colors over two seconds.
- Respect disabled desktop animations and keep blocked or failed update details
  visible inline. Preserve Polkit, preflight, cancellation and rollback safeguards.
- Add regression coverage for outcome wording, voice activity, and completion
  timing, plus offline previews of the flash, shared fade and inline failures.

# Speech waveform — 0.7.2

- Add the GitHub updater's centered green waveform to the native GTK dashboard.
- Follow Piper's PCM loudness during announcements and voice samples, including
  WAV fallback playback; gently pulse while checking and hide when idle.
- Respect the desktop animation setting and pause drawing while unmapped.
- Preserve audio bytes, player fallback, timeouts and exit-status checks.
- Add loudness, WAV parsing, meter cleanup and audio relay regression tests,
  plus offline checking/speech waveform previews.

# Restore the original desktop and voice — 0.7.1

- Restore the 0.6 overview, gold icon, compact source cards, status colors,
  workflow hints, icon actions and footer using the original CSS.
- Restore native libadwaita preferences, installed voice selection, sample
  playback, package review with source checkboxes and confirmation dialogs.
- Restore automatic check announcements, the repeat speaker button, original
  status wording, and the original 22,050 Hz mono Piper audio. Prefer
  native PipeWire playback, with ALSA/WAV fallback to avoid ALSA drain hangs.
- Keep synthesis/player exit checks, timeouts, update preflight and Polkit policy.

# Rust rewrite — 0.7.0

- Replace the default desktop and privileged helper with Rust executables.
- Retain Pacman/Flatpak checks, source selection, package review, preflight,
  retries, streamed output, cancellation, timeouts, settings, notifications,
  Piper announcements, geometry, session recovery and redacted diagnostic ZIPs.
- Use a cancellation-only control pipe so the privileged helper can interrupt
  its own Pacman child. Passwords remain entirely with the Polkit agent.
- Retain Python files as the migration reference and regression suite.

# Changelog

## Unreleased

- Give the desktop all-clear state a grumpier success message.
- Remove AUR and Snap checks, settings, install actions, dependencies, and
  packaging; Pacman and Flatpak are now the supported update sources.

## 0.6.0 — 2026-08-12

- Remove the terminal dashboard, JSON/scripted CLI, and `/usr/bin/cuc`
  packaging entry point; CachyOS Update Center is now desktop-only.
- Rename the shared non-executable module from `cuc.py` to `cuc_backend.py` and
  remove the Rich runtime dependency.
- Keep the dedicated terminal handoff for secure AUR authentication and
  PKGBUILD review.

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
