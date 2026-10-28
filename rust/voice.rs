use crate::{
    config::Config,
    diagnostics,
    process::{self, CommandSpec},
};
use std::{
    fs,
    path::Path,
    sync::{Arc, Mutex, atomic::AtomicBool},
    time::Instant,
};
static VOICE: Mutex<()> = Mutex::new(());
// Only a compact loudness envelope is shared with GTK, never audio or widgets.
#[derive(Default)]
struct SpeechMeter {
    started: Option<Instant>,
    levels: Vec<f64>,
    step: f64,
}
static METER: Mutex<SpeechMeter> = Mutex::new(SpeechMeter {
    started: None,
    levels: Vec::new(),
    step: 1102.0 / 22050.0,
});
struct MeterReset;
impl Drop for MeterReset {
    fn drop(&mut self) {
        reset_meter();
    }
}
fn reset_meter() {
    if let Ok(mut meter) = METER.lock() {
        *meter = SpeechMeter::default();
    }
}
/// PCM loudness, with the same gain and clipping as GitHub App Updates.
pub fn pcm_level(bytes: &[u8]) -> f64 {
    let count = bytes.len() / 2;
    if count == 0 {
        return 0.0;
    }
    let energy: f64 = bytes
        .chunks_exact(2)
        .map(|s| {
            let sample = i16::from_le_bytes([s[0], s[1]]) as f64 / 32768.0;
            sample * sample
        })
        .sum();
    ((energy / count as f64).sqrt() * 5.0).min(1.0)
}
fn observe_pcm(bytes: &[u8]) {
    if let Ok(mut meter) = METER.lock() {
        if meter.started.is_none() {
            meter.started = Some(Instant::now());
            meter.step = 1102.0 / 22050.0;
        }
        // Each attempt has a 45 second deadline; bound visualization storage too.
        if meter.levels.len() < 1200 {
            meter.levels.push(pcm_level(bytes));
        }
    }
}
/// None means playback is inactive (including synthesis, errors and completion).
pub fn playback_level() -> Option<f64> {
    let meter = METER.lock().ok()?;
    let elapsed = meter.started?.elapsed().as_secs_f64();
    meter.levels.get((elapsed / meter.step) as usize).copied()
}

// WAV fallback players use the same meter and actual PCM loudness.
fn wav_meter(wav: &[u8]) -> Option<SpeechMeter> {
    if wav.get(..4)? != b"RIFF" || wav.get(8..12)? != b"WAVE" {
        return None;
    }
    let mut offset = 12usize;
    let mut format = None;
    let mut audio = None;
    while offset.checked_add(8)? <= wav.len() {
        let size = u32::from_le_bytes(wav.get(offset + 4..offset + 8)?.try_into().ok()?) as usize;
        let start = offset + 8;
        let end = start.checked_add(size)?;
        let chunk = wav.get(start..end)?;
        match wav.get(offset..offset + 4)? {
            b"fmt " if chunk.len() >= 16 => {
                let channels = u16::from_le_bytes(chunk[2..4].try_into().ok()?) as usize;
                let rate = u32::from_le_bytes(chunk[4..8].try_into().ok()?) as usize;
                if chunk[..2] != [1, 0] || chunk[14..16] != [16, 0] || channels == 0 || rate < 20 {
                    return None;
                }
                format = Some((rate, channels));
            }
            b"data" => audio = Some(chunk),
            _ => {}
        }
        offset = end.checked_add(size & 1)?;
    }
    let (rate, channels) = format?;
    let samples = rate / 20;
    Some(SpeechMeter {
        started: Some(Instant::now()),
        levels: audio?
            .chunks(samples.checked_mul(channels)?.checked_mul(2)?)
            .take(1200)
            .map(pcm_level)
            .collect(),
        step: samples as f64 / rate as f64,
    })
}

