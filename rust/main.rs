#![allow(unsafe_op_in_unsafe_fn)]
mod dashboard;
mod gtk;
mod speech_wave;
use cachyos_update_center::{
    config::{self, Config, WindowState},
    diagnostics, presentation,
    updates::{self, Outcome, UpdateResult},
    voice,
};
use gtk::*;
use std::{
    ffi::{c_int, c_void},
    ptr::null_mut,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::Instant,
};

#[derive(PartialEq)]
enum Phase {
    Idle,
    Checking,
    Reviewing,
    Installing,
}
enum Event {
    Checked(Vec<UpdateResult>),
    Output(String),
    Installed(Result<Vec<Outcome>, String>),
    Exported(Result<String, String>),
}
struct Settings {
    window: Widget,
    toggles: Vec<Widget>,
    entries: Vec<Widget>,
    spins: Vec<Widget>,
    voice_combo: Widget,
    voice_paths: Vec<String>,
    error: Widget,
}
struct Confirmation {
    window: Widget,
}
struct Ui {
    app: Widget,
    window: Widget,
    status: Widget,
    dashboard: Option<dashboard::Dashboard>,
    progress: Widget,
    progress_close: Widget,
    output: Widget,
    cancel_button: Widget,
    confirmation: Option<Confirmation>,
    review: Widget,
    review_selections: Vec<(String, Widget)>,
    review_status: Widget,
    settings: Option<Settings>,
    config: Config,
    config_error: Option<String>,
    results: Vec<UpdateResult>,
    targets: Vec<String>,
    phase: Phase,
    cancel: Arc<AtomicBool>,
    tx: Sender<Event>,
    rx: Receiver<Event>,
    inhibit: u32,
    smoke: bool,
    smoke_started: Instant,
    timer: u32,
    reboot: bool,
    smoke_stage: u8,
}
impl Ui {
    fn new(smoke: bool) -> Self {
        let (tx, rx) = mpsc::channel();
        let (config, error) = match Config::load(&config::config_path()) {
            Ok(c) => (c, None),
            Err(e) => (Config::default(), Some(e)),
        };
        Self {
            app: null_mut(),
            window: null_mut(),
            status: null_mut(),
            dashboard: None,
            progress: null_mut(),
            progress_close: null_mut(),
            output: null_mut(),
            cancel_button: null_mut(),
            confirmation: None,
            review: null_mut(),
            review_selections: vec![],
            review_status: null_mut(),
            settings: None,
            config,
            config_error: error,
            results: vec![],
            targets: vec![],
            phase: if smoke { Phase::Checking } else { Phase::Idle },
            cancel: Arc::new(AtomicBool::new(false)),
            tx,
            rx,
            inhibit: 0,
            smoke,
            smoke_started: Instant::now(),
            timer: 0,
            reboot: false,
            smoke_stage: 0,
        }
    }
    unsafe fn notice(&self, text: &str) {
        set_label(self.status, text);
        gtk_widget_set_visible(self.status, (!text.is_empty()) as i32);
    }
    unsafe fn sensitive(&self, enabled: bool) {
        if let Some(d) = &self.dashboard {
            d.sensitive(enabled, &self.results);
        }
        if !self.cancel_button.is_null() {
            gtk_widget_set_sensitive(self.cancel_button, (self.phase == Phase::Installing) as i32);
        }
    }

