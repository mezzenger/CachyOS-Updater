use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    env, fs,
    io::Write,
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
};

pub const APP: &str = "cachyos-update-center";
pub const MODEL: &str = "/usr/share/piper-voices/en/en_GB/alba/medium/en_GB-alba-medium.onnx";
pub fn home() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/tmp"))
}
pub fn xdg(key: &str, fallback: &str) -> PathBuf {
    env::var_os(key)
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| home().join(fallback))
}
pub fn config_path() -> PathBuf {
    xdg("XDG_CONFIG_HOME", ".config")
        .join(APP)
        .join("config.json")
}
pub fn state_dir() -> PathBuf {
    xdg("XDG_STATE_HOME", ".local/state").join(APP)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub voice_enabled: bool,
    pub piper_model: String,
    pub preview_dir: String,
    pub log_file: String,
    pub command_timeout: u64,
    pub installation_timeout: u64,
    pub max_result_age_minutes: u64,
    pub minimum_free_space_mb: u64,
    pub spoken_name: String,
    pub notifications_enabled: bool,
    pub enabled_sources: BTreeMap<String, bool>,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            voice_enabled: true,
            piper_model: MODEL.into(),
            preview_dir: xdg("XDG_CACHE_HOME", ".cache")
                .join("update-check-preview")
                .display()
                .to_string(),
            log_file: state_dir().join("app.log").display().to_string(),
            command_timeout: 120,
            installation_timeout: 3600,
            max_result_age_minutes: 15,
            minimum_free_space_mb: 1024,
            spoken_name: "Cozmik".into(),
            notifications_enabled: true,
            enabled_sources: [("pacman".into(), true), ("flatpak".into(), true)].into(),
        }
    }
}
impl Config {
    pub fn load(path: &Path) -> Result<Self, String> {
        let text = match fs::read_to_string(path) {
            Ok(s) => s,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Self::default()),
            Err(e) => return Err(format!("Cannot read {}: {e}", path.display())),
        };
        let mut value: serde_json::Value = serde_json::from_str(&text)
            .map_err(|e| format!("Invalid configuration {}: {e}", path.display()))?;
        if !value.is_object() {
            return Err("Configuration must contain a JSON object".into());
        }
        if let Some(sources) = value
            .get_mut("enabled_sources")
            .and_then(|v| v.as_object_mut())
        {
            sources.remove("aur");
            sources.remove("snap");
        }
        let mut c: Self = serde_json::from_value(value)
            .map_err(|e| format!("Invalid configuration {}: {e}", path.display()))?;
        c.enabled_sources.remove("aur");
        c.enabled_sources.remove("snap");
        for source in ["pacman", "flatpak"] {
            c.enabled_sources.entry(source.into()).or_insert(true);
        }
        c.validate()?;
        Ok(c)
    }
    pub fn validate(&self) -> Result<(), String> {
        for (key, value) in [
            ("piper_model", &self.piper_model),
            ("preview_dir", &self.preview_dir),
            ("log_file", &self.log_file),
            ("spoken_name", &self.spoken_name),
        ] {
            if value.trim().is_empty() || value.contains('\0') {
                return Err(format!(
                    "{key} must be a nonempty string without NUL characters"
                ));
            }
        }
        for (key, value) in [
            ("command_timeout", self.command_timeout),
            ("installation_timeout", self.installation_timeout),
            ("max_result_age_minutes", self.max_result_age_minutes),
            ("minimum_free_space_mb", self.minimum_free_space_mb),
        ] {
            if value == 0 || value > i64::MAX as u64 / 1024 {
                return Err(format!("{key} must be a positive integer within range"));
            }
        }
        if self
            .enabled_sources
            .keys()
            .any(|s| s != "pacman" && s != "flatpak")
        {
            return Err("Unknown update source".into());
        }
        Ok(())
    }
    pub fn save(&self, path: &Path) -> Result<(), String> {
        self.validate()?;
        // A corrupt existing file is never replaced by fallback defaults.
        Self::load(path)?;
        atomic_json(path, self)
    }
}
pub fn atomic_json(path: &Path, value: &impl Serialize) -> Result<(), String> {
    let parent = path.parent().ok_or("No parent directory")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    // O_EXCL prevents following an attacker-controlled temporary symlink.
    let temp = parent.join(format!(
        ".cuc-{}-{}.tmp",
        std::process::id(),
        crate::updates::now_nanos()
    ));
    let result = (|| {
        let mut f = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)?;
        f.write_all(&serde_json::to_vec_pretty(value)?)?;
        f.write_all(b"\n")?;
        f.sync_all()?;
        fs::rename(&temp, path)?;
        Ok::<_, std::io::Error>(())
    })();
    let _ = fs::remove_file(temp);
    result.map_err(|e| e.to_string())
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindowState {
    pub width: i32,
    pub height: i32,
    pub maximized: bool,
}
impl Default for WindowState {
    fn default() -> Self {
        Self {
            width: 960,
            height: 640,
            maximized: false,
        }
    }
}
impl WindowState {
    pub fn load() -> Self {
        let mut s: Self = fs::read(state_dir().join("window-state.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        s.width = s.width.clamp(460, 16384);
        s.height = s.height.clamp(480, 16384);
        s
    }
    pub fn save(&self) -> Result<(), String> {
        atomic_json(&state_dir().join("window-state.json"), self)
    }
}
