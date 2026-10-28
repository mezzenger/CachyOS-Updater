# Troubleshooting

## Pacman Is Unavailable

Install `pacman-contrib` so `checkupdates` is present. If installation is
disabled, install the system helper with `sudo ./scripts/install-system-helper.sh`
and confirm a Polkit authentication agent is running.

## Flatpak Is Unavailable

Install it with `sudo pacman -S flatpak`, then refresh the dashboard.

## Installation Is Blocked

Finish other Pacman or Flatpak operations, remove a stale Pacman lock only after
confirming no package manager is active, free the configured minimum disk space,
and refresh stale or failed results.

## Voice Does Not Play

Confirm `piper-tts`, the selected model, and one of `pw-play`, `paplay`, or
`aplay` are installed. Voice failure never blocks update checks or installs.

Use **Export diagnostics** for a redacted support bundle. Logs are stored under
`~/.local/state/cachyos-update-center/` by default.

## Rust Build or Launch Fails

Install Rust, GTK4 and libadwaita development libraries, then run
`cargo build --release --locked`. `cuc-desktop` needs a graphical desktop session;
`--check` provides read-only JSON results without opening GTK.

If the app reports that the Rust helper is required, rebuild both binaries and
reinstall the helper with `sudo ./scripts/install-system-helper.sh`. The old
Python helper does not support the Rust cancellation channel and is rejected.
Enable **Piper status announcements** in Settings for automatic speech after
checks. Use the header speaker button to repeat the status, or **Play sample**
to test the voice selected in Settings. The same Piper voice and raw audio format are preserved. Native PipeWire
playback is preferred, with ALSA and WAV/player fallback. This avoids an ALSA
drain hang observed with the older `aplay` pipeline.