    unsafe fn refresh(&mut self, retry: bool) {
        if self.phase != Phase::Idle {
            return;
        }
        if let Some(e) = &self.config_error {
            self.notice(e);
            return;
        }
        self.phase = Phase::Checking;
        self.sensitive(false);
        if let Some(d) = &self.dashboard {
            d.checking();
        }
        let c = self.config.clone();
        let tx = self.tx.clone();
        let prior = self.results.clone();
        std::thread::spawn(move || {
            let results = if retry {
                std::thread::scope(|scope| {
                    let handles: Vec<_> = updates::SOURCES
                        .iter()
                        .map(|source| {
                            let old = prior.iter().find(|r| r.source == *source).cloned();
                            let c = &c;
                            scope.spawn(move || match old {
                                Some(r)
                                    if r.error.is_none()
                                        || !r.enabled
                                        || !r.available_on_system =>
                                {
                                    r
                                }
                                _ => updates::check(source, c),
                            })
                        })
                        .collect();
                    handles.into_iter().map(|h| h.join().unwrap()).collect()
                })
            } else {
                updates::check_all(&c)
            };
            let _ = tx.send(Event::Checked(results));
        });
    }
    unsafe fn notify(&self, title: &str, body: &str) {
        if !self.config.notifications_enabled || self.smoke {
            return;
        }
        let n = g_notification_new(c(title).as_ptr());
        g_notification_set_body(n, c(body).as_ptr());
        g_application_send_notification(self.app, c"updates".as_ptr(), n);
        g_object_unref(n);
    }
    fn persist(&self, phase: &str) {
        let _ = config::atomic_json(
            &config::state_dir().join("last-session.json"),
            &serde_json::json!({"phase": phase, "targets": self.targets, "updated_at": updates::now()}),
        );
    }
}
unsafe extern "C" fn activate(app: Widget, data: Widget) {
    let ui = &mut *(data as *mut Ui);
    if !ui.window.is_null() {
        gtk_window_present(ui.window);
        return;
    }
    ui.app = app;
    let (win, dashboard) = dashboard::Dashboard::new(app, data);
    ui.window = win;
    ui.status = dashboard.notice;
    ui.dashboard = Some(dashboard);
    let geometry = WindowState::load();
    gtk_window_set_default_size(win, geometry.width, geometry.height);
    if geometry.maximized {
        gtk_window_maximize(win);
    }
    let keys = gtk_event_controller_key_new();
    gtk_event_controller_set_propagation_phase(keys, 1);
    g_signal_connect_data(
        keys,
        c"key-pressed".as_ptr(),
        Some(std::mem::transmute::<
            unsafe extern "C" fn(Widget, u32, u32, u32, Widget) -> c_int,
            Callback,
        >(key_pressed)),
        data,
        None,
        0,
    );
    gtk_widget_add_controller(win, keys);
    connect_close(win, close_requested, data);
    ui.timer = g_timeout_add(50, Some(poll), data);
    gtk_window_present(win);
    if let Some(e) = &ui.config_error {
        ui.notice(&format!(
            "{e}\nThe existing file has been preserved. Correct it and restart."
        ));
    } else if !ui.smoke {
        let previous = std::fs::read_to_string(config::state_dir().join("last-session.json"))
            .ok()
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok());
        if previous
            .as_ref()
            .and_then(|v| v["phase"].as_str())
            .is_some_and(|s| s == "installing" || s == "cancelling")
        {
            ui.notice("A previous installation did not record completion. Inspect the log and package manager before retrying.");
        } else {
            ui.refresh(false);
        }
    }
}
unsafe extern "C" fn check_clicked(_: Widget, data: Widget) {
    (&mut *(data as *mut Ui)).refresh(false);
}
unsafe extern "C" fn retry_clicked(_: Widget, data: Widget) {
    (&mut *(data as *mut Ui)).refresh(true);
}
unsafe extern "C" fn copy_clicked(_: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    gdk_clipboard_set_text(
        gtk_widget_get_clipboard(ui.window),
        c(&updates::package_list(
            &ui.results
                .iter()
                .filter(|r| ui.targets.is_empty() || ui.targets.contains(&r.source))
                .cloned()
                .collect::<Vec<_>>(),
        ))
        .as_ptr(),
    );
}
unsafe extern "C" fn announce_clicked(_: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    if ui.results.is_empty() {
        ui.notice("Check updates first.");
        return;
    }
    if !ui.config.voice_enabled {
        ui.notice("Piper voice is disabled in the CUC configuration.");
        return;
    }
    voice::speak(
        ui.config.clone(),
        presentation::speech(&ui.results, &ui.config, ui.reboot),
    );
}

