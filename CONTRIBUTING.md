# Contributing

The active implementation lives in `rust/`, with explicit binary paths in
`Cargo.toml` because the legacy Arch packaging ignores a root `src/` directory.
Keep Pacman and Flatpak behavior isolated behind the provider functions. Prefer
the standard library and preserve defensive config handling: invalid files must
be reported, not overwritten. Mock subprocesses and optional system tools in
tests.

Run before handoff:

```sh
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
cargo build --release --locked
python3 -m unittest -v test_cuc.py
python3 -m py_compile cuc_backend.py cuc_controller.py cuc_desktop.py
python3 -m py_compile cuc_diagnostics.py cuc_providers.py cuc_privileged.py
```

Pacman desktop updates must continue through the fixed-command Polkit helper.
Do not add password collection to the desktop process. Keep logs and previews
in their documented XDG locations.

`rust/gtk.rs` contains the small system GTK4/libadwaita FFI surface. Keep
callbacks on the GTK main thread and match each C signal signature exactly.
Workers send plain data through a channel; they never access GTK pointers.
Run `./target/release/cuc-desktop --smoke-test` on a graphical session to
construct the dashboard, settings and review windows using fixture data,
then exit automatically. It does not access update sources, install packages,
write settings or play audio.
