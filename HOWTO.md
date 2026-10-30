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
The workflow row highlights every active step in bright green. Check and Install
pulse every 0.8 seconds while running, staying bright between pulses. After the
commands finish, the highlight remains on through the final refresh and stays
steady during any queued Alba announcement. Check stays green after checking,
Review stays green once opened, and Install stays green after a successful
installation. After the announcement finishes, all three steps and the top
status brighten together and fade back to their normal colors over two seconds.
A new check resets the steps. Hidden steps do not pulse, and disabled desktop
animations use a steady highlight instead of motion.
Repositories can change between the check and installation; Pacman runs a full
system upgrade, and Flatpak updates installed applications and runtimes.

After confirmation, updates run in the background. The dashboard shows a short
status and **Cancel update** control; full package-manager output is saved in the
configured application log (default `~/.local/state/cachyos-update-center/app.log`).
When the session finishes, the dashboard refreshes before Alba announces the
results. On verified success, the top “Your system is up to date” status flashes
bright green. No completion dialog covers the dashboard. Failures stay visible
inline, and any reboot recommendation remains in the overview and announcement.
The automatic follow-up check stays quiet.

When **Piper status announcements** is enabled, the configured voice speaks a
summary after each completed check. The header speaker button repeats it.
Settings includes an installed voice picker and **Play sample**. **Copy complete
list** is in the package review. **Export diagnostics** writes a redacted ZIP
under `$XDG_STATE_HOME/cachyos-update-center/diagnostics/` (default
`~/.local/state/cachyos-update-center/diagnostics/`) and shows its path.

**Cancel update** requests interruption and waits for the process to exit.
Cancellation or timeout skips the remaining sources. A failed source does not
prevent an independent source from running. Interrupted package transactions
can require manual recovery; inspect the application log before trying again.
