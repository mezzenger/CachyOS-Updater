# How to Use CachyOS Update Center

Launch `cargo run --release --locked --bin cuc-desktop` from the checkout,
or use the installed application launcher. The dashboard checks Pacman and Flatpak, shows each
pending package list, and lets you install one source or all eligible sources.

Use Settings to enable or disable either source, configure timeouts and free
space requirements, choose a Piper voice, and enable notifications. Pacman
authorization uses the system Polkit agent; Flatpak runs as your desktop user.

Before installation, the app verifies that results are fresh, free space is
adequate, the Pacman database is unlocked, and no conflicting package-manager
process is active. Retry the check after resolving any reported problem.

Keyboard shortcuts include Ctrl+R to refresh, Ctrl+U to review updates, Ctrl+D
to export diagnostics, Ctrl+, for settings, and Ctrl+Q to quit.

Each source card has a list icon to **Review packages** and an install icon to
**Install this source**. The overview has corresponding actions for all eligible
sources. Review shows the complete package lists and lets you select sources;
**Install reviewed updates** requests installation. The install icons provide a
short confirmation without requiring the optional package review.
Repositories can change between the check and installation; Pacman runs a full
system upgrade, and Flatpak updates installed applications and runtimes.

When **Piper status announcements** is enabled, the configured voice speaks a
summary after each completed check. The header speaker button repeats it.
Settings includes an installed voice picker and **Play sample**. **Copy complete
list** is in the package review. **Export diagnostics** writes a redacted ZIP
under `$XDG_STATE_HOME/cachyos-update-center/diagnostics/` (default
`~/.local/state/cachyos-update-center/diagnostics/`) and shows its path.

**Cancel update** requests interruption and waits for the process to exit.
Cancellation or timeout skips the remaining sources. A failed source does not
prevent an independent source from running. Interrupted package transactions
can require manual recovery; inspect the output before trying again.
