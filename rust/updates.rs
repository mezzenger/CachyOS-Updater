use crate::{
    config::Config,
    process::{self, CommandSpec},
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::Path,
    sync::{Arc, atomic::AtomicBool},
    time::{SystemTime, UNIX_EPOCH},
};
pub const SOURCES: [&str; 2] = ["pacman", "flatpak"];
pub const HELPER: &str = "/usr/lib/cachyos-update-center/cuc-privileged";
pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
pub fn now_nanos() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpdateResult {
    pub source: String,
    pub enabled: bool,
    pub available_on_system: bool,
    pub items: Vec<String>,
    pub error: Option<String>,
    pub checked_at: u64,
}
impl UpdateResult {
    pub fn eligible(&self) -> bool {
        self.enabled && self.available_on_system && self.error.is_none() && !self.items.is_empty()
    }
    pub fn fresh(&self, minutes: u64, current: u64) -> bool {
        self.checked_at <= current && current - self.checked_at <= minutes.saturating_mul(60)
    }
}
pub fn parse_lines(text: &str, spacing: bool) -> Vec<String> {
    text.lines()
        .filter(|s| !s.trim().is_empty())
        .map(|s| {
            if spacing {
                s.trim_end().into()
            } else {
                s.trim().into()
            }
        })
        .collect()
}
pub fn check(source: &str, c: &Config) -> UpdateResult {
    check_using(source, c, process::exists, capture_check)
}
type CheckOutput = Result<(Option<i32>, String, String), String>;
pub(crate) fn check_using(
    source: &str,
    c: &Config,
    exists: impl Fn(&str) -> bool,
    capture: impl FnOnce(&CommandSpec, u64) -> CheckOutput,
) -> UpdateResult {
    let mut r = UpdateResult {
        source: source.into(),
        enabled: c.enabled_sources.get(source).copied().unwrap_or(false),
        available_on_system: true,
        items: vec![],
        error: None,
        checked_at: now(),
    };
    if !r.enabled {
        return r;
    }
    let mut spec = match source {
        "pacman" => CommandSpec::new("checkupdates", &[]),
        "flatpak" => CommandSpec::new("flatpak", &["remote-ls", "--updates"]),
        _ => {
            r.error = Some("Unknown source".into());
            return r;
        }
    };
    if !exists(&spec.program) {
        r.available_on_system = false;
        r.error = Some(format!("{} is not installed", spec.program));
        return r;
    }
    let db = std::env::temp_dir().join(format!(
        "cuc-checkupdates-{}-{}",
        std::process::id(),
        now_nanos()
    ));
    if source == "pacman" {
        use std::os::unix::fs::DirBuilderExt;
        if let Err(e) = fs::DirBuilder::new().mode(0o700).create(&db) {
            r.error = Some(e.to_string());
            return r;
        }
        spec.env
            .push(("CHECKUPDATES_DB".into(), db.display().to_string()));
    }
    // Keep stdout separate: Flatpak warnings and checkupdates errors are not packages.
    let output = capture(&spec, c.command_timeout);
    if source == "pacman" {
        let _ = fs::remove_dir_all(db);
    }
    match output {
        Ok((code, stdout, stderr))
            if code == Some(0) || (source == "pacman" && code == Some(2)) =>
        {
            r.items = parse_lines(&stdout, source == "flatpak");
            let _ = stderr;
        }
        Ok((code, _, stderr)) => {
            r.error = Some(format!("Check failed ({code:?}): {}", stderr.trim()))
        }
        Err(e) => r.error = Some(e),
    }
    r.checked_at = now();
    r
}
fn capture_check(
    spec: &CommandSpec,
    timeout: u64,
) -> Result<(Option<i32>, String, String), String> {
    use std::{
        io::Read,
        os::unix::process::CommandExt,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };
    let mut ch = Command::new(&spec.program)
        .args(&spec.args)
        .envs(spec.env.iter().cloned())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|e| e.to_string())?;
    fn reader(mut p: impl Read + Send + 'static) -> thread::JoinHandle<Result<String, String>> {
        thread::spawn(move || {
            let mut b = Vec::new();
            p.by_ref()
                .take(16 * 1024 * 1024 + 1)
                .read_to_end(&mut b)
                .map_err(|e| e.to_string())?;
            if b.len() > 16 * 1024 * 1024 {
                return Err("Check output exceeds 16 MiB".into());
            }
            Ok(String::from_utf8_lossy(&b).into())
        })
    }
    let a = reader(ch.stdout.take().unwrap());
    let b = reader(ch.stderr.take().unwrap());
    let start = Instant::now();
    let status = loop {
        if let Some(s) = ch.try_wait().map_err(|e| e.to_string())? {
            break s;
        }
        if start.elapsed().as_secs() >= timeout {
            unsafe {
                libc::kill(-(ch.id() as i32), libc::SIGKILL);
            }
            let _ = ch.wait();
            return Err("Update check timed out".into());
        }
        thread::sleep(Duration::from_millis(30));
    };
    // Readers are only joined once finished, including descendants retaining a pipe.
    while !a.is_finished() || !b.is_finished() {
        if start.elapsed().as_secs() >= timeout {
            return Err("Update check output timed out".into());
        }
        thread::sleep(Duration::from_millis(30));
    }
    Ok((
        status.code(),
        a.join().map_err(|_| "stdout reader failed")??,
        b.join().map_err(|_| "stderr reader failed")??,
    ))
}
pub fn check_all(c: &Config) -> Vec<UpdateResult> {
    std::thread::scope(|s| {
        let handles: Vec<_> = SOURCES
            .iter()
            .map(|source| s.spawn(move || check(source, c)))
            .collect();
        handles
            .into_iter()
            .map(|h| h.join().expect("checker panicked"))
            .collect()
    })
}
pub fn command(source: &str) -> Result<CommandSpec, String> {
    match source {
        "pacman" => Ok(CommandSpec::new("/usr/bin/pkexec", &[HELPER, "pacman"])),
        "flatpak" => Ok(CommandSpec::new("flatpak", &["update", "-y"])),
        _ => Err("Unknown update source".into()),
    }
}
pub fn helper_ready(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let m = fs::symlink_metadata(path)
        .map_err(|_| "Install the CUC Polkit helper before updating Pacman")?;
    if !m.is_file()
        || m.uid() != 0
        || m.permissions().mode() & 0o022 != 0
        || m.permissions().mode() & 0o111 == 0
    {
        return Err(
            "Polkit helper must be a root-owned executable, not writable by group or others".into(),
        );
    }
    let mut magic = [0; 4];
    use std::io::Read;
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    if file.read_exact(&mut magic).is_err() || magic != *b"\x7fELF" {
        return Err("The Rust Polkit helper is required. Build the release and reinstall it with scripts/install-system-helper.sh".into());
    }
    let mut parent = path.parent();
    while let Some(p) = parent {
        let m = fs::symlink_metadata(p).map_err(|e| e.to_string())?;
        if !m.is_dir() || m.uid() != 0 || m.mode() & 0o022 != 0 {
            return Err("Unsafe Polkit helper directory".into());
        }
        parent = p.parent();
    }
    if !Path::new("/usr/bin/pkexec").is_file()
        || !Path::new("/usr/share/polkit-1/actions/io.github.alvin.Cuc.policy").is_file()
    {
        return Err("Polkit executable or CUC policy is missing".into());
    }
    Ok(())
}
pub fn validate_targets(
    targets: &[String],
    results: &[UpdateResult],
    c: &Config,
    current: u64,
) -> Result<(), String> {
    if targets.is_empty() {
        return Err("No sources selected".into());
    }
    for source in targets {
        let r = results
            .iter()
            .find(|r| &r.source == source)
            .ok_or("Check updates first")?;
        if !SOURCES.contains(&source.as_str())
            || !r.eligible()
            || c.enabled_sources.get(source) != Some(&true)
        {
            return Err(format!("{source} is not eligible for installation"));
        }
        if !r.fresh(c.max_result_age_minutes, current) {
            return Err("Refresh required: results are stale".into());
        }
    }
    Ok(())
}
pub fn preflight(targets: &[String], results: &[UpdateResult], c: &Config) -> Result<(), String> {
    validate_targets(targets, results, c, now())?;
    if targets.iter().any(|s| s == "pacman") {
        if Path::new("/var/lib/pacman/db.lck").exists() {
            return Err("Pacman is locked by another operation".into());
        }
        helper_ready(Path::new(HELPER))?;
    }
    for entry in fs::read_dir("/proc").map_err(|e| e.to_string())?.flatten() {
        if entry.file_name().to_string_lossy().parse::<u32>().is_ok() {
            let stat = fs::read_to_string(entry.path().join("stat")).unwrap_or_default();
            if stat
                .rsplit_once(") ")
                .is_some_and(|(_, tail)| tail.starts_with('Z'))
            {
                continue;
            }
            let name = fs::read_to_string(entry.path().join("comm")).unwrap_or_default();
            if targets.iter().any(|s| s == name.trim()) {
                return Err(format!(
                    "{} is already running (PID {})",
                    name.trim(),
                    entry.file_name().to_string_lossy()
                ));
            }
        }
    }
    let mut stat = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // SAFETY: NUL-terminated path and writable output of the correct C type.
    if unsafe { libc::statvfs(c"/".as_ptr(), stat.as_mut_ptr()) } != 0 {
        return Err("Could not check free disk space".into());
    }
    let stat = unsafe { stat.assume_init() };
    let free = stat.f_bavail.saturating_mul(stat.f_frsize) / (1024 * 1024);
    if free < c.minimum_free_space_mb {
        return Err(format!(
            "Only {free} MiB free; {} MiB required",
            c.minimum_free_space_mb
        ));
    }
    Ok(())
}
#[derive(Debug, Clone, Serialize)]
pub struct Outcome {
    pub source: String,
    pub status: String,
    pub message: String,
    pub exit_code: Option<i32>,
}
pub fn install(
    targets: &[String],
    c: &Config,
    cancel: Arc<AtomicBool>,
    mut emit: impl FnMut(&str),
) -> Vec<Outcome> {
    let mut outcomes = vec![];
    let mut stopped = false;
    for source in targets {
        let outcome = if stopped {
            Outcome {
                source: source.clone(),
                status: "skipped".into(),
                message: "Skipped after cancellation or timeout".into(),
                exit_code: None,
            }
        } else {
            let result = command(source).and_then(|spec| {
                emit(&format!("\n$ {} {}\n", spec.program, spec.args.join(" ")));
                process::run(
                    &spec,
                    c.installation_timeout,
                    cancel.clone(),
                    None,
                    &mut emit,
                )
            });
            match result {
                Err(e) => Outcome {
                    source: source.clone(),
                    status: "failed".into(),
                    message: e,
                    exit_code: None,
                },
                Ok(o) => {
                    let status = if o.cancelled {
                        "cancelled"
                    } else if o.timed_out {
                        "timed-out"
                    } else if o.code == Some(0) {
                        "succeeded"
                    } else {
                        "failed"
                    };
                    stopped = o.cancelled || o.timed_out;
                    Outcome {
                        source: source.clone(),
                        status: status.into(),
                        message: format!("{source}: {status} (exit {:?})", o.code),
                        exit_code: o.code,
                    }
                }
            }
        };
        emit(&format!("\n{}\n", outcome.message));
        outcomes.push(outcome);
    }
    outcomes
}
pub fn summary(results: &[UpdateResult]) -> String {
    let mut parts = vec![];
    for r in results.iter().filter(|r| r.enabled) {
        parts.push(if !r.available_on_system {
            format!("{} unavailable", r.source)
        } else if let Some(e) = &r.error {
            format!("{} check failed: {e}", r.source)
        } else {
            format!("{}: {} updates", r.source, r.items.len())
        });
    }
    if parts.is_empty() {
        "All update sources are disabled".into()
    } else if results
        .iter()
        .filter(|r| r.enabled)
        .all(|r| r.error.is_none() && r.items.is_empty() && r.available_on_system)
    {
        "Annoyingly current".into()
    } else {
        parts.join(" · ")
    }
}
pub fn package_list(results: &[UpdateResult]) -> String {
    results
        .iter()
        .filter(|r| r.enabled)
        .map(|r| {
            format!(
                "{} — {} {}\n{}",
                crate::presentation::name(&r.source),
                r.items.len(),
                if r.items.len() == 1 {
                    "update"
                } else {
                    "updates"
                },
                r.error.clone().unwrap_or_else(|| r.items.join("\n"))
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}
