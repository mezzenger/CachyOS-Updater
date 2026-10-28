//! Faithful GTK/libadwaita reconstruction of the 0.6 dashboard.
use super::gtk::*;
use cachyos_update_center::{
    presentation,
    updates::{self, UpdateResult},
};

pub struct Card {
    pub status: Widget,
    pub count: Widget,
    pub detail: Widget,
    pub footer: Widget,
    pub review: Widget,
    pub install: Widget,
}
pub struct Dashboard {
    pub headline: Widget,
    pub subtitle: Widget,
    pub badge: Widget,
    pub summary: Widget,
    pub notice: Widget,
    pub last_checked: Widget,
    pub retry: Widget,
    pub review_all: Widget,
    pub install_all: Widget,
    pub voice: Widget,
    pub wave: super::speech_wave::SpeechWave,
    pub cards: Vec<Card>,
    pub controls: Vec<Widget>,
}
impl Dashboard {
    pub unsafe fn new(app: Widget, data: Widget) -> (Widget, Self) {
        unsafe {
            let window = adw_application_window_new(app);
            gtk_window_set_title(window, c"Cachy Update Center".as_ptr());
            gtk_widget_set_size_request(window, 460, 480);
            let toolbar = adw_toolbar_view_new();
            adw_application_window_set_content(window, toolbar);
            let header = adw_header_bar_new();
            adw_toolbar_view_add_top_bar(toolbar, header);
            let voice = icon_button(
                "audio-volume-high-symbolic",
                "Repeat update status with Piper",
            );
            let settings = icon_button("emblem-system-symbolic", "Settings (Ctrl+,)");
            let about = icon_button("help-about-symbolic", "About Cachy Update Center");
            for (w, callback) in [
                (
                    voice,
                    super::announce_clicked as unsafe extern "C" fn(Widget, Widget),
                ),
                (settings, super::show_settings),
                (about, super::about_clicked),
            ] {
                adw_header_bar_pack_end(header, w);
                connect(w, "clicked", callback, data);
            }
            let scroller = gtk_scrolled_window_new();
            gtk_scrolled_window_set_policy(scroller, 2, 1);
            adw_toolbar_view_set_content(toolbar, scroller);
            let clamp = adw_clamp_new();
            adw_clamp_set_maximum_size(clamp, 1040);
            adw_clamp_set_tightening_threshold(clamp, 820);
            gtk_scrolled_window_set_child(scroller, clamp);
            let page = gtk_box_new(1, 16);
            margins(page, 20);
            gtk_widget_set_margin_bottom(page, 24);
            adw_clamp_set_child(clamp, page);
            let hero = gtk_box_new(0, 14);
            css(hero, "overview-panel");
            gtk_box_append(page, hero);
            let well = gtk_box_new(0, 0);
            css(well, "overview-icon-well");
            gtk_widget_set_valign(well, 3);
            gtk_box_append(well, app_icon());
            gtk_box_append(hero, well);
            let hero_text = gtk_box_new(1, 4);
            let headline = label("Checking your system");
            css(headline, "title-1");
            let subtitle = label("Pacman and Flatpak");
            css(subtitle, "subtitle");
            gtk_box_append(hero_text, headline);
            gtk_box_append(hero_text, subtitle);
            gtk_box_append(hero, hero_text);
            let badge = label("Checking");
            css(badge, "overview-badge");
            css(badge, "overview-checking");
            gtk_widget_set_valign(badge, 3);
            gtk_box_append(hero, badge);
            let actions = gtk_box_new(0, 8);
            gtk_widget_set_hexpand(actions, 1);
            gtk_widget_set_halign(actions, 2);
            gtk_box_append(hero, actions);
            let refresh = icon_button("view-refresh-symbolic", "Refresh update status (Ctrl+R)");
            let review_all =
                icon_button("view-list-symbolic", "Review all pending packages (Ctrl+U)");
            let install_all = icon_button(
                "system-software-install-symbolic",
                "Install all eligible updates",
            );
            css(install_all, "primary-action");
            for (w, cb) in [
                (
                    refresh,
                    super::check_clicked as unsafe extern "C" fn(Widget, Widget),
                ),
                (review_all, super::review_all_clicked),
                (install_all, super::confirm_all_clicked),
            ] {
                css(w, "circular");
                gtk_widget_set_valign(w, 3);
                gtk_box_append(actions, w);
                connect(w, "clicked", cb, data);
            }
            gtk_widget_set_visible(review_all, 0);
            gtk_widget_set_visible(install_all, 0);
            let workflow = gtk_box_new(0, 10);
            css(workflow, "workflow");
            for (number, title) in [("1", "Check"), ("2", "Review (optional)"), ("3", "Install")] {
                let step = gtk_box_new(0, 6);
                let n = label(number);
                css(n, "step-number");
                gtk_label_set_xalign(n, 0.5);
                let t = label(title);
                css(t, "dim-label");
                gtk_box_append(step, n);
                gtk_box_append(step, t);
                gtk_box_append(workflow, step);
            }
            gtk_box_append(page, workflow);
            let wave = super::speech_wave::SpeechWave::new();
            gtk_box_append(page, wave.widget);
            let notice = label("");
            css(notice, "notice");
            gtk_label_set_wrap(notice, 1);
            gtk_label_set_selectable(notice, 1);
            gtk_widget_set_visible(notice, 0);
            gtk_box_append(page, notice);
            let section = gtk_box_new(0, 12);
            css(section, "section-header");
            let source_text = gtk_box_new(1, 2);
            let title = label("Update sources");
            css(title, "title-3");
            let summary = label("Checking Pacman and Flatpak");
            css(summary, "dim-label");
            gtk_box_append(source_text, title);
            gtk_box_append(source_text, summary);
            gtk_box_append(section, source_text);
            let retry = button("Retry failed");
            gtk_widget_set_hexpand(retry, 1);
            gtk_widget_set_halign(retry, 2);
            gtk_widget_set_visible(retry, 0);
            gtk_box_append(section, retry);
            connect(retry, "clicked", super::retry_clicked, data);
            gtk_box_append(page, section);
            let grid = gtk_grid_new();
            gtk_grid_set_column_homogeneous(grid, 1);
            gtk_grid_set_row_homogeneous(grid, 1);
            gtk_grid_set_column_spacing(grid, 8);
            gtk_widget_set_valign(grid, 1);
            let mut cards = vec![];
            for (i, source) in updates::SOURCES.iter().enumerate() {
                let card = gtk_box_new(1, 10);
                css(card, "source-card");
                gtk_widget_set_hexpand(card, 1);
                gtk_widget_set_size_request(card, 190, -1);
                margins(card, 3);
                let top = gtk_box_new(0, 10);
                let well = gtk_box_new(0, 0);
                css(well, "source-icon-well");
                let icon = source_icon(source);
                gtk_image_set_pixel_size(icon, 28);
                gtk_box_append(well, icon);
                gtk_box_append(top, well);
                let titles = gtk_box_new(1, 2);
                let title = label(presentation::name(source));
                css(title, "title-4");
                let status = label("Checking…");
                css(status, "caption");
                css(status, "status-chip");
                gtk_box_append(titles, title);
                gtk_box_append(titles, status);
                gtk_box_append(top, titles);
                let count = label("—");
                gtk_label_set_xalign(count, 1.0);
                gtk_widget_set_hexpand(count, 1);
                for class in ["numeric", "count", "count-pill"] {
                    css(count, class);
                }
                gtk_box_append(top, count);
                gtk_box_append(card, top);
                let detail = label("Contacting this update source.");
                gtk_label_set_wrap(detail, 1);
                css(detail, "dim-label");
                css(detail, "source-detail");
                gtk_box_append(card, detail);
                let footer = gtk_box_new(0, 8);
                gtk_widget_set_halign(footer, 2);
                let review = icon_button("view-list-symbolic", "Review packages");
                let install =
                    icon_button("system-software-install-symbolic", "Install this source");
                css(install, "primary-action");
                for (w, cb) in [
                    (
                        review,
                        super::card_review_clicked as unsafe extern "C" fn(Widget, Widget),
                    ),
                    (install, super::card_install_clicked),
                ] {
                    css(w, "circular");
                    gtk_box_append(footer, w);
                    connect(w, "clicked", cb, data);
                }
                gtk_box_append(card, footer);
                gtk_widget_set_visible(footer, 0);
                gtk_grid_attach(grid, card, i as i32, 0, 1, 1);
                cards.push(Card {
                    status,
                    count,
                    detail,
                    footer,
                    review,
                    install,
                });
            }
            gtk_box_append(page, grid);
            let footer = gtk_box_new(0, 8);
            css(footer, "footer");
            let last_checked = label("Not checked yet");
            css(last_checked, "dim-label");
            gtk_box_append(footer, last_checked);
            let version = label(&format!("v{}", env!("CARGO_PKG_VERSION")));
            css(version, "dim-label");
            gtk_box_append(footer, version);
            let export = button("Export diagnostics");
            gtk_widget_set_tooltip_text(
                export,
                c"Create a privacy-safe support bundle (Ctrl+D)".as_ptr(),
            );
            gtk_box_append(footer, export);
            connect(export, "clicked", super::export_clicked, data);
            adw_toolbar_view_add_bottom_bar(toolbar, footer);
            let provider = gtk_css_provider_new();
            let style = include_str!("dashboard.css");
            gtk_css_provider_load_from_data(provider, c(style).as_ptr(), style.len() as isize);
            gtk_style_context_add_provider_for_display(gdk_display_get_default(), provider, 600);
            g_object_unref(provider);
            (
                window,
                Self {
                    headline,
                    subtitle,
                    badge,
                    summary,
                    notice,
                    last_checked,
                    retry,
                    review_all,
                    install_all,
                    voice,
                    wave,
                    cards,
                    controls: vec![
                        refresh,
                        review_all,
                        install_all,
                        retry,
                        voice,
                        settings,
                        export,
                    ],
                },
            )
        }
    }
    pub unsafe fn checking(&self) {
        unsafe {
            set_label(self.headline, "Checking your system");
            set_label(self.subtitle, "Pacman and Flatpak");
            set_label(self.badge, "Checking");
            gtk_widget_set_visible(self.badge, 1);
            class_status(
                self.badge,
                "overview-checking",
                &["overview-current", "overview-ready", "overview-error"],
            );
            gtk_widget_set_visible(self.notice, 0);
            for card in &self.cards {
                card.render(None);
            }
        }
    }
    pub unsafe fn render(&self, results: &[UpdateResult], reboot: bool, voice_enabled: bool) {
        unsafe {
            let overview = presentation::overview(results, reboot);
            set_label(self.headline, &overview.headline);
            set_label(self.subtitle, &overview.subtitle);
            set_label(self.summary, &overview.summary);
            set_label(self.badge, overview.badge);
            class_status(
                self.badge,
                overview.class,
                &[
                    "overview-current",
                    "overview-ready",
                    "overview-error",
                    "overview-checking",
                ],
            );
            gtk_widget_set_visible(self.badge, (!overview.badge.is_empty()) as i32);
            let failed = results
                .iter()
                .any(|r| r.enabled && r.available_on_system && r.error.is_some());
            gtk_widget_set_visible(self.retry, failed as i32);
            let available = results.iter().any(|r| r.eligible());
            gtk_widget_set_visible(self.review_all, available as i32);
            gtk_widget_set_visible(self.install_all, available as i32);
            for (i, source) in updates::SOURCES.iter().enumerate() {
                self.cards[i].render(results.iter().find(|r| r.source == *source));
            }
            gtk_button_set_icon_name(
                self.voice,
                c(if voice_enabled {
                    "audio-volume-high-symbolic"
                } else {
                    "audio-volume-muted-symbolic"
                })
                .as_ptr(),
            );
            let time = updates::now() as libc::time_t;
            let mut local = std::mem::MaybeUninit::<libc::tm>::uninit();
            if !libc::localtime_r(&time, local.as_mut_ptr()).is_null() {
                let t = local.assume_init();
                set_label(
                    self.last_checked,
                    &format!("Last checked {:02}:{:02}", t.tm_hour, t.tm_min),
                );
            }
        }
    }
    pub unsafe fn sensitive(&self, enabled: bool, results: &[UpdateResult]) {
        unsafe {
            for w in &self.controls {
                gtk_widget_set_sensitive(*w, enabled as i32);
            }
            for (i, source) in updates::SOURCES.iter().enumerate() {
                let eligible =
                    enabled && results.iter().any(|r| r.source == *source && r.eligible());
                gtk_widget_set_sensitive(self.cards[i].review, eligible as i32);
                gtk_widget_set_sensitive(self.cards[i].install, eligible as i32);
            }
        }
    }
}
impl Card {
    unsafe fn render(&self, result: Option<&UpdateResult>) {
        unsafe {
            let p = presentation::card(result);
            set_label(self.status, &p.status);
            set_label(self.count, &p.count);
            set_label(self.detail, &p.detail);
            class_status(
                self.status,
                p.class,
                &["status-ok", "status-warn", "status-error"],
            );
            gtk_widget_set_visible(self.footer, p.actions as i32);
        }
    }
}