unsafe extern "C" fn review_all_clicked(_: Widget, data: Widget) {
    review(data, None);
}
unsafe fn review(data: Widget, source: Option<&str>) {
    let ui = &mut *(data as *mut Ui);
    if ui.phase != Phase::Idle {
        return;
    }
    ui.targets = updates::SOURCES
        .iter()
        .enumerate()
        .filter(|(_, s)| {
            (source.is_none() || source == Some(**s))
                && ui.results.iter().any(|r| r.source == **s && r.eligible())
        })
        .map(|(_, s)| (*s).into())
        .collect();
    if let Err(e) = updates::validate_targets(&ui.targets, &ui.results, &ui.config, updates::now())
    {
        ui.notice(&e);
        return;
    }
    if !ui.review.is_null() {
        g_object_unref(ui.review);
    }
    ui.review = approval_dialog("Review updates", "", "Install reviewed updates", data);
    let content = gtk_box_new(1, 14);
    gtk_widget_set_size_request(content, 600, -1);
    ui.review_status = label("");
    css(ui.review_status, "title-2");
    gtk_box_append(content, ui.review_status);
    let hint = label(
        "The complete package list is shown below. Choose the sources to install; nothing starts until you confirm.",
    );
    gtk_label_set_wrap(hint, 1);
    gtk_box_append(content, hint);
    let copy = button("Copy complete list");
    gtk_widget_set_halign(copy, 1);
    connect(copy, "clicked", copy_clicked, data);
    gtk_box_append(content, copy);
    ui.review_selections.clear();
    for r in ui.results.iter().filter(|r| ui.targets.contains(&r.source)) {
        let check = gtk_check_button_new_with_label(
            c(&format!(
                "{} — {} update(s)",
                presentation::name(&r.source),
                r.items.len()
            ))
            .as_ptr(),
        );
        gtk_check_button_set_active(check, 1);
        gtk_box_append(content, check);
        ui.review_selections.push((r.source.clone(), check));
        connect(check, "toggled", review_selection_changed, data);
        let packages = view();
        gtk_text_view_set_cursor_visible(packages, 0);
        text(packages, &r.items.join("\n"));
        css(packages, "package-preview");
        gtk_widget_set_margin_start(packages, 24);
        gtk_box_append(content, packages);
    }
    let scroll = gtk_scrolled_window_new();
    gtk_scrolled_window_set_policy(scroll, 2, 1);
    gtk_scrolled_window_set_min_content_height(scroll, 420);
    gtk_scrolled_window_set_child(scroll, content);
    adw_alert_dialog_set_extra_child(ui.review, scroll);
    ui.phase = Phase::Reviewing;
    ui.sensitive(false);
    let dialog = ui.review;
    review_selection_changed(std::ptr::null_mut(), data);
    adw_dialog_present(dialog, (*(data as *mut Ui)).window);
}
unsafe fn approval_dialog(heading: &str, body: &str, install_label: &str, data: Widget) -> Widget {
    let dialog = adw_alert_dialog_new(c(heading).as_ptr(), c(body).as_ptr());
    g_object_ref_sink(dialog);
    adw_alert_dialog_add_response(dialog, c"cancel".as_ptr(), c"Cancel".as_ptr());
    adw_alert_dialog_add_response(dialog, c"install".as_ptr(), c(install_label).as_ptr());
    adw_alert_dialog_set_response_appearance(dialog, c"install".as_ptr(), 1);
    adw_alert_dialog_set_default_response(dialog, c"install".as_ptr());
    adw_alert_dialog_set_close_response(dialog, c"cancel".as_ptr());
    g_signal_connect_data(
        dialog,
        c"response".as_ptr(),
        Some(std::mem::transmute::<
            unsafe extern "C" fn(Widget, *const std::ffi::c_char, Widget),
            Callback,
        >(approval_response)),
        data,
        None,
        0,
    );
    dialog
}
unsafe extern "C" fn review_selection_changed(_: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    let selected: Vec<_> = ui
        .review_selections
        .iter()
        .filter(|(_, w)| gtk_check_button_get_active(*w) != 0)
        .map(|(s, _)| s)
        .collect();
    let total: usize = ui
        .results
        .iter()
        .filter(|r| selected.contains(&&r.source))
        .map(|r| r.items.len())
        .sum();
    set_label(
        ui.review_status,
        &format!(
            "{total} update{} from {} source{}",
            if total == 1 { "" } else { "s" },
            selected.len(),
            if selected.len() == 1 { "" } else { "s" }
        ),
    );
    adw_alert_dialog_set_response_enabled(
        ui.review,
        c"install".as_ptr(),
        (!selected.is_empty()) as i32,
    );
}
unsafe extern "C" fn approval_response(
    dialog: Widget,
    response: *const std::ffi::c_char,
    data: Widget,
) {
    let ui = &mut *(data as *mut Ui);
    if std::ffi::CStr::from_ptr(response).to_bytes() == b"install" {
        if dialog == ui.review {
            ui.targets = ui
                .review_selections
                .iter()
                .filter(|(_, w)| gtk_check_button_get_active(*w) != 0)
                .map(|(s, _)| s.clone())
                .collect();
        }
        install_clicked(std::ptr::null_mut(), data);
    } else {
        ui.phase = Phase::Idle;
        ui.sensitive(true);
    }
}

