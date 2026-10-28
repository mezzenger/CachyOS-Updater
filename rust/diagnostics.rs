use crate::{
    config::{self, Config},
    updates::{self, UpdateResult},
};
use regex::Regex;
use serde_json::json;
use std::{
    fs::{self, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    os::unix::fs::OpenOptionsExt,
    path::Path,
    sync::Mutex,
};
static LOG_LOCK: Mutex<()> = Mutex::new(());
pub fn log(c: &Config, message: &str) {
    let Ok(_guard) = LOG_LOCK.lock() else {
        return;
    };
    let path = Path::new(&c.log_file);
    if let Some(p) = path.parent() {
        let _ = fs::create_dir_all(p);
    }
    if fs::metadata(path).is_ok_and(|m| m.len() > 2 * 1024 * 1024) {
        let _ = fs::rename(path, path.with_extension("log.1"));
    }
    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)
    {
        let _ = writeln!(f, "{} {}", updates::now(), message);
    }
}
pub fn sanitize(text: &str) -> String {
    let mut s = text.to_string();
    let home = config::home().display().to_string();
    if !home.is_empty() {
        s = s.replace(&home, "$HOME");
    }
    for (pattern, replacement) in [
        (
            r"(?i)\b(password|passwd|token|access[_-]?token|api[_-]?key|secret|authorization|cookie)\s*[:=]\s*\S+",
            "$1=<redacted>",
        ),
        (r"(?i)\bbearer\s+\S+", "Bearer <redacted>"),
        (
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            "<redacted-email>",
        ),
    ] {
        s = Regex::new(pattern)
            .unwrap()
            .replace_all(&s, replacement)
            .into_owned();
    }
    s
}
pub fn export(path: &Path, c: &Config, results: &[UpdateResult]) -> Result<(), String> {
    let mut cfg = serde_json::to_value(c).map_err(|e| e.to_string())?;
    cfg["spoken_name"] = json!("<redacted>");
    for field in ["piper_model", "preview_dir", "log_file"] {
        cfg[field] = json!(sanitize(cfg[field].as_str().unwrap_or_default()));
    }
    let state: Vec<_> = results.iter().map(|r| json!({"source": r.source, "enabled": r.enabled, "available_on_system": r.available_on_system, "count": r.items.len(), "error": r.error.as_ref().map(|e| sanitize(e)), "checked_at": r.checked_at})).collect();
    let mut tail = String::new();
    if let Ok(mut f) = fs::File::open(&c.log_file) {
        let size = f.metadata().map(|m| m.len()).unwrap_or(0);
        let _ = f.seek(SeekFrom::Start(size.saturating_sub(256 * 1024)));
        let mut b = vec![];
        let _ = f.take(256 * 1024).read_to_end(&mut b);
        tail = String::from_utf8_lossy(&b).into();
    }
    // Session metadata contains only source names, statuses and exit codes.
    let session =
        fs::read_to_string(config::state_dir().join("last-session.json")).unwrap_or_default();
    let system = json!({"application_version": env!("CARGO_PKG_VERSION"), "created_at": updates::now(), "os": std::env::consts::OS, "arch": std::env::consts::ARCH, "commands": (["pacman", "checkupdates", "flatpak", "pkexec"].map(|s| (s, crate::process::exists(s))))});
    let entries = vec![("system.json", system.to_string()), ("config.json", cfg.to_string()), ("state.json", json!(state).to_string()), ("app.log", sanitize(&tail)), ("last-session.json", sanitize(&session)), ("README.txt", "CUC diagnostics. Home path, credentials, email addresses and spoken name redacted. Package item lists omitted.\n".into())];
    write_zip(path, &entries).map_err(|e| e.to_string())
}
// ZIP method 0 (stored): no external archiver and no executable archive contents.
fn write_zip(path: &Path, entries: &[(&str, String)]) -> std::io::Result<()> {
    let mut data = vec![];
    let mut central = vec![];
    for (name, text) in entries {
        let offset = data.len() as u32;
        let bytes = text.as_bytes();
        let crc = crc32(bytes);
        let size = bytes.len() as u32;
        u32le(&mut data, 0x04034b50);
        u16le(&mut data, 20);
        u16le(&mut data, 0x0800);
        u16le(&mut data, 0);
        u16le(&mut data, 0);
        u16le(&mut data, 0);
        u32le(&mut data, crc);
        u32le(&mut data, size);
        u32le(&mut data, size);
        u16le(&mut data, name.len() as u16);
        u16le(&mut data, 0);
        data.extend_from_slice(name.as_bytes());
        data.extend_from_slice(bytes);
        u32le(&mut central, 0x02014b50);
        u16le(&mut central, 20);
        u16le(&mut central, 20);
        u16le(&mut central, 0x0800);
        u16le(&mut central, 0);
        u16le(&mut central, 0);
        u16le(&mut central, 0);
        u32le(&mut central, crc);
        u32le(&mut central, size);
        u32le(&mut central, size);
        u16le(&mut central, name.len() as u16);
        for _ in 0..4 {
            u16le(&mut central, 0);
        }
        u32le(&mut central, 0);
        u32le(&mut central, offset);
        central.extend_from_slice(name.as_bytes());
    }
    let offset = data.len() as u32;
    let size = central.len() as u32;
    data.extend(central);
    u32le(&mut data, 0x06054b50);
    u16le(&mut data, 0);
    u16le(&mut data, 0);
    u16le(&mut data, entries.len() as u16);
    u16le(&mut data, entries.len() as u16);
    u32le(&mut data, size);
    u32le(&mut data, offset);
    u16le(&mut data, 0);
    let mut f = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(path)?;
    f.write_all(&data)?;
    f.sync_all()
}
fn u16le(v: &mut Vec<u8>, n: u16) {
    v.extend_from_slice(&n.to_le_bytes());
}
fn u32le(v: &mut Vec<u8>, n: u32) {
    v.extend_from_slice(&n.to_le_bytes());
}
fn crc32(b: &[u8]) -> u32 {
    let mut c = !0u32;
    for &x in b {
        c ^= x as u32;
        for _ in 0..8 {
            c = (c >> 1) ^ (0xedb88320 & (0u32.wrapping_sub(c & 1)));
        }
    }
    !c
}