/// Announce after completed checks, or repeat/sample on explicit user action.
/// Smoke tests never call this function.
pub fn speak(c: Config, text: String) {
    std::thread::spawn(move || {
        let Ok(_guard) = VOICE.lock() else {
            return;
        };
        if !c.voice_enabled || !Path::new(&c.piper_model).is_file() || !process::exists("piper-tts")
        {
            return;
        }
        if let Err(e) = say(&c, &text) {
            diagnostics::log(&c, &format!("Voice unavailable: {e}"));
        }
    });
}
fn say(c: &Config, text: &str) -> Result<(), String> {
    use std::os::unix::{fs::OpenOptionsExt, io::AsRawFd};
    fs::create_dir_all(&c.preview_dir).map_err(|e| e.to_string())?;
    let lock = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(Path::new(&c.preview_dir).join("voice.lock"))
        .map_err(|e| e.to_string())?;
    // Kernel releases this lock on every exit path; do not unlink the lock inode.
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err("Another voice announcement is active".into());
    }
    let _meter_reset = MeterReset;
    // Preserve the original mono 22,050 Hz signed-16 Piper audio. Native
    // PipeWire avoids the ALSA drain hang observed on this desktop.
    for (pipewire, player) in [(true, "pw-play"), (false, "aplay")] {
        if !process::exists(player) {
            continue;
        }
        let (producer, consumer) = raw_commands(c, pipewire);
        reset_meter();
        match process::pipeline_observed(
            &producer,
            &consumer,
            format!("{text}\n").as_bytes(),
            45,
            observe_pcm,
        ) {
            Ok(()) => {
                diagnostics::log(
                    c,
                    &format!("Piper raw synthesis and {player} completed successfully."),
                );
                return Ok(());
            }
            Err(e) => diagnostics::log(
                c,
                &format!("Raw {player} voice pipeline failed; trying fallback: {e}"),
            ),
        }
    }
    reset_meter();
    let wav = Path::new(&c.preview_dir).join(format!(
        "update-check-voice-{}-{}.wav",
        std::process::id(),
        crate::updates::now_nanos()
    ));
    let file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&wav)
        .map_err(|e| e.to_string())?;
    drop(file);
    let result = (|| {
        let path = wav.to_str().ok_or("Invalid voice output path")?;
        let spec = CommandSpec::new(
            "piper-tts",
            &["--model", &c.piper_model, "--output_file", path],
        );
        let o = process::run(
            &spec,
            45,
            Arc::new(AtomicBool::new(false)),
            Some(text.as_bytes()),
            |_| {},
        )?;
        if o.code != Some(0) || o.timed_out {
            return Err("Piper synthesis failed or timed out".into());
        }
        for player in ["pw-play", "paplay", "aplay"] {
            if !process::exists(player) {
                continue;
            }
            reset_meter();
            if let Some(meter) = fs::read(&wav).ok().and_then(|bytes| wav_meter(&bytes)) {
                if let Ok(mut current) = METER.lock() {
                    *current = meter;
                }
            }
            let o = process::run(
                &CommandSpec::new(player, &[path]),
                60,
                Arc::new(AtomicBool::new(false)),
                None,
                |_| {},
            )?;
            if o.code == Some(0) && !o.timed_out {
                diagnostics::log(c, "Piper WAV synthesis and player completed successfully.");
                return Ok(());
            }
        }
        Err("No player completed successfully".into())
    })();
    let _ = fs::remove_file(wav);
    result
}