unsafe extern "C" fn install_clicked(_: Widget, data: Widget) {
    let ui = &mut *(data as *mut Ui);
    if ui.phase != Phase::Reviewing {
        return;
    }
    if let Err(e) = updates::validate_targets(&ui.targets, &ui.results, &ui.config, updates::now())
    {
        ui.notice(&e);
        ui.phase = Phase::Idle;
        ui.sensitive(true);
        return;
    }
    ui.phase = Phase::Installing;
    ui.cancel = Arc::new(AtomicBool::new(false));
    ui.sensitive(false);
    ui.inhibit = gtk_application_inhibit(
        ui.app,
        ui.window,
        1 | 4,
        c"Installing system updates".as_ptr(),
    );
    ui.show_progress(data);
    ui.persist("installing");
    ui.notice("Installing updates…");
    text(ui.output, "");
    diagnostics::log(
        &ui.config,
        &format!("Installation approved: {}", ui.targets.join(", ")),
    );
    let c = ui.config.clone();
    let results = ui.results.clone();
    let targets = ui.targets.clone();
    let cancel = ui.cancel.clone();
    let tx = ui.tx.clone();
    std::thread::spawn(move || {
        let result = updates::preflight(&targets, &results, &c).map(|_| {
            updates::install(&targets, &c, cancel, |s| {
                diagnostics::log(&c, s);
                let _ = tx.send(Event::Output(s.into()));
            })
        });
        let _ = tx.send(Event::Installed(result));
    });
}
unsafe extern "C" fn cancel_clicked(_: Widget, data: Widget) {
    let ui = &mut *(data as *mut Ui);
    if ui.phase == Phase::Installing {
        ui.cancel.store(true, Ordering::SeqCst);
        ui.persist("cancelling");
        gtk_widget_set_sensitive(ui.cancel_button, 0);
        ui.notice("Cancellation requested. Waiting for the running command to exit…");
    }
}
unsafe extern "C" fn close_requested(_: Widget, data: Widget) -> c_int {
    let ui = &mut *(data as *mut Ui);
    if ui.phase == Phase::Installing {
        ui.notice(
            "Wait for installation to finish, or cancel it and wait for the command to exit.",
        );
        return 1;
    }
    if !ui.smoke {
        let mut width = 960;
        let mut height = 640;
        gtk_window_get_default_size(ui.window, &mut width, &mut height);
        let _ = WindowState {
            width,
            height,
            maximized: gtk_window_is_maximized(ui.window) != 0,
        }
        .save();
    }
    0
}
unsafe extern "C" fn export_clicked(_: Widget, data: Widget) {
    let ui = &mut *(data as *mut Ui);
    let c = ui.config.clone();
    let results = ui.results.clone();
    let tx = ui.tx.clone();
    ui.notice("Exporting a privacy-safe diagnostic ZIP…");
    std::thread::spawn(move || {
        let directory = config::state_dir().join("diagnostics");
        let result = std::fs::create_dir_all(&directory)
            .map_err(|e| e.to_string())
            .and_then(|_| {
                let p = directory.join(format!("cuc-{}.zip", updates::now_nanos()));
                diagnostics::export(&p, &c, &results).map(|_| p.display().to_string())
            });
        let _ = tx.send(Event::Exported(result));
    });
}
unsafe extern "C" fn poll(data: Widget) -> c_int {
    let ui = &mut *(data as *mut Ui);
    // Bound work per tick so busy subprocesses cannot starve GTK input.
    for _ in 0..128 {
        let Ok(event) = ui.rx.try_recv() else {
            break;
        };
        match event {
            Event::Checked(results) => {
                ui.results = results;
                ui.phase = Phase::Idle;
                ui.sensitive(true);
                let status = updates::summary(&ui.results);
                if let Some(d) = &ui.dashboard {
                    d.render(&ui.results, ui.reboot, ui.config.voice_enabled);
                }
                ui.notice("");
                if !ui.smoke {
                    voice::speak(
                        ui.config.clone(),
                        presentation::speech(&ui.results, &ui.config, ui.reboot),
                    );
                }
                diagnostics::log(&ui.config, &status);
                ui.notify("Update check complete", &status);
            }
            Event::Output(s) => {
                let lower = s.to_lowercase();
                if lower.contains("reboot recommended")
                    || lower.contains("reboot is recommended")
                    || lower.contains("restart your computer")
                    || lower.contains("reboot required")
                {
                    ui.reboot = true;
                }
                append(ui.output, &s);
            }
            Event::Installed(result) => {
                if ui.inhibit != 0 {
                    gtk_application_uninhibit(ui.app, ui.inhibit);
                    ui.inhibit = 0;
                }
                ui.phase = Phase::Idle;
                ui.sensitive(true);
                gtk_widget_set_sensitive(ui.progress_close, 1);
                adw_dialog_set_can_close(ui.progress, 1);
                let successful = result
                    .as_ref()
                    .is_ok_and(|outcomes| outcomes.iter().all(|o| o.status == "succeeded"));
                let message = match &result {
                    Ok(outcomes) => outcomes
                        .iter()
                        .map(|o| o.message.as_str())
                        .collect::<Vec<_>>()
                        .join("\n"),
                    Err(e) => format!("Installation blocked: {e}"),
                };
                append(
                    ui.output,
                    &format!(
                        "\n{message}{}\n",
                        if ui.reboot {
                            "\nA reboot is recommended."
                        } else {
                            ""
                        }
                    ),
                );
                ui.notice(&message);
                diagnostics::log(&ui.config, &message);
                ui.persist(if successful { "complete" } else { "failed" });
                if let Ok(outcomes) = &result {
                    let _ = config::atomic_json(
                        &config::state_dir().join("last-outcomes.json"),
                        outcomes,
                    );
                }
                ui.notify(
                    if successful {
                        "Updates completed"
                    } else {
                        "Update session needs attention"
                    },
                    &message,
                );
                // Never offer another install based on the pre-transaction snapshot.
                ui.results.clear();
                if result.is_ok() {
                    ui.refresh(false);
                }
            }
            Event::Exported(result) => ui.notice(&match result {
                Ok(path) => format!("Diagnostics saved: {path}"),
                Err(e) => format!("Diagnostic export failed: {e}"),
            }),
        }
    }
    if ui.smoke && ui.smoke_started.elapsed().as_millis() > 450 {
        let stage = ui.smoke_stage;
        ui.smoke_stage += 1;
        ui.smoke_started = Instant::now();
        let app = ui.app;
        let preview = std::env::var_os("CUC_PREVIEW_DIR").map(std::path::PathBuf::from);
        let save = |window: Widget, name: &str| {
            if let Some(dir) = &preview {
                let _ = std::fs::create_dir_all(dir);
                if let Err(e) = snapshot_window(window, &dir.join(name)) {
                    eprintln!("Preview {name}: {e}");
                }
            }
        };
        match stage {
            0 => {
                save(ui.window, "dashboard-checking-wave.png");
                ui.config = Config::default();
                ui.results = updates::SOURCES
                    .iter()
                    .map(|source| UpdateResult {
                        source: (*source).into(),
                        enabled: true,
                        available_on_system: true,
                        items: if *source == "pacman" {
                            updates::parse_lines(
                                include_str!("../tests/fixtures/pacman_updates.txt"),
                                false,
                            )
                        } else {
                            updates::parse_lines(
                                include_str!("../tests/fixtures/flatpak_updates.txt"),
                                true,
                            )
                        },
                        error: None,
                        checked_at: updates::now(),
                    })
                    .collect();
                ui.dashboard
                    .as_ref()
                    .unwrap()
                    .render(&ui.results, false, true);
                ui.phase = Phase::Idle;
                ui.sensitive(true);
            }
            1 => {
                save(ui.window, "dashboard-ready.png");
                save(ui.window, "dashboard-speech-wave.png");
                for r in &mut ui.results {
                    r.items.clear();
                }
                ui.dashboard
                    .as_ref()
                    .unwrap()
                    .render(&ui.results, false, true);
            }
            2 => {
                save(ui.window, "dashboard-current.png");
                ui.results[0].error = Some("Network unavailable. Try refreshing again.".into());
                ui.dashboard
                    .as_ref()
                    .unwrap()
                    .render(&ui.results, false, true);
            }
            3 => {
                save(ui.window, "dashboard-error.png");
                ui.results[0].error = None;
                show_settings(null_mut(), data);
            }
            4 => {
                save((*(data as *mut Ui)).window, "settings.png");
                let settings_window = (*(data as *mut Ui)).settings.as_ref().unwrap().window;
                adw_dialog_close(settings_window);
            }
            5 => {
                let ui = &mut *(data as *mut Ui);
                ui.results[0].items = updates::parse_lines(
                    include_str!("../tests/fixtures/pacman_updates.txt"),
                    false,
                );
                ui.dashboard
                    .as_ref()
                    .unwrap()
                    .render(&ui.results, false, true);
                let review_button = ui.dashboard.as_ref().unwrap().cards[0].review;
                card_review_clicked(review_button, data);
                assert_eq!((*(data as *mut Ui)).targets, ["pacman"]);
            }
            6 => {
                save((*(data as *mut Ui)).window, "review.png");
                let ui = &*(data as *mut Ui);
                for (_, check) in &ui.review_selections {
                    gtk_check_button_set_active(*check, 0);
                }
                assert_eq!(
                    adw_alert_dialog_get_response_enabled(ui.review, c"install".as_ptr()),
                    0
                );
                adw_dialog_close(ui.review);
            }
            7 => {
                let install_button =
                    (*(data as *mut Ui)).dashboard.as_ref().unwrap().cards[0].install;
                card_install_clicked(install_button, data);
            }
            8 => {
                save((*(data as *mut Ui)).window, "confirmation.png");
                adw_dialog_close((*(data as *mut Ui)).confirmation.as_ref().unwrap().window);
            }
            9 => {
                let ui = &mut *(data as *mut Ui);
                ui.phase = Phase::Installing;
                ui.show_progress(data);
                text(
                    ui.output,
                    "Pacman — example update output\nPreparing transaction…\nThis is an offline layout preview. No commands are running.",
                );
            }
            10 => {
                save((*(data as *mut Ui)).window, "progress.png");
                let ui = &mut *(data as *mut Ui);
                ui.phase = Phase::Idle;
                adw_dialog_set_can_close(ui.progress, 1);
                g_application_quit(app);
            }
            _ => {
                g_application_quit(app);
            }
        }
    }
    if let Some(d) = &ui.dashboard {
        let speech = if ui.smoke && ui.smoke_stage == 1 {
            Some(0.7) // Offline visual fixture; never synthesize or play audio.
        } else {
            voice::playback_level()
        };
        d.wave.update(ui.phase == Phase::Checking, speech);
    }

    1
}

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.iter().any(|s| s == "--help" || s == "-h") {
        println!(
            "CachyOS Update Center {}\nUsage: cuc-desktop [--check | --smoke-test | --version]\n--check prints read-only check results as JSON. Installation requires desktop approval.",
            env!("CARGO_PKG_VERSION")
        );
        return;
    }
    if args == ["--version"] {
        println!("{}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if args == ["--check"] {
        match Config::load(&config::config_path()) {
            Ok(c) => {
                let results = updates::check_all(&c);
                println!("{}", serde_json::to_string_pretty(&results).unwrap());
                if results.iter().any(|r| r.enabled && r.error.is_some()) {
                    std::process::exit(1);
                }
            }
            Err(e) => {
                eprintln!("{e}");
                std::process::exit(1);
            }
        }
        return;
    }
    let smoke = args == ["--smoke-test"];
    if !args.is_empty() && !smoke {
        eprintln!("Unknown option. Use --help.");
        std::process::exit(2);
    }
    unsafe {
        adw_init();
        let app = gtk_application_new(c"io.github.alvin.Cuc".as_ptr(), if smoke { 32 } else { 0 });
        let mut ui = Box::new(Ui::new(smoke));
        ui.app = app;
        let data = (&mut *ui as *mut Ui).cast::<c_void>();
        connect(app, "activate", activate, data);
        let code = g_application_run(app, 0, null_mut());
        if ui.timer != 0 {
            g_source_remove(ui.timer);
        }
        // All callbacks retain `data` only while their widget/application exists.
        // Destroy secondary windows before dropping the state.
        if !ui.review.is_null() {
            adw_dialog_force_close(ui.review);
            g_object_unref(ui.review);
        }
        if let Some(c) = &ui.confirmation {
            adw_dialog_force_close(c.window);
            g_object_unref(c.window);
        }
        if !ui.progress.is_null() {
            adw_dialog_force_close(ui.progress);
            g_object_unref(ui.progress);
        }
        if let Some(s) = &ui.settings {
            adw_dialog_force_close(s.window);
            g_object_unref(s.window);
        }
        g_object_unref(app);
        std::process::exit(code);
    }
}

