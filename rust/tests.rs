use crate::{
    config::Config,
    diagnostics,
    process::{self, CommandSpec},
    updates::{self, UpdateResult},
};
use std::{
    fs,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        static NEXT_TEMP: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!(
            "cuc-test-{}-{}-{}",
            std::process::id(),
            updates::now_nanos(),
            NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
#[test]
fn config_defaults_and_legacy_migration() {
    let t = Temp::new();
    let p = t.0.join("config.json");
    assert_eq!(Config::load(&p).unwrap().command_timeout, 120);
    fs::write(&p, r#"{"enabled_sources":{"aur":true,"snap":false,"pacman":false},"aur_advisory_url":"legacy"}"#).unwrap();
    let c = Config::load(&p).unwrap();
    assert!(!c.enabled_sources["pacman"]);
    assert!(c.enabled_sources["flatpak"]);
    assert_eq!(c.enabled_sources.len(), 2);
    c.save(&p).unwrap();
    let s = fs::read_to_string(p).unwrap();
    assert!(!s.contains("aur"));
    assert!(!s.contains("snap"));
}
#[test]
fn invalid_config_is_preserved() {
    let t = Temp::new();
    let p = t.0.join("config.json");
    for value in [
        "[]",
        "null",
        "{",
        r#"{"command_timeout":true}"#,
        r#"{"installation_timeout":0}"#,
        r#"{"enabled_sources":{"bogus":true}}"#,
        r#"{"voice_enabled":"yes"}"#,
        r#"{"spoken_name":" "}"#,
        r#"{"enabled_sources":{"pacman":1}}"#,
    ] {
        fs::write(&p, value).unwrap();
        assert!(Config::load(&p).is_err(), "{value}");
        assert!(Config::default().save(&p).is_err());
        assert_eq!(fs::read_to_string(&p).unwrap(), value);
    }
}
#[test]
fn atomic_config_roundtrip() {
    let t = Temp::new();
    let p = t.0.join("nested/config.json");
    let c = Config {
        spoken_name: "Test user".into(),
        ..Config::default()
    };
    c.save(&p).unwrap();
    assert_eq!(Config::load(&p).unwrap().spoken_name, "Test user");
    assert_eq!(fs::read_dir(p.parent().unwrap()).unwrap().count(), 1);
}
#[test]
fn fixtures_preserve_every_package_and_flatpak_columns() {
    let pac = include_str!("../tests/fixtures/pacman_updates.txt");
    let flat = include_str!("../tests/fixtures/flatpak_updates.txt");
    assert_eq!(
        updates::parse_lines(pac, false).len(),
        pac.lines().filter(|s| !s.trim().is_empty()).count()
    );
    assert_eq!(
        updates::parse_lines(flat, true).len(),
        flat.lines().filter(|s| !s.trim().is_empty()).count()
    );
    assert_eq!(
        updates::parse_lines("  App\tID   \n\n", true),
        ["  App\tID"]
    );
}
fn result() -> UpdateResult {
    UpdateResult {
        source: "pacman".into(),
        enabled: true,
        available_on_system: true,
        items: vec!["linux 1 -> 2".into()],
        error: None,
        checked_at: 1000,
    }
}
#[test]
fn stale_failed_disabled_and_missing_results_block_install() {
    let c = Config::default();
    let targets = vec!["pacman".into()];
    let r = result();
    assert!(updates::validate_targets(&targets, std::slice::from_ref(&r), &c, 1900).is_ok());
    assert!(updates::validate_targets(&targets, std::slice::from_ref(&r), &c, 1901).is_err());
    assert!(updates::validate_targets(&targets, std::slice::from_ref(&r), &c, 999).is_err());
    assert!(updates::validate_targets(&targets, &[], &c, 1000).is_err());
    for bad in [
        UpdateResult {
            error: Some("offline".into()),
            ..r.clone()
        },
        UpdateResult {
            enabled: false,
            ..r.clone()
        },
        UpdateResult {
            available_on_system: false,
            ..r.clone()
        },
        UpdateResult {
            items: vec![],
            ..r.clone()
        },
    ] {
        assert!(updates::validate_targets(&targets, &[bad], &c, 1000).is_err());
    }
    let mut c = c;
    c.enabled_sources.insert("pacman".into(), false);
    assert!(updates::validate_targets(&targets, &[r], &c, 1000).is_err());
}
#[test]
fn commands_are_allowlisted_and_desktop_never_uses_sudo() {
    let pac = updates::command("pacman").unwrap();
    assert_eq!(pac.program, "/usr/bin/pkexec");
    assert_eq!(pac.args, [updates::HELPER, "pacman"]);
    let flat = updates::command("flatpak").unwrap();
    assert_eq!(flat.args, ["update", "-y"]);
    for s in ["aur", "snap", "pacman; touch /tmp/unsafe", "", "--help"] {
        assert!(updates::command(s).is_err());
    }
}
#[test]
fn untrusted_helper_is_rejected() {
    let t = Temp::new();
    let p = t.0.join("helper");
    fs::write(&p, "untrusted").unwrap();
    assert!(updates::helper_ready(&p).is_err());
    std::os::unix::fs::symlink("/usr/bin/true", t.0.join("link")).unwrap();
    assert!(updates::helper_ready(&t.0.join("link")).is_err());
}
#[test]
fn process_drains_both_streams_and_reports_nonzero() {
    let o = process::run(
        &CommandSpec::new(
            "/bin/sh",
            &["-c", "printf stdout; printf stderr >&2; exit 7"],
        ),
        5,
        Arc::new(AtomicBool::new(false)),
        None,
        |_| {},
    )
    .unwrap();
    assert_eq!(o.code, Some(7));
    assert!(o.text.contains("stdout"));
    assert!(o.text.contains("stderr"));
    assert!(!o.cancelled);
    assert!(!o.timed_out);
}
#[test]
fn timeout_works_without_newline_and_reaps_child() {
    let start = Instant::now();
    let o = process::run(
        &CommandSpec::new("/bin/sh", &["-c", "printf waiting; sleep 60"]),
        1,
        Arc::new(AtomicBool::new(false)),
        None,
        |_| {},
    )
    .unwrap();
    assert!(o.timed_out);
    assert!(!o.cancelled);
    assert!(start.elapsed() < Duration::from_secs(5));
    assert!(o.text.contains("waiting"));
}
#[test]
fn explicit_cancellation_stops_process_and_remaining_sources() {
    let cancel = Arc::new(AtomicBool::new(false));
    let flag = cancel.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(150));
        flag.store(true, Ordering::SeqCst);
    });
    let o = process::run(
        &CommandSpec::new("/bin/sh", &["-c", "sleep 60"]),
        20,
        cancel,
        None,
        |_| {},
    )
    .unwrap();
    assert!(o.cancelled);
    assert!(!o.timed_out);
    let outcomes = updates::install(
        &["flatpak".into(), "pacman".into()],
        &Config::default(),
        Arc::new(AtomicBool::new(true)),
        |_| {},
    );
    assert_eq!(outcomes[0].status, "cancelled");
    assert_eq!(outcomes[1].status, "skipped");
}
#[test]
fn diagnostics_redacts_secrets_and_omits_packages() {
    let t = Temp::new();
    let log = t.0.join("app.log");
    fs::write(&log, "password=hunter2 Bearer private abc@example.com").unwrap();
    let c = Config {
        log_file: log.display().to_string(),
        spoken_name: "PRIVATE_NAME".into(),
        ..Config::default()
    };
    let path = t.0.join("diagnostics.zip");
    diagnostics::export(&path, &c, &[result()]).unwrap();
    let bytes = fs::read(&path).unwrap();
    let text = String::from_utf8_lossy(&bytes);
    assert!(bytes.starts_with(b"PK\x03\x04"));
    for secret in [
        "hunter2",
        "Bearer private",
        "abc@example.com",
        "PRIVATE_NAME",
        "linux 1 -> 2",
    ] {
        assert!(!text.contains(secret), "{secret}");
    }
    // Validate actual ZIP headers, CRC and directory using the independent stdlib reader.
    let status = std::process::Command::new("python3").args(["-c", "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None; assert len(z.namelist()) == 6"]).arg(&path).status().unwrap();
    assert!(status.success());
    assert!(diagnostics::export(&path, &c, &[]).is_err());
}
#[test]
fn incomplete_checks_never_claim_current() {
    let mut r = result();
    r.items.clear();
    assert_eq!(updates::summary(&[r.clone()]), "Annoyingly current");
    r.available_on_system = false;
    r.error = Some("missing".into());
    assert_ne!(updates::summary(&[r]), "Annoyingly current");
    assert_eq!(updates::summary(&[]), "All update sources are disabled");
}
#[test]
fn provider_checks_are_isolated_and_stderr_is_not_a_package() {
    let c = Config::default();
    let mut database = PathBuf::new();
    let pac = updates::check_using(
        "pacman",
        &c,
        |name| name == "checkupdates",
        |spec, timeout| {
            assert_eq!(spec.program, "checkupdates");
            assert_eq!(timeout, c.command_timeout);
            let db = &spec
                .env
                .iter()
                .find(|(k, _)| k == "CHECKUPDATES_DB")
                .unwrap()
                .1;
            assert!(std::path::Path::new(db).is_dir());
            // Return the path through stdout to check that it is removed afterwards.
            Ok((Some(0), db.clone(), "ignored warning".into()))
        },
    );
    database.push(&pac.items[0]);
    assert!(!database.exists());
    assert!(pac.error.is_none());
    assert_eq!(pac.items.len(), 1);
    let flat = updates::check_using(
        "flatpak",
        &c,
        |_| true,
        |spec, _| {
            assert_eq!(spec.args, ["remote-ls", "--updates"]);
            Ok((Some(0), "App\torg.test.App\n".into(), "warning".into()))
        },
    );
    assert_eq!(flat.items, ["App\torg.test.App"]);
    let none = updates::check_using(
        "pacman",
        &c,
        |_| true,
        |_, _| Ok((Some(2), "".into(), "".into())),
    );
    assert!(none.items.is_empty());
    assert!(none.error.is_none());
    let fail = updates::check_using(
        "flatpak",
        &c,
        |_| true,
        |_, _| Ok((Some(1), "partial".into(), "network failed".into())),
    );
    assert!(fail.error.unwrap().contains("network failed"));
    assert!(fail.items.is_empty());
    let timeout = updates::check_using("pacman", &c, |_| true, |_, _| Err("timed out".into()));
    assert_eq!(timeout.error.as_deref(), Some("timed out"));
    let missing = updates::check_using(
        "pacman",
        &c,
        |_| false,
        |_, _| panic!("must not execute missing tool"),
    );
    assert!(!missing.available_on_system);
    let mut c = c;
    c.enabled_sources.insert("pacman".into(), false);
    let disabled = updates::check_using(
        "pacman",
        &c,
        |_| panic!("disabled"),
        |_, _| panic!("disabled"),
    );
    assert!(!disabled.enabled);
}
#[test]
fn helper_supervisor_cancels_and_reaps_on_control_eof() {
    use std::{
        os::unix::{net::UnixStream, process::CommandExt},
        process::Command,
    };
    let (reader, writer) = UnixStream::pair().unwrap();
    let child = Command::new("/bin/sleep")
        .arg("60")
        .process_group(0)
        .spawn()
        .unwrap();
    let pid = child.id();
    let start = Instant::now();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(150));
        drop(writer);
    });
    let status = process::supervise_child(child, reader).unwrap();
    assert!(!status.success());
    assert!(start.elapsed() < Duration::from_secs(5));
    // waitpid on an already reaped child reports ECHILD.
    let mut status = 0;
    assert_eq!(
        unsafe { libc::waitpid(pid as i32, &mut status, libc::WNOHANG) },
        -1
    );
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::ECHILD)
    );
}
#[test]
fn original_dashboard_wording_and_card_states() {
    use crate::presentation;
    let r = result();
    let overview = presentation::overview(std::slice::from_ref(&r), false);
    assert_eq!(overview.headline, "1 installable update");
    assert_eq!(overview.summary, "1 installable update across 1 source");
    assert_eq!(overview.badge, "Ready to install");
    let card = presentation::card(Some(&r));
    assert_eq!(card.status, "1 update ready");
    assert!(card.actions);
    let mut r = r;
    r.items.clear();
    assert_eq!(
        presentation::overview(std::slice::from_ref(&r), false).headline,
        "Annoyingly current"
    );
    let card = presentation::card(Some(&r));
    assert_eq!(card.status, "Up to date");
    assert!(!card.actions);
    r.error = Some("offline".into());
    assert_eq!(
        presentation::overview(std::slice::from_ref(&r), false).headline,
        "Update check needs attention"
    );
    assert_eq!(presentation::card(Some(&r)).status, "Check failed");
    assert!(!presentation::card(Some(&r)).actions);
    r.available_on_system = false;
    assert_eq!(presentation::card(Some(&r)).status, "Not installed");
    r.enabled = false;
    assert_eq!(presentation::card(Some(&r)).status, "Disabled");
    assert_eq!(presentation::card(None).status, "Checking…");
}
#[test]
fn piper_summaries_match_previous_version() {
    use crate::presentation;
    let config = Config::default();
    let mut pacman = result();
    let mut flatpak = result();
    flatpak.source = "flatpak".into();
    flatpak.items.push("App 1 -> 2".into());
    assert_eq!(
        presentation::speech(&[pacman.clone(), flatpak.clone()], &config, false),
        "Pacman has 1 updates. Flatpak has 2 updates."
    );
    pacman.items.clear();
    assert_eq!(
        presentation::speech(&[pacman.clone()], &config, false),
        "Cozmik.. Your system is currently up to date and is operating nominally!"
    );
    assert_eq!(
        presentation::speech(&[pacman.clone()], &config, true),
        "Cozmik.. Your system is up to date. A reboot is recommended."
    );
    pacman.error = Some("Network".into());
    assert_eq!(
        presentation::speech(&[pacman, flatpak], &config, false),
        "Update check incomplete. Could not check Pacman."
    );
}
#[test]
fn original_raw_voice_parameters_and_both_pipeline_exits() {
    let config = Config {
        piper_model: "/custom/selected voice.onnx".into(),
        ..Config::default()
    };
    let (producer, consumer) = crate::voice::raw_commands(&config, false);
    assert_eq!(producer.program, "piper-tts");
    assert_eq!(
        producer.args,
        ["--model", "/custom/selected voice.onnx", "--output-raw"]
    );
    assert_eq!(consumer.program, "aplay");
    assert_eq!(
        consumer.args,
        ["-q", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"]
    );
    let (_, pw) = crate::voice::raw_commands(&config, true);
    assert_eq!(pw.program, "pw-play");
    assert_eq!(
        pw.args,
        [
            "--raw",
            "--rate",
            "22050",
            "--channels",
            "1",
            "--format",
            "s16",
            "-"
        ]
    );
    let echo = CommandSpec::new("/bin/cat", &[]);
    let sink = CommandSpec::new("/bin/sh", &["-c", "cat >/dev/null"]);
    process::pipeline(&echo, &sink, b"test bytes", 3).unwrap();
    let fail = CommandSpec::new("/bin/sh", &["-c", "exit 9"]);
    assert!(process::pipeline(&fail, &sink, b"test", 3).is_err());
    assert!(process::pipeline(&echo, &fail, b"test", 3).is_err());
    let sleep = CommandSpec::new("/bin/sleep", &["60"]);
    let start = Instant::now();
    assert!(
        process::pipeline(&sleep, &sink, b"", 1)
            .unwrap_err()
            .contains("timed out")
    );
    assert!(start.elapsed() < Duration::from_secs(3));
}
#[test]
fn voice_picker_includes_installed_and_current_models() {
    let temp = Temp::new();
    let root = temp.0.join("voices");
    let model = root.join("en/en_GB/alba/medium/en_GB-alba-medium.onnx");
    fs::create_dir_all(model.parent().unwrap()).unwrap();
    fs::write(&model, b"test").unwrap();
    let current = "/custom/existing.onnx";
    let models = crate::voice::discover_models(&root, current);
    assert_eq!(models.len(), 2);
    assert_eq!(models[0].0, "Alba · English (UK) · Medium");
    assert_eq!(models[1].1, current);
    let models = crate::voice::discover_models(&root, model.to_str().unwrap());
    assert_eq!(models.len(), 1);
}

#[test]
fn audio_relay_preserves_bytes_and_observes_final_partial_chunk() {
    let temp = Temp::new();
    let output = temp.0.join("audio.pcm");
    let sink = CommandSpec::new("/usr/bin/tee", &[output.to_str().unwrap()]);
    let bytes: Vec<_> = (0..17003).map(|i| (i % 256) as u8).collect();
    let captured = Arc::new(std::sync::Mutex::new(Vec::new()));
    let observer = captured.clone();
    process::pipeline_observed(
        &CommandSpec::new("/bin/cat", &[]),
        &sink,
        &bytes,
        3,
        move |chunk| {
            observer.lock().unwrap().extend_from_slice(chunk);
        },
    )
    .unwrap();
    assert_eq!(fs::read(output).unwrap(), bytes);
    assert_eq!(*captured.lock().unwrap(), bytes);
}
