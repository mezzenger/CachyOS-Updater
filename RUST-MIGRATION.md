# Rust implementation

The default build and installation now use Rust 2024 (minimum Rust 1.85).
`cuc-desktop` and `cuc-privileged` do not invoke the Python application.
The existing modified Python files remain available as a migration reference.
AUR/Snap and advisory maintenance are retired features in the current Python
application and are not reintroduced by this port.

| Python component | Rust replacement |
| --- | --- |
| Backend configuration and geometry | `rust/config.rs` |
| Providers, controller and update policy | `rust/updates.rs` |
| Streamed subprocess execution | `rust/process.rs` |
| GTK desktop and settings | `rust/main.rs`, `rust/dashboard.rs`, `rust/gtk.rs` |
| Privileged command allowlist | `rust/bin/privileged.rs` |
| Diagnostics and rotating logs | `rust/diagnostics.rs` |
| Piper voice and player fallback | `rust/voice.rs` |

The GTK interface uses the stable C ABI of installed GTK4/libadwaita libraries.
A small FFI module holds the declarations and ownership helpers. This keeps
compilation offline with the cached Rust dependencies, without generating a
second set of bindings. GTK widgets stay on the main thread. Background checks
and installations send data through a channel polled by the GTK main loop.
Source logos are embedded in the executable.

## Update policy

Checks use `checkupdates` with a private temporary database and Flatpak's
`remote-ls --updates`. Stdout is parsed separately from stderr. Disabled,
missing, failed, future-dated and stale results cannot authorize installation.
Optional review shows every package line and allows source selection. Install
buttons open a confirmation; neither path starts updates without approval. Approval triggers a
fresh preflight check of result age, free space, database lock, conflicting
processes and helper ownership. No updates occur automatically.

Pacman runs only through `/usr/bin/pkexec` and the root-owned fixed helper at
`/usr/lib/cachyos-update-center/cuc-privileged`. The helper accepts exactly one
argument, `pacman`, clears its environment, and starts `/usr/bin/pacman -Syu
--noconfirm`. Flatpak runs as the desktop user. There is no shell expansion or
password input in the desktop process.

The helper monitors a cancellation-only stdin pipe. EOF or input requests
interruption of the helper's Pacman child; no input selects a command or carries
credentials. Signals escalate from SIGINT to SIGTERM after five seconds and
SIGKILL after ten. The desktop waits for process exit. This behavior requires
installing the Rust helper when migrating from the Python version. As with
manually interrupting a package manager, cancellation may leave work requiring
manual recovery.

Configuration uses the existing JSON schema, honors XDG paths, and removes
retired source keys on save. Invalid existing files are reported and preserved.
Logs rotate at 2 MiB with one backup. Diagnostic ZIPs omit package lists and
redact identity and credential-like text. Exports never replace an existing
file. The original compact dashboard and native preferences return in 0.7.1.
When enabled, Piper speaks the original status wording after completed checks;
the speaker button repeats it. The existing voice selection is retained, with
the Alba Piper model as default. Raw `piper-tts --output-raw` audio preserves the original 22,050 Hz mono signed-16
format. Native `pw-play` is preferred because the local ALSA `aplay` path hung
at drain during verification. ALSA and WAV/player fallbacks remain available. Both synthesis and
player exit statuses must succeed; no shell is used. The voice picker and
sample action are available in Settings.

## Validation

Run `cargo test --locked`, `cargo clippy --all-targets -- -D warnings`,
`cargo fmt --check`, and `cargo build --release --locked`.
Tests use fixtures, injected check runners and harmless child processes.
Python 3 independently validates the generated ZIP's directory and CRC values;
it is a test dependency only. The legacy Python suite remains a separate
regression check and does not establish Rust behavior by itself.

`cuc-desktop --smoke-test` constructs the dashboard in ready/current/error
states, native preferences, review with source deselection, confirmation and
progress dialogs using fixtures, then exits. Set `CUC_PREVIEW_DIR` to a local
directory to render PNGs for visual QA. The legacy Python UI can be rendered
with the same fixtures for comparison. It does not check live
sources, request authentication, install packages, save settings, or emit audio.
A successful smoke test establishes native widget construction, not owner
acceptance of the layout. Real package installation, Polkit prompts and audible
Piper delivery require manual validation and are not part of automated tests.