unsafe extern "C" fn key_pressed(_: Widget, key: u32, _: u32, state: u32, data: Widget) -> c_int {
    if state & 4 == 0 {
        return 0;
    }
    match char::from_u32(key).map(|c| c.to_ascii_lowercase()) {
        Some('r') => check_clicked(null_mut(), data),
        Some('u') => review_all_clicked(null_mut(), data),
        Some('d') => export_clicked(null_mut(), data),
        Some(',') => show_settings(null_mut(), data),
        Some('q') => gtk_window_close((*(data as *mut Ui)).window),
        _ => return 0,
    }
    1
}

unsafe extern "C" fn card_review_clicked(button: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    let source = ui
        .dashboard
        .as_ref()
        .and_then(|d| d.cards.iter().position(|c| c.review == button))
        .map(|i| updates::SOURCES[i]);
    if let Some(source) = source {
        review(data, Some(source));
    }
}
unsafe extern "C" fn card_install_clicked(button: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    let source = ui
        .dashboard
        .as_ref()
        .and_then(|d| d.cards.iter().position(|c| c.install == button))
        .map(|i| updates::SOURCES[i]);
    if let Some(source) = source {
        confirm(data, Some(source));
    }
}
unsafe extern "C" fn confirm_all_clicked(_: Widget, data: Widget) {
    confirm(data, None);
}
unsafe fn confirm(data: Widget, source: Option<&str>) {
    let ui = &mut *(data as *mut Ui);
    if ui.phase != Phase::Idle {
        return;
    }
    ui.targets = ui
        .results
        .iter()
        .filter(|r| r.eligible() && (source.is_none() || source == Some(r.source.as_str())))
        .map(|r| r.source.clone())
        .collect();
    if let Err(e) = updates::validate_targets(&ui.targets, &ui.results, &ui.config, updates::now())
    {
        ui.notice(&e);
        return;
    }
    if ui.confirmation.is_none() {
        ui.confirmation = Some(Confirmation {
            window: approval_dialog("", "", "Install now", data),
        });
    }
    let count: usize = ui
        .results
        .iter()
        .filter(|r| ui.targets.contains(&r.source))
        .map(|r| r.items.len())
        .sum();
    let dialog = ui.confirmation.as_ref().unwrap().window;
    adw_alert_dialog_set_heading(
        dialog,
        c(&format!(
            "Install {count} update{}?",
            if count == 1 { "" } else { "s" }
        ))
        .as_ptr(),
    );
    adw_alert_dialog_set_body(dialog,c(&format!("Eligible updates from {} will be installed. You can continue without opening the optional package review.",ui.targets.iter().map(|s|presentation::name(s)).collect::<Vec<_>>().join(", "))).as_ptr());
    ui.phase = Phase::Reviewing;
    ui.sensitive(false);
    adw_dialog_present(dialog, ui.window);
}

