//! Fixed-command Polkit supervisor. The only control channel is stdin EOF,
//! which requests cancellation; it cannot select executables or package arguments.
use std::{
    env,
    os::unix::process::CommandExt,
    process::{Command, ExitCode, Stdio},
};
fn main() -> ExitCode {
    let args: Vec<_> = env::args_os().skip(1).collect();
    if args.len() != 1 || args[0] != "pacman" {
        eprintln!("Usage: cuc-privileged pacman");
        return ExitCode::from(2);
    }
    // SAFETY: geteuid has no preconditions or pointer arguments.
    if unsafe { libc::geteuid() } != 0 {
        eprintln!("Authorize this helper through Polkit.");
        return ExitCode::from(77);
    }
    // A cancelled authentication request must not start a new transaction.
    let mut control = libc::pollfd {
        fd: 0,
        events: libc::POLLIN | libc::POLLHUP,
        revents: 0,
    };
    // SAFETY: one valid writable pollfd, with no wait.
    if unsafe { libc::poll(&mut control, 1, 0) } < 0 || control.revents != 0 {
        eprintln!("Desktop control channel is closed or cancelled.");
        return ExitCode::from(125);
    }
    let child = match Command::new("/usr/bin/pacman")
        .args(["-Syu", "--noconfirm"])
        .env_clear()
        .env("HOME", "/root")
        .env("LANG", "C.UTF-8")
        .env("PATH", "/usr/bin")
        .current_dir("/")
        .stdin(Stdio::null())
        .process_group(0)
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Cannot execute Pacman: {e}");
            return ExitCode::FAILURE;
        }
    };
    match cachyos_update_center::process::supervise_child(child, std::io::stdin()) {
        Ok(status) => ExitCode::from(status.code().unwrap_or(1).clamp(0, 255) as u8),
        Err(e) => {
            eprintln!("Could not wait for Pacman: {e}");
            ExitCode::FAILURE
        }
    }
}