/// Inventory installed models without following directory symlinks.
pub fn discover_models(root: &Path, current: &str) -> Vec<(String, String)> {
    fn visit(path: &Path, models: &mut Vec<String>) {
        if let Ok(entries) = fs::read_dir(path) {
            for entry in entries.flatten() {
                let Ok(kind) = entry.file_type() else {
                    continue;
                };
                if kind.is_dir() {
                    visit(&entry.path(), models);
                } else if entry.path().extension().is_some_and(|ext| ext == "onnx")
                    && entry.path().is_file()
                {
                    models.push(entry.path().display().to_string());
                }
            }
        }
    }
    let mut paths = vec![];
    visit(root, &mut paths);
    paths.sort();
    if !paths.iter().any(|p| p == current) {
        paths.push(current.into());
    }
    paths
        .into_iter()
        .map(|path| {
            let p = Path::new(&path);
            let parts = p
                .strip_prefix(root)
                .ok()
                .map(|p| {
                    p.iter()
                        .map(|s| s.to_string_lossy().into_owned())
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            fn title(s: &str) -> String {
                s.replace('_', " ")
                    .split_whitespace()
                    .map(|word| {
                        let mut chars = word.chars();
                        match chars.next() {
                            Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
                            None => String::new(),
                        }
                    })
                    .collect::<Vec<_>>()
                    .join(" ")
            }
            let label = if parts.len() >= 5 {
                let locale = match parts[1].as_str() {
                    "en_GB" => "English (UK)".into(),
                    "en_US" => "English (US)".into(),
                    other => other.replace('_', " "),
                };
                format!("{} · {} · {}", title(&parts[2]), locale, title(&parts[3]))
            } else {
                format!(
                    "Current voice · {}",
                    title(&p.file_stem().unwrap_or_default().to_string_lossy())
                )
            };
            (label, path)
        })
        .collect()
}

pub(crate) fn raw_commands(c: &Config, pipewire: bool) -> (CommandSpec, CommandSpec) {
    let player = if pipewire {
        CommandSpec::new(
            "pw-play",
            &[
                "--raw",
                "--rate",
                "22050",
                "--channels",
                "1",
                "--format",
                "s16",
                "-",
            ],
        )
    } else {
        CommandSpec::new(
            "aplay",
            &["-q", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
        )
    };
    (
        CommandSpec::new("piper-tts", &["--model", &c.piper_model, "--output-raw"]),
        player,
    )
}

#[cfg(test)]
mod meter_tests {
    use super::*;
    #[test]
    fn meter_clears_on_completion_or_error() {
        reset_meter();
        assert!(playback_level().is_none());
        {
            let _reset = MeterReset;
            observe_pcm(&4096i16.to_le_bytes().repeat(1102));
            assert!(playback_level().unwrap() > 0.5);
        }
        assert!(playback_level().is_none());
    }
    #[test]
    fn pcm_loudness_distinguishes_silence_volume_and_clips() {
        assert_eq!(pcm_level(&[]), 0.0);
        assert_eq!(pcm_level(&[0; 2204]), 0.0);
        let quiet = 1024i16.to_le_bytes().repeat(1102);
        let loud = 4096i16.to_le_bytes().repeat(1102);
        assert!(pcm_level(&loud) > pcm_level(&quiet));
        assert_eq!(pcm_level(&i16::MIN.to_le_bytes()), 1.0);
        assert_eq!(pcm_level(&[0]), 0.0);
    }
    #[test]
    fn wav_meter_reads_padded_chunks_and_rejects_invalid_audio() {
        let mut wav = b"RIFF\0\0\0\0WAVEJUNK\x01\0\0\0x\0fmt \x10\0\0\0".to_vec();
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&22050u32.to_le_bytes());
        wav.extend_from_slice(&44100u32.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&4408u32.to_le_bytes());
        wav.extend_from_slice(&[0; 2204]);
        wav.extend_from_slice(&4096i16.to_le_bytes().repeat(1102));
        let meter = wav_meter(&wav).unwrap();
        assert_eq!(meter.levels.len(), 2);
        assert_eq!(meter.levels[0], 0.0);
        assert!(meter.levels[1] > 0.5);
        assert!((meter.step - 0.05).abs() < 0.0001);
        assert!(wav_meter(b"not a wave").is_none());
        wav.pop();
        assert!(wav_meter(&wav).is_none());
    }
}