impl Ui {
    unsafe fn show_progress(&mut self, data: Widget) {
        if self.progress.is_null() {
            self.progress = adw_dialog_new();
            g_object_ref_sink(self.progress);
            adw_dialog_set_title(self.progress, c"Installing updates".as_ptr());
            adw_dialog_set_content_width(self.progress, 760);
            adw_dialog_set_content_height(self.progress, 480);
            let toolbar = adw_toolbar_view_new();
            adw_dialog_set_child(self.progress, toolbar);
            let header = adw_header_bar_new();
            adw_toolbar_view_add_top_bar(toolbar, header);
            self.cancel_button = button("Cancel update");
            connect(self.cancel_button, "clicked", cancel_clicked, data);
            adw_header_bar_pack_start(header, self.cancel_button);
            self.progress_close = button("Close");
            connect(self.progress_close, "clicked", progress_close_clicked, data);
            adw_header_bar_pack_end(header, self.progress_close);
            self.output = view();
            margins(self.output, 12);
            adw_toolbar_view_set_content(toolbar, scroll(self.output, 300));
        }
        gtk_widget_set_sensitive(self.cancel_button, 1);
        gtk_widget_set_sensitive(self.progress_close, 0);
        adw_dialog_set_can_close(self.progress, 0);
        adw_dialog_present(self.progress, self.window);
    }
}
unsafe extern "C" fn progress_close_clicked(_: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    if ui.phase != Phase::Installing {
        adw_dialog_close(ui.progress);
    }
}
unsafe extern "C" fn about_clicked(_: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    let dialog = adw_about_dialog_new();
    adw_about_dialog_set_application_name(dialog, c"Cachy Update Center".as_ptr());
    adw_about_dialog_set_application_icon(dialog, c"io.github.alvin.Cuc".as_ptr());
    adw_about_dialog_set_version(dialog, c(env!("CARGO_PKG_VERSION")).as_ptr());
    adw_about_dialog_set_comments(
        dialog,
        c"Native update dashboard for Pacman and Flatpak.".as_ptr(),
    );
    adw_dialog_present(dialog, ui.window);
}
unsafe fn group(page: Widget, title: &str, description: &str) -> Widget {
    let g = adw_preferences_group_new();
    adw_preferences_group_set_title(g, c(title).as_ptr());
    if !description.is_empty() {
        adw_preferences_group_set_description(g, c(description).as_ptr());
    }
    adw_preferences_page_add(page, g);
    g
}
unsafe fn switch_row(group: Widget, title: &str, subtitle: &str) -> Widget {
    let row = adw_switch_row_new();
    row_title(row, title);
    adw_action_row_set_subtitle(row, c(subtitle).as_ptr());
    adw_preferences_group_add(group, row);
    row
}
unsafe fn entry_row(group: Widget, title: &str) -> Widget {
    let row = adw_entry_row_new();
    row_title(row, title);
    adw_preferences_group_add(group, row);
    row
}
unsafe extern "C" fn show_settings(_: Widget, data: Widget) {
    let ui = &mut *(data as *mut Ui);
    if ui.phase != Phase::Idle {
        return;
    }
    if ui.settings.is_none() {
        let dialog = adw_preferences_dialog_new();
        g_object_ref_sink(dialog);
        adw_dialog_set_title(dialog, c"Cachy Update Center Settings".as_ptr());
        adw_dialog_set_content_width(dialog, 620);
        adw_dialog_set_content_height(dialog, 620);
        let page = adw_preferences_page_new();
        adw_preferences_dialog_add(dialog, page);
        let general = group(page, "Update behavior", "");
        let voice_enabled = switch_row(
            general,
            "Piper status announcements",
            "Speak a summary after each completed update check.",
        );
        let spoken = entry_row(general, "Spoken name");
        let mut spins = vec![];
        for (title, subtitle, min, max, step) in [
            (
                "Command timeout",
                "Maximum time, in seconds, for each update-source check.",
                1.,
                900.,
                1.,
            ),
            (
                "Installation timeout",
                "Maximum runtime, in seconds, for each selected update source.",
                60.,
                14400.,
                60.,
            ),
            (
                "Result freshness",
                "Refresh before installation when results are older than this many minutes.",
                1.,
                1440.,
                1.,
            ),
            (
                "Minimum free disk space",
                "Block installation below this many MiB of free space.",
                128.,
                65536.,
                128.,
            ),
        ] {
            let row = adw_spin_row_new_with_range(min, max, step);
            row_title(row, title);
            adw_action_row_set_subtitle(row, c(subtitle).as_ptr());
            adw_preferences_group_add(general, row);
            spins.push(row);
        }
        let notifications = switch_row(
            general,
            "Desktop notifications",
            "Notify when checks or installations finish or need attention.",
        );
        let sources = group(
            page,
            "Update sources",
            "Disabled sources are skipped and shown as disabled on the dashboard.",
        );
        let pacman = switch_row(sources, "Pacman", "Check and install updates from Pacman.");
        let flatpak = switch_row(
            sources,
            "Flatpak",
            "Check and install updates from Flatpak.",
        );
        let auth = group(page, "Authentication", "");
        let auth_row = adw_action_row_new();
        row_title(auth_row, "Native Polkit integration");
        let ready = updates::helper_ready(std::path::Path::new(updates::HELPER));
        adw_action_row_set_subtitle(
            auth_row,
            c(&match &ready {
                Ok(()) => "Fixed-command system helper is ready.".into(),
                Err(e) => e.clone(),
            })
            .as_ptr(),
        );
        adw_action_row_add_prefix(
            auth_row,
            gtk_image_new_from_icon_name(
                c(if ready.is_ok() {
                    "emblem-ok-symbolic"
                } else {
                    "dialog-warning-symbolic"
                })
                .as_ptr(),
            ),
        );
        adw_preferences_group_add(auth, auth_row);
        let voice_group = group(page, "Piper voice", "");
        let voices = voice::discover_models(
            std::path::Path::new("/usr/share/piper-voices"),
            &ui.config.piper_model,
        );
        let labels: Vec<_> = voices.iter().map(|(label, _)| c(label)).collect();
        let mut pointers: Vec<_> = labels.iter().map(|s| s.as_ptr()).collect();
        pointers.push(std::ptr::null());
        let model = gtk_string_list_new(pointers.as_ptr());
        let combo = adw_combo_row_new();
        row_title(combo, "Update voice");
        adw_action_row_set_subtitle(
            combo,
            c"Choose from the Piper voices installed on this computer.".as_ptr(),
        );
        adw_combo_row_set_model(combo, model);
        g_object_unref(model);
        adw_preferences_group_add(voice_group, combo);
        let sample_row = adw_action_row_new();
        row_title(sample_row, "Voice sample");
        adw_action_row_set_subtitle(
            sample_row,
            c"Play a short sample with the voice currently selected above.".as_ptr(),
        );
        let sample = button("Play sample");
        gtk_widget_set_valign(sample, 3);
        connect(sample, "clicked", voice_sample_clicked, data);
        adw_action_row_add_suffix(sample_row, sample);
        adw_action_row_set_activatable_widget(sample_row, sample);
        adw_preferences_group_add(voice_group, sample_row);
        let locations = group(page, "File locations", "");
        let preview = entry_row(locations, "Voice preview directory");
        let log = entry_row(locations, "Log file");
        let custom_model = entry_row(locations, "Custom Piper model path");
        let actions = group(page, "", "");
        let error = adw_action_row_new();
        row_title(error, "");
        gtk_widget_set_visible(error, 0);
        adw_preferences_group_add(actions, error);
        let save_row = adw_action_row_new();
        row_title(save_row, "Apply preferences");
        let save = button("Save settings");
        css(save, "suggested-action");
        gtk_widget_set_valign(save, 3);
        connect(save, "clicked", save_settings, data);
        adw_action_row_add_suffix(save_row, save);
        adw_action_row_set_activatable_widget(save_row, save);
        adw_preferences_group_add(actions, save_row);
        ui.settings = Some(Settings {
            window: dialog,
            toggles: vec![pacman, flatpak, voice_enabled, notifications],
            entries: vec![custom_model, preview, log, spoken],
            spins,
            voice_combo: combo,
            voice_paths: voices.into_iter().map(|(_, p)| p).collect(),
            error,
        });
    }
    let s = ui.settings.as_ref().unwrap();
    let cfg = &ui.config;
    for (w, value) in s.toggles.iter().zip([
        cfg.enabled_sources["pacman"],
        cfg.enabled_sources["flatpak"],
        cfg.voice_enabled,
        cfg.notifications_enabled,
    ]) {
        adw_switch_row_set_active(*w, value as i32);
    }
    // Leave the custom override blank so selecting an installed voice takes effect.
    for (w, value) in s.entries.iter().zip([
        "".into(),
        cfg.preview_dir.clone(),
        cfg.log_file.clone(),
        cfg.spoken_name.clone(),
    ]) {
        gtk_editable_set_text(*w, c(&value).as_ptr());
    }
    for (w, value) in s.spins.iter().zip([
        cfg.command_timeout,
        cfg.installation_timeout,
        cfg.max_result_age_minutes,
        cfg.minimum_free_space_mb,
    ]) {
        adw_spin_row_set_value(*w, value as f64);
    }
    let selected = s
        .voice_paths
        .iter()
        .position(|p| p == &cfg.piper_model)
        .unwrap_or(0);
    adw_combo_row_set_selected(s.voice_combo, selected as u32);
    if let Some(e) = &ui.config_error {
        row_title(s.error, e);
        gtk_widget_set_visible(s.error, 1);
    } else {
        gtk_widget_set_visible(s.error, 0);
    }
    adw_dialog_present(s.window, ui.window);
}
unsafe fn selected_voice(s: &Settings) -> String {
    let custom = entry_text(s.entries[0]);
    if !custom.trim().is_empty() {
        custom
    } else {
        s.voice_paths
            .get(adw_combo_row_get_selected(s.voice_combo) as usize)
            .cloned()
            .unwrap_or_else(|| config::MODEL.into())
    }
}
unsafe extern "C" fn voice_sample_clicked(_: Widget, data: Widget) {
    let ui = &*(data as *mut Ui);
    let s = ui.settings.as_ref().unwrap();
    let model = selected_voice(s);
    if !std::path::Path::new(&model).is_file()
        || !cachyos_update_center::process::exists("piper-tts")
    {
        row_title(
            s.error,
            "Voice sample could not play: the selected Piper voice or piper-tts is unavailable.",
        );
        gtk_widget_set_visible(s.error, 1);
        return;
    }
    let c = Config {
        voice_enabled: true,
        piper_model: model,
        ..ui.config.clone()
    };
    voice::speak(c, "Hello. This is your selected update voice.".into());
}
unsafe extern "C" fn save_settings(_: Widget, data: Widget) {
    let ui = &mut *(data as *mut Ui);
    let s = ui.settings.as_ref().unwrap();
    let mut cfg = ui.config.clone();
    cfg.enabled_sources = updates::SOURCES
        .iter()
        .enumerate()
        .map(|(i, source)| {
            (
                (*source).into(),
                adw_switch_row_get_active(s.toggles[i]) != 0,
            )
        })
        .collect();
    cfg.voice_enabled = adw_switch_row_get_active(s.toggles[2]) != 0;
    cfg.notifications_enabled = adw_switch_row_get_active(s.toggles[3]) != 0;
    cfg.piper_model = selected_voice(s);
    cfg.preview_dir = entry_text(s.entries[1]);
    cfg.log_file = entry_text(s.entries[2]);
    cfg.spoken_name = entry_text(s.entries[3]);
    cfg.command_timeout = adw_spin_row_get_value(s.spins[0]) as u64;
    cfg.installation_timeout = adw_spin_row_get_value(s.spins[1]) as u64;
    cfg.max_result_age_minutes = adw_spin_row_get_value(s.spins[2]) as u64;
    cfg.minimum_free_space_mb = adw_spin_row_get_value(s.spins[3]) as u64;
    match cfg.save(&config::config_path()) {
        Ok(()) => {
            ui.config = cfg;
            ui.config_error = None;
            ui.results.clear();
            adw_dialog_close(s.window);
            ui.refresh(false);
        }
        Err(e) => {
            row_title(s.error, &e);
            gtk_widget_set_visible(s.error, 1);
        }
    }
}
