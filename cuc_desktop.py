#!/usr/bin/env python3
"""Native GNOME dashboard for the CachyOS Update Center."""

from __future__ import annotations

import os
import json
import logging
import signal
import shlex
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import cuc_backend as cuc  # noqa: E402


APP_ID = "io.github.alvin.Cuc"
PIPER_VOICE_ROOT = Path("/usr/share/piper-voices")
SOURCE_ORDER = ("pacman", "aur", "flatpak", "snap")
SOURCE_ICONS = {
    "pacman": "system-software-update-symbolic",
    "aur": "applications-system-symbolic",
    "flatpak": "package-x-generic-symbolic",
    "snap": "system-run-symbolic",
}
POLKIT_HELPER = Path("/usr/lib/cachyos-update-center/cuc-privileged")


def secure_terminal_command(command: list[str]) -> list[str] | None:
    """Wrap an interactive command without invoking a shell or carrying credentials."""
    if shutil.which("kgx"):
        return ["kgx", "--wait", "--", *command]
    if shutil.which("gnome-terminal"):
        return ["gnome-terminal", "--wait", "--", *command]
    if shutil.which("xterm"):
        return ["xterm", "-T", "CachyOS AUR Update", "-e", *command]
    return None


class SourceCard(Gtk.Box):
    """One compact update-source summary card."""

    def __init__(self, source: str, on_review, on_install) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.source = source
        self.on_review = on_review
        self.on_install = on_install
        self.add_css_class("source-card")
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_size_request(280, -1)
        self.set_margin_top(3)
        self.set_margin_bottom(3)
        self.set_margin_start(3)
        self.set_margin_end(3)

        top = Gtk.Box(spacing=10)
        icon_well = Gtk.Box()
        icon_well.add_css_class("source-icon-well")
        icon = Gtk.Image.new_from_icon_name(SOURCE_ICONS[source])
        icon.set_pixel_size(22)
        icon.add_css_class("source-icon")
        icon_well.append(icon)
        top.append(icon_well)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("title-4")
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("caption")
        self.status.add_css_class("status-chip")
        title_box.append(self.title)
        title_box.append(self.status)
        top.append(title_box)

        self.count = Gtk.Label(xalign=1)
        self.count.set_hexpand(True)
        self.count.add_css_class("numeric")
        self.count.add_css_class("count")
        self.count.add_css_class("count-pill")
        top.append(self.count)
        self.append(top)

        self.detail = Gtk.Label(xalign=0, wrap=True)
        self.detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.detail.add_css_class("dim-label")
        self.detail.add_css_class("source-detail")
        self.append(self.detail)

        self.items = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.items.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.items.add_css_class("package-list")
        self.items.add_css_class("package-preview")
        self.append(self.items)

        self.all_packages: list[str] = []
        self.has_updates = False
        self.more_packages = Gtk.LinkButton.new_with_label("about:blank", "")
        self.more_packages.set_halign(Gtk.Align.START)
        self.more_packages.add_css_class("more-link")
        self.more_packages.set_visible(False)
        self.more_packages.connect("activate-link", self.show_all_packages)
        self.append(self.more_packages)

        self.footer = Gtk.Box(spacing=8)
        self.footer.set_halign(Gtk.Align.END)
        self.review_button = Gtk.Button(label="Review")
        self.review_button.set_icon_name("view-list-symbolic")
        self.review_button.connect("clicked", lambda *_: self.on_review(self.source))
        self.footer.append(self.review_button)
        self.install_button = Gtk.Button(label="Install now")
        self.install_button.set_icon_name("system-software-install-symbolic")
        self.install_button.add_css_class("primary-action")
        self.install_button.connect("clicked", lambda *_: self.on_install(self.source))
        self.footer.append(self.install_button)
        self.append(self.footer)

        self.show_checking()

    def show_checking(self) -> None:
        self.title.set_text(self.source.capitalize() if self.source != "aur" else "AUR")
        self.status.set_text("Checking…")
        self.status.remove_css_class("status-ok")
        self.status.remove_css_class("status-warn")
        self.status.remove_css_class("status-error")
        self.count.set_text("—")
        self.detail.set_text("Contacting this update source.")
        self.items.set_text("")
        self.items.set_visible(False)
        self.all_packages = []
        self.has_updates = False
        self.more_packages.set_visible(False)
        self.footer.set_visible(False)
        self.review_button.set_sensitive(False)
        self.install_button.set_sensitive(False)

    def update(self, result: cuc.UpdateResult) -> None:
        self.title.set_text(result.display_name)
        self.status.remove_css_class("status-ok")
        self.status.remove_css_class("status-warn")
        self.status.remove_css_class("status-error")
        self.items.set_text("")
        self.items.set_visible(False)
        self.all_packages = []
        self.has_updates = False
        self.more_packages.set_visible(False)
        self.footer.set_visible(False)

        if not result.enabled:
            self.status.set_text("Disabled")
            self.status.add_css_class("status-ok")
            self.count.set_text("—")
            self.detail.set_text("Enable this source in Settings to include it in update checks.")
            self.review_button.set_sensitive(False)
            self.install_button.set_sensitive(False)
            return
        if not result.available_on_system:
            self.status.set_text("Not installed")
            self.status.add_css_class("status-error")
            self.count.set_text("—")
            self.detail.set_text(result.error or result.command_hint)
            self.review_button.set_sensitive(False)
            self.install_button.set_sensitive(False)
            return
        if result.error:
            self.status.set_text("Check failed")
            self.status.add_css_class("status-error")
            self.count.set_text("!")
            self.detail.set_text(result.error)
            self.review_button.set_sensitive(False)
            self.install_button.set_sensitive(False)
            return
        if result.available:
            noun = "update" if result.count == 1 else "updates"
            if not result.install_allowed:
                self.status.set_text("Safety check blocked")
                self.status.add_css_class("status-error")
            elif result.security_warnings:
                self.status.set_text("Review required")
                self.status.add_css_class("status-warn")
            else:
                self.status.set_text(f"{result.count} {noun} ready")
                self.status.add_css_class("status-warn")
            self.count.set_text(str(result.count))
            if result.security_warnings:
                self.detail.set_text(result.security_warnings[0])
            else:
                self.detail.set_text("Review the items below, then install when ready.")
            preview = result.items[:5]
            self.all_packages = result.items
            self.has_updates = True
            if result.count > len(preview):
                remaining = result.count - len(preview)
                self.more_packages.set_label(f"Show {remaining} more")
                self.more_packages.set_visible(True)
            self.items.set_text("\n".join(preview))
            self.items.set_visible(True)
            self.footer.set_visible(True)
            self.review_button.set_label("Review safety" if not result.install_allowed else "Review")
            self.review_button.set_sensitive(True)
            self.install_button.set_sensitive(result.install_allowed)
            return

        self.status.set_text("Up to date")
        self.status.add_css_class("status-ok")
        self.count.set_text("0")
        self.detail.set_text("Nothing pending from this source.")
        self.review_button.set_sensitive(False)
        self.install_button.set_sensitive(False)

    def show_all_packages(self, _button: Gtk.LinkButton) -> bool:
        root = self.get_root()
        dialog = Adw.AlertDialog(
            heading=f"{self.source.upper() if self.source == 'aur' else self.source.capitalize()} updates",
            body=f"{len(self.all_packages)} packages available",
        )
        dialog.add_response("close", "Close")
        dialog.add_response("review", "Review selection")
        dialog.set_response_appearance("review", Adw.ResponseAppearance.SUGGESTED)
        result_can_install = bool(self.has_updates)
        dialog.set_response_enabled("review", result_can_install)
        dialog.set_default_response("review")
        dialog.set_close_response("close")

        def respond(_current: Adw.AlertDialog, response: str) -> None:
            if response == "review" and self.has_updates:
                self.on_review(self.source)

        dialog.connect("response", respond)

        packages = Gtk.TextView()
        packages.set_editable(False)
        packages.set_cursor_visible(False)
        packages.set_monospace(True)
        packages.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        packages.get_buffer().set_text("\n".join(self.all_packages))
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(packages)
        scroll.set_size_request(600, 420)
        dialog.set_extra_child(scroll)
        dialog.present(root if isinstance(root, Gtk.Widget) else None)
        return True


class SettingsWindow(Adw.PreferencesDialog):
    """Editable front end for CUC's persistent configuration."""

    def __init__(self, config: cuc.Config, on_saved) -> None:
        super().__init__(title="Cachy Update Center Settings")
        self.set_content_width(620)
        self.set_content_height(620)
        self.on_saved = on_saved
        self.current_config = config

        page = Adw.PreferencesPage(title="Settings", icon_name="emblem-system-symbolic")
        self.add(page)

        general = Adw.PreferencesGroup(title="Update behavior")
        page.add(general)
        self.voice_enabled = Adw.SwitchRow(
            title="Piper status announcements",
            subtitle="Speak a summary after each completed update check.",
        )
        self.voice_enabled.set_active(config.voice_enabled)
        general.add(self.voice_enabled)

        self.spoken_name = Adw.EntryRow(title="Spoken name")
        self.spoken_name.set_text(config.spoken_name)
        general.add(self.spoken_name)

        self.aur_helpers = ("auto", *cuc.SUPPORTED_AUR_HELPERS)
        self.aur_helper = Adw.ComboRow(
            title="AUR helper",
            subtitle="Choose the helper used for AUR checks and installs.",
            model=Gtk.StringList.new(self.aur_helpers),
        )
        self.aur_helper.set_selected(self.aur_helpers.index(config.aur_helper))
        general.add(self.aur_helper)

        self.timeout = Adw.SpinRow.new_with_range(1, 900, 1)
        self.timeout.set_title("Command timeout")
        self.timeout.set_subtitle("Maximum time, in seconds, for each update-source check.")
        self.timeout.set_value(config.command_timeout)
        general.add(self.timeout)

        self.installation_timeout = Adw.SpinRow.new_with_range(60, 14400, 60)
        self.installation_timeout.set_title("Installation timeout")
        self.installation_timeout.set_subtitle(
            "Maximum runtime, in seconds, for each selected update source."
        )
        self.installation_timeout.set_value(config.installation_timeout)
        general.add(self.installation_timeout)

        self.max_result_age = Adw.SpinRow.new_with_range(1, 1440, 1)
        self.max_result_age.set_title("Result freshness")
        self.max_result_age.set_subtitle(
            "Refresh before installation when results are older than this many minutes."
        )
        self.max_result_age.set_value(config.max_result_age_minutes)
        general.add(self.max_result_age)

        self.minimum_free_space = Adw.SpinRow.new_with_range(128, 65536, 128)
        self.minimum_free_space.set_title("Minimum free disk space")
        self.minimum_free_space.set_subtitle(
            "Block installation below this many MiB of free space."
        )
        self.minimum_free_space.set_value(config.minimum_free_space_mb)
        general.add(self.minimum_free_space)

        self.notifications_enabled = Adw.SwitchRow(
            title="Desktop notifications",
            subtitle="Notify when checks or installations finish or need attention.",
        )
        self.notifications_enabled.set_active(config.notifications_enabled)
        general.add(self.notifications_enabled)

        sources = Adw.PreferencesGroup(
            title="Update sources",
            description="Disabled sources are skipped and shown as disabled on the dashboard.",
        )
        page.add(sources)
        self.source_switches: dict[str, Adw.SwitchRow] = {}
        for source in SOURCE_ORDER:
            row = Adw.SwitchRow(
                title=cuc.SOURCE_DISPLAY_NAMES[source],
                subtitle=f"Check and install updates from {cuc.SOURCE_DISPLAY_NAMES[source]}.",
            )
            row.set_active(config.enabled_sources.get(source, True))
            sources.add(row)
            self.source_switches[source] = row

        polkit = cuc.check_polkit_readiness()
        integration = Adw.PreferencesGroup(title="Authentication")
        page.add(integration)
        polkit_row = Adw.ActionRow(
            title="Native Polkit integration",
            subtitle=polkit.message,
        )
        polkit_row.add_prefix(
            Gtk.Image.new_from_icon_name(
                "emblem-ok-symbolic" if polkit.ready else "dialog-warning-symbolic"
            )
        )
        integration.add(polkit_row)

        voice = Adw.PreferencesGroup(title="Piper voice")
        page.add(voice)
        self.voice_labels, self.voice_model_paths = self.discover_voice_models(config.piper_model)
        self.piper_voice = Adw.ComboRow(
            title="Update voice",
            subtitle="Choose from the Piper voices installed on this computer.",
            model=Gtk.StringList.new(self.voice_labels),
        )
        self.piper_voice.set_selected(self.voice_model_paths.index(config.piper_model))
        voice.add(self.piper_voice)
        sample_row = Adw.ActionRow(
            title="Voice sample",
            subtitle="Play a short sample with the voice currently selected above.",
        )
        self.sample_button = Gtk.Button(label="Play sample")
        self.sample_button.set_icon_name("media-playback-start-symbolic")
        self.sample_button.set_valign(Gtk.Align.CENTER)
        self.sample_button.connect("clicked", lambda *_: self.play_sample())
        sample_row.add_suffix(self.sample_button)
        sample_row.set_activatable_widget(self.sample_button)
        voice.add(sample_row)

        storage = Adw.PreferencesGroup(title="Storage")
        page.add(storage)
        self.preview_dir = Adw.EntryRow(title="Preview cache directory")
        self.preview_dir.set_text(config.preview_dir)
        storage.add(self.preview_dir)
        self.log_file = Adw.EntryRow(title="Log file")
        self.log_file.set_text(config.log_file)
        storage.add(self.log_file)

        advisories = Adw.PreferencesGroup(
            title="AUR security advisories",
            description=(
                "Optional automatic security data from a trusted publisher. "
                "Configure all three signed-feed fields together, or leave all three blank."
            ),
        )
        page.add(advisories)
        self.advisory_cache_file = Adw.EntryRow(title="Authenticated advisory cache")
        self.advisory_cache_file.set_text(config.advisory_cache_file)
        self.advisory_cache_file.set_tooltip_text(
            "Local cache used for verified AUR security advisory data."
        )
        advisories.add(self.advisory_cache_file)

        def advisory_entry(title: str, subtitle: str) -> tuple[Adw.ActionRow, Gtk.Entry]:
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_valign(Gtk.Align.CENTER)
            entry.set_width_chars(34)
            row.add_suffix(entry)
            row.set_activatable_widget(entry)
            return row, entry

        feed_row, self.advisory_feed_url = advisory_entry(
            "Signed advisory feed URL",
            "HTTPS address of the advisory JSON from a trusted publisher.",
        )
        self.advisory_feed_url.set_text(config.advisory_feed_url)
        advisories.add(feed_row)
        signature_row, self.advisory_signature_url = advisory_entry(
            "Advisory signature URL",
            "HTTPS address of the detached Ed25519 signature for the feed.",
        )
        self.advisory_signature_url.set_text(config.advisory_signature_url)
        advisories.add(signature_row)
        key_row, self.advisory_public_key_file = advisory_entry(
            "Advisory public key file",
            "Absolute path to the trusted Ed25519 public key used for verification.",
        )
        self.advisory_public_key_file.set_text(config.advisory_public_key_file)
        advisories.add(key_row)
        self.advisory_refresh_hours = Adw.SpinRow.new_with_range(1, 168, 1)
        self.advisory_refresh_hours.set_title("Advisory refresh interval")
        self.advisory_refresh_hours.set_subtitle("Hours between signed advisory refresh attempts.")
        self.advisory_refresh_hours.set_value(config.advisory_refresh_hours)
        advisories.add(self.advisory_refresh_hours)

        action_group = Adw.PreferencesGroup()
        page.add(action_group)
        save_row = Adw.ActionRow(title="Apply settings")
        save_button = Gtk.Button(label="Save settings")
        save_button.add_css_class("primary-action")
        save_button.set_valign(Gtk.Align.CENTER)
        save_button.connect("clicked", lambda *_: self.save())
        save_row.add_suffix(save_button)
        save_row.set_activatable_widget(save_button)
        action_group.add(save_row)
        self.error_row = Adw.ActionRow()
        self.error_row.add_css_class("error-row")
        self.error_row.set_visible(False)
        action_group.add(self.error_row)

    @staticmethod
    def discover_voice_models(current_model: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        models = sorted(PIPER_VOICE_ROOT.glob("**/*.onnx")) if PIPER_VOICE_ROOT.is_dir() else []
        paths = [str(model) for model in models]
        if current_model not in paths:
            paths.append(current_model)

        def label(model_path: str) -> str:
            path = Path(model_path)
            try:
                relative = path.relative_to(PIPER_VOICE_ROOT)
                locale = {
                    "en_GB": "English (UK)",
                    "en_US": "English (US)",
                }.get(relative.parts[1], relative.parts[1].replace("_", " "))
                voice = relative.parts[2].replace("_", " ").title()
                quality = relative.parts[3].title()
                return f"{voice} · {locale} · {quality}"
            except (IndexError, ValueError):
                return f"Current voice · {path.stem.replace('_', ' ').title()}"

        return tuple(label(path) for path in paths), tuple(paths)

    def selected_voice_model(self) -> str:
        return self.voice_model_paths[self.piper_voice.get_selected()]

    def play_sample(self) -> None:
        model = Path(self.selected_voice_model())
        if not model.is_file() or not cuc.command_exists("piper-tts"):
            self.error_row.set_title("Voice sample could not play: the selected Piper voice or piper-tts is unavailable.")
            self.error_row.set_visible(True)
            return
        sample_config = cuc.Config(
            voice_enabled=True,
            piper_model=str(model),
            preview_dir=self.current_config.preview_dir,
            log_file=self.current_config.log_file,
            command_timeout=self.current_config.command_timeout,
            spoken_name=self.spoken_name.get_text().strip() or "there",
            aur_helper=self.current_config.aur_helper,
        )
        self.error_row.set_visible(False)
        cuc.speak_async("Hello. This is your selected update voice.", sample_config)

    def save(self) -> None:
        def required(row: Adw.EntryRow, label: str) -> str:
            value = row.get_text().strip()
            if not value:
                raise ValueError(f"{label} cannot be empty.")
            return str(Path(value).expanduser()) if "path" in label.lower() or "directory" in label.lower() or "file" in label.lower() else value

        try:
            advisory_settings = cuc.validate_signed_advisory_settings(
                self.advisory_feed_url.get_text(),
                self.advisory_signature_url.get_text(),
                self.advisory_public_key_file.get_text(),
            )
            if advisory_settings[2] and not Path(advisory_settings[2]).is_file():
                raise ValueError(
                    "The signed advisory public key file does not exist or is not a regular file."
                )
            config = cuc.Config(
                voice_enabled=self.voice_enabled.get_active(),
                spoken_name=required(self.spoken_name, "Spoken name"),
                aur_helper=self.aur_helpers[self.aur_helper.get_selected()],
                command_timeout=int(self.timeout.get_value()),
                installation_timeout=int(self.installation_timeout.get_value()),
                max_result_age_minutes=int(self.max_result_age.get_value()),
                minimum_free_space_mb=int(self.minimum_free_space.get_value()),
                piper_model=self.selected_voice_model(),
                preview_dir=required(self.preview_dir, "Preview cache directory"),
                log_file=required(self.log_file, "Log file"),
                advisory_cache_file=required(
                    self.advisory_cache_file,
                    "Advisory cache file",
                ),
                advisory_feed_url=advisory_settings[0],
                advisory_signature_url=advisory_settings[1],
                advisory_public_key_file=advisory_settings[2],
                advisory_refresh_hours=int(self.advisory_refresh_hours.get_value()),
                notifications_enabled=self.notifications_enabled.get_active(),
                enabled_sources={
                    source: row.get_active()
                    for source, row in self.source_switches.items()
                },
            )
            config.save(cuc.DEFAULT_CONFIG_PATH)
        except (OSError, ValueError) as exc:
            self.error_row.set_title(f"Settings were not saved: {exc}")
            self.error_row.set_visible(True)
            return
        self.on_saved(config)
        self.close()


class UpdateWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="Cachy Update Center")
        window_state = cuc.load_window_state()
        self.set_default_size(window_state.width, window_state.height)
        self.set_size_request(460, 480)
        if window_state.maximized:
            self.maximize()
        self.connect("close-request", self.persist_window_state)
        self.config_warning: str | None = None
        self.config = self.load_config()
        cuc.configure_logging(Path(self.config.log_file))
        logging.info("Desktop application started")
        polkit = cuc.check_polkit_readiness()
        logging.info("Polkit readiness ready=%s message=%s", polkit.ready, polkit.message)
        self.state: cuc.AppState | None = None
        self.check_in_progress = False
        self.update_in_progress = False
        self.session = cuc.UpdateSession()
        self.session_state_path = Path(self.config.log_file).with_name("last-session.json")
        self.recovery_warning = self.load_recovery_warning()
        self.active_process: subprocess.Popen[str] | None = None
        self.cancel_event: threading.Event | None = None
        self.process_lock = threading.Lock()
        self.cards: dict[str, SourceCard] = {}
        self.last_install_summary = cuc.InstallSummary()
        self.pending_install_summary = False

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        self.voice_button = Gtk.Button.new_from_icon_name(
            "audio-volume-high-symbolic" if self.config.voice_enabled else "audio-volume-muted-symbolic"
        )
        self.voice_button.set_tooltip_text("Repeat update status with Piper")
        self.voice_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Repeat spoken update status"],
        )
        self.voice_button.connect("clicked", lambda *_: self.announce_current_status(manual=True))
        header.pack_end(self.voice_button)
        settings_button = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        settings_button.set_tooltip_text("Settings (Ctrl+,)")
        settings_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Open update settings"],
        )
        settings_button.connect("clicked", lambda *_: self.open_settings())
        header.pack_end(settings_button)
        about_button = Gtk.Button.new_from_icon_name("help-about-symbolic")
        about_button.set_tooltip_text("About Cachy Update Center")
        about_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["About Cachy Update Center"],
        )
        about_button.connect("clicked", lambda *_: self.show_about())
        header.pack_end(about_button)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scroll)
        clamp = Adw.Clamp(maximum_size=1040, tightening_threshold=820)
        scroll.set_child(clamp)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(20)
        page.set_margin_bottom(24)
        page.set_margin_start(20)
        page.set_margin_end(20)
        clamp.set_child(page)

        hero = Gtk.Box(spacing=14)
        hero.add_css_class("overview-panel")
        hero_icon_well = Gtk.Box()
        hero_icon_well.add_css_class("overview-icon-well")
        hero_icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
        hero_icon.set_pixel_size(32)
        hero_icon.add_css_class("overview-icon")
        hero_icon_well.append(hero_icon)
        hero.append(hero_icon_well)
        hero_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.headline = Gtk.Label(label="Checking your system", xalign=0)
        self.headline.add_css_class("title-1")
        self.subheadline = Gtk.Label(label="Pacman, AUR, Flatpak, and Snap", xalign=0)
        self.subheadline.add_css_class("subtitle")
        hero_text.append(self.headline)
        hero_text.append(self.subheadline)
        hero.append(hero_text)
        self.overview_badge = Gtk.Label(label="Checking", xalign=0)
        self.overview_badge.add_css_class("overview-badge")
        self.overview_badge.add_css_class("overview-checking")
        self.overview_badge.set_valign(Gtk.Align.CENTER)
        hero.append(self.overview_badge)
        actions = Gtk.Box(spacing=8)
        actions.set_hexpand(True)
        actions.set_halign(Gtk.Align.END)
        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Refresh update status (Ctrl+R)")
        self.refresh_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Refresh update status"],
        )
        self.refresh_button.add_css_class("circular")
        self.refresh_button.set_valign(Gtk.Align.CENTER)
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        actions.append(self.refresh_button)
        self.review_all_button = Gtk.Button(label="Review updates")
        self.review_all_button.set_icon_name("view-list-symbolic")
        self.review_all_button.set_valign(Gtk.Align.CENTER)
        self.review_all_button.set_sensitive(False)
        self.review_all_button.set_visible(False)
        self.review_all_button.set_tooltip_text("Review all pending packages (Ctrl+U)")
        self.review_all_button.connect("clicked", lambda *_: self.confirm_install(None))
        actions.append(self.review_all_button)
        self.install_all_button = Gtk.Button(label="Update all")
        self.install_all_button.set_icon_name("system-software-install-symbolic")
        self.install_all_button.add_css_class("primary-action")
        self.install_all_button.set_valign(Gtk.Align.CENTER)
        self.install_all_button.set_sensitive(False)
        self.install_all_button.set_visible(False)
        self.install_all_button.set_tooltip_text("Install all eligible updates")
        self.install_all_button.connect("clicked", lambda *_: self.confirm_install_now(None))
        actions.append(self.install_all_button)
        hero.append(actions)
        page.append(hero)

        workflow = Gtk.Box(spacing=10)
        workflow.add_css_class("workflow")
        for number, title in (("1", "Check"), ("2", "Review (optional)"), ("3", "Install")):
            step = Gtk.Box(spacing=6)
            number_label = Gtk.Label(label=number)
            number_label.add_css_class("step-number")
            title_label = Gtk.Label(label=title)
            title_label.add_css_class("dim-label")
            step.append(number_label)
            step.append(title_label)
            workflow.append(step)
        page.append(workflow)

        self.notice = Gtk.Label(xalign=0, wrap=True)
        self.notice.set_visible(False)
        self.notice.add_css_class("notice")
        page.append(self.notice)
        if self.config_warning:
            self.show_notice(self.config_warning)
        if self.recovery_warning:
            self.show_notice(self.recovery_warning)

        self.security_notice = Gtk.Label(xalign=0, wrap=True)
        self.security_notice.add_css_class("security-banner")
        self.security_notice.set_visible(False)
        page.append(self.security_notice)

        source_header = Gtk.Box(spacing=12)
        source_header.add_css_class("section-header")
        source_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        source_title = Gtk.Label(label="Update sources", xalign=0)
        source_title.add_css_class("title-3")
        self.source_summary = Gtk.Label(label="Checking Pacman, AUR, Flatpak, and Snap", xalign=0)
        self.source_summary.add_css_class("dim-label")
        source_text.append(source_title)
        source_text.append(self.source_summary)
        source_header.append(source_text)
        self.retry_button = Gtk.Button(label="Retry failed")
        self.retry_button.set_icon_name("view-refresh-symbolic")
        self.retry_button.set_halign(Gtk.Align.END)
        self.retry_button.set_hexpand(True)
        self.retry_button.set_visible(False)
        self.retry_button.connect("clicked", lambda *_: self.retry_failed_sources())
        source_header.append(self.retry_button)
        page.append(source_header)

        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_min_children_per_line(1)
        grid.set_max_children_per_line(2)
        grid.set_column_spacing(8)
        grid.set_row_spacing(8)
        grid.set_homogeneous(True)
        grid.set_valign(Gtk.Align.START)
        for source in SOURCE_ORDER:
            card = SourceCard(source, self.confirm_install, self.confirm_install_now)
            self.cards[source] = card
            grid.append(card)
        page.append(grid)

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("footer")
        self.last_checked = Gtk.Label(label="Not checked yet", xalign=0)
        self.last_checked.add_css_class("dim-label")
        footer.append(self.last_checked)
        version = Gtk.Label(label=f"v{cuc.__version__}")
        version.add_css_class("dim-label")
        version.set_tooltip_text("CachyOS Update Center version")
        footer.append(version)
        export_diagnostics = Gtk.Button(label="Export diagnostics")
        export_diagnostics.set_tooltip_text(
            "Create a privacy-safe support bundle in the preview cache directory (Ctrl+D)"
        )
        export_diagnostics.connect("clicked", lambda *_: self.export_diagnostic_bundle())
        footer.append(export_diagnostics)
        toolbar_view.add_bottom_bar(footer)

        self.install_css()
        self.install_keyboard_shortcuts(application)
        self.refresh()

    def persist_window_state(self, *_args) -> bool:
        width, height = self.get_default_size()
        try:
            cuc.save_window_state(
                cuc.WindowState(width, height, self.is_maximized())
            )
        except OSError as exc:
            logging.warning("Could not persist desktop window state: %s", exc)
        return False

    def load_config(self) -> cuc.Config:
        try:
            return cuc.Config.load(cuc.DEFAULT_CONFIG_PATH)
        except cuc.ConfigError as exc:
            self.config_warning = f"Configuration could not be read; using defaults for this session. {exc}"
            return cuc.Config()

    def load_recovery_warning(self) -> str | None:
        try:
            payload = json.loads(self.session_state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        phase = payload.get("phase")
        if phase not in (cuc.SessionPhase.INSTALLING.value, cuc.SessionPhase.CANCELLING.value):
            return None
        targets = payload.get("targets")
        source_text = ", ".join(targets) if isinstance(targets, list) else "unknown sources"
        logging.warning("Recovered interrupted update session phase=%s targets=%s", phase, source_text)
        return (
            "The previous update session ended unexpectedly while processing "
            f"{source_text}. Refresh and verify package-manager state before starting another update."
        )

    def persist_session(self, event: str) -> None:
        payload = {
            "version": cuc.__version__,
            "event": event,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            **self.session.to_dict(),
        }
        temporary = self.session_state_path.with_suffix(".json.tmp")
        try:
            self.session_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.session_state_path)
        except OSError as exc:
            logging.warning("Could not persist update session state: %s", exc)
        logging.info(
            "Update session transition event=%s phase=%s targets=%s",
            event,
            self.session.phase.value,
            ",".join(self.session.targets),
        )

    def install_css(self) -> None:
        css = b"""
        .overview-panel {
          padding: 18px;
          border-radius: 16px;
          background: @card_bg_color;
          border: 1px solid alpha(@window_fg_color, 0.14);
          box-shadow: 0 2px 8px alpha(#000000, 0.08);
        }
        .overview-icon-well, .source-icon-well {
          border-radius: 999px;
          background: alpha(@window_fg_color, 0.08);
          color: @window_fg_color;
        }
        .overview-icon-well {
          min-width: 56px;
          min-height: 56px;
        }
        .source-icon-well {
          min-width: 44px;
          min-height: 44px;
        }
        .overview-icon, .source-icon { color: @window_fg_color; }
        .overview-badge, .count-pill, .status-chip {
          border-radius: 999px;
          font-weight: 700;
        }
        .overview-badge {
          padding: 6px 10px;
          background: alpha(@window_fg_color, 0.08);
        }
        .overview-checking { color: @window_fg_color; }
        .overview-ready { color: @warning_color; }
        .overview-current { color: @success_color; }
        .overview-error { color: @error_color; }
        .source-card {
          padding: 14px;
          border-radius: 16px;
          background: @card_bg_color;
          border: 1px solid alpha(@borders, 0.72);
          box-shadow: 0 1px 3px alpha(#000000, 0.07);
        }
        .source-card:hover {
          border-color: alpha(@window_fg_color, 0.28);
          box-shadow: 0 4px 12px alpha(#000000, 0.10);
        }
        .count-pill {
          min-width: 26px;
          padding: 5px 9px;
          background: alpha(@window_fg_color, 0.08);
          color: @window_fg_color;
          font-size: 18px;
        }
        .primary-action {
          background: @window_fg_color;
          color: @window_bg_color;
          font-weight: 700;
        }
        .primary-action:hover {
          background: alpha(@window_fg_color, 0.84);
        }
        .workflow {
          padding: 0 6px;
        }
        .step-number {
          min-width: 24px;
          min-height: 24px;
          border-radius: 999px;
          background: alpha(@window_fg_color, 0.10);
          font-weight: 700;
        }
        .package-list {
          font-family: monospace;
          font-size: 0.9em;
        }
        .package-preview {
          padding: 10px 12px;
          border-radius: 12px;
          background: alpha(@window_fg_color, 0.045);
        }
        .status-chip { font-size: 0.92em; }
        .status-ok { color: @success_color; }
        .status-warn { color: @warning_color; }
        .status-error { color: @error_color; }
        .more-link { font-weight: 600; }
        .notice {
          padding: 12px 14px;
          border-radius: 12px;
          background: alpha(@warning_bg_color, 0.18);
        }
        .security-banner {
          padding: 14px 16px;
          border-radius: 12px;
          background: alpha(@warning_bg_color, 0.15);
          border: 1px solid alpha(@warning_color, 0.35);
        }
        .section-header { padding: 6px 4px 0; }
        .footer {
          padding: 10px 20px;
          background: @headerbar_bg_color;
          border-top: 1px solid alpha(@borders, 0.80);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def show_notice(self, message: str) -> None:
        self.notice.set_text(message)
        self.notice.set_visible(True)

    def install_keyboard_shortcuts(self, application: Adw.Application) -> None:
        shortcuts = {
            "refresh": (lambda: self.refresh(), ["<Primary>r"]),
            "settings": (self.open_settings, ["<Primary>comma"]),
            "review": (lambda: self.confirm_install(None), ["<Primary>u"]),
            "diagnostics": (self.export_diagnostic_bundle, ["<Primary>d"]),
        }
        for name, (callback, accelerators) in shortcuts.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate",
                lambda _action, _parameter, callback=callback: callback(),
            )
            self.add_action(action)
            application.set_accels_for_action(f"win.{name}", accelerators)

    def send_notification(self, title: str, body: str) -> None:
        if not self.config.notifications_enabled:
            return
        application = self.get_application()
        if not isinstance(application, Gio.Application):
            return
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        application.send_notification(None, notification)

    def retry_failed_sources(self) -> None:
        if not self.state:
            return
        failed_sources = cuc.UPDATE_CONTROLLER.retry_sources(self.state.results)
        if failed_sources:
            self.refresh(failed_sources, preserve_results=True)

    def set_overview_badge(self, text: str, style: str) -> None:
        for css_class in ("overview-checking", "overview-ready", "overview-current", "overview-error"):
            self.overview_badge.remove_css_class(css_class)
        self.overview_badge.set_text(text)
        self.overview_badge.add_css_class(style)

    def refresh(
        self,
        sources: list[str] | None = None,
        *,
        preserve_results: bool = False,
    ) -> None:
        if self.update_in_progress:
            self.show_notice("Wait for the current update session to finish before refreshing.")
            return
        if self.check_in_progress:
            return
        self.check_in_progress = True
        prior_state = self.state if preserve_results else None
        if not preserve_results:
            self.state = None
        self.refresh_button.set_sensitive(False)
        self.retry_button.set_sensitive(False)
        self.review_all_button.set_sensitive(False)
        self.review_all_button.set_visible(False)
        self.install_all_button.set_sensitive(False)
        self.install_all_button.set_visible(False)
        self.review_all_button.set_label("Review updates")
        self.install_all_button.set_label("Update all")
        self.notice.set_visible(False)
        self.security_notice.set_visible(False)
        if self.config_warning:
            self.show_notice(self.config_warning)
        source_names = sources or list(SOURCE_ORDER)
        display_sources = ", ".join(cuc.SOURCE_DISPLAY_NAMES[source] for source in source_names)
        self.headline.set_text("Retrying failed sources" if preserve_results else "Checking your system")
        self.subheadline.set_text(f"{display_sources} {'are' if len(source_names) != 1 else 'is'} being checked.")
        self.set_overview_badge("Checking", "overview-checking")
        self.source_summary.set_text(f"Checking {display_sources}")
        for source in source_names:
            if source in self.cards:
                self.cards[source].show_checking()
        threading.Thread(
            target=self._check_worker,
            args=(sources, prior_state),
            daemon=True,
        ).start()

    def _check_worker(
        self,
        sources: list[str] | None,
        prior_state: cuc.AppState | None,
    ) -> None:
        try:
            results = cuc.UPDATE_CONTROLLER.check(
                self.config,
                sources=sources,
                prior_results=prior_state.results if prior_state else None,
            )
            state = cuc.AppState(results)
            GLib.idle_add(self._finish_refresh, state, None)
        except Exception as exc:  # defensive boundary between worker and UI
            GLib.idle_add(self._finish_refresh, None, str(exc))

    def _finish_refresh(self, state: cuc.AppState | None, error: str | None) -> bool:
        self.check_in_progress = False
        self.refresh_button.set_sensitive(True)
        self.retry_button.set_sensitive(True)
        if error or state is None:
            logging.error("Desktop update check failed: %s", error or "unknown error")
            self.headline.set_text("Update check failed")
            self.subheadline.set_text("Try refreshing again. No update action has been started.")
            self.set_overview_badge("Check failed", "overview-error")
            self.source_summary.set_text("No update actions are available until the next successful check")
            self.show_notice(error or "Unknown update check error")
            self.send_notification("Update check failed", error or "Unknown update check error")
            return False

        self.state = state
        for result in state.results.values():
            logging.info(
                "Check result source=%s enabled=%s available=%s count=%s allowed=%s error=%r",
                result.source,
                result.enabled,
                result.available,
                result.count,
                result.install_allowed,
                result.error,
            )
        for source in SOURCE_ORDER:
            result = state.results.get(source)
            if result:
                self.cards[source].update(result)
        total = sum(result.count for result in state.results.values())
        failures = state.failed_results()
        self.retry_button.set_visible(bool(failures))
        unavailable = state.unavailable_results()
        targets = state.installable_sources()
        installable_total = sum(state.results[source].count for source in targets)
        self.review_all_button.set_sensitive(bool(total))
        self.review_all_button.set_visible(bool(total))
        self.install_all_button.set_sensitive(bool(targets))
        self.install_all_button.set_visible(bool(targets))
        if installable_total:
            self.review_all_button.set_label(
                f"Review {total} update{'s' if total != 1 else ''}"
            )
            self.install_all_button.set_label("Update all")
        self.last_checked.set_text(f"Last checked {datetime.now().strftime('%H:%M')}")
        security_warnings = [
            warning
            for result in state.results.values()
            for warning in result.security_warnings
        ]
        if security_warnings:
            self.security_notice.set_text(
                "AUR safety review\n" + "\n".join(f"• {warning}" for warning in security_warnings)
            )
            self.security_notice.set_visible(True)

        if failures:
            self.headline.set_text("Update check needs attention")
            self.subheadline.set_text(f"Could not check: {', '.join(result.display_name for result in failures)}")
            self.set_overview_badge("Needs attention", "overview-error")
            self.source_summary.set_text("Install actions are available only for sources checked successfully")
            self.show_notice("Updates are only available for sources that completed successfully.")
            self.send_notification(
                "Update check needs attention",
                "Could not check: " + ", ".join(result.display_name for result in failures),
            )
        elif total:
            blocked = [result for result in state.results.values() if result.available and not result.install_allowed]
            suffix = f"; {len(blocked)} source blocked by safety checks" if blocked else ""
            if installable_total:
                self.headline.set_text(
                    f"{installable_total} installable update{'s' if installable_total != 1 else ''}"
                )
                self.subheadline.set_text("Review package details, or update all eligible sources now.")
                self.set_overview_badge("Ready to install", "overview-ready")
                self.source_summary.set_text(
                    f"{installable_total} installable update{'s' if installable_total != 1 else ''} across "
                    f"{len(targets)} source{'s' if len(targets) != 1 else ''}{suffix}"
                )
                self.send_notification(
                    "Updates are ready",
                    self.source_summary.get_text(),
                )
            else:
                self.headline.set_text("Updates require safety review")
                self.subheadline.set_text("No pending source is currently installable.")
                self.set_overview_badge("Blocked", "overview-error")
                self.source_summary.set_text(
                    f"{total} pending update{'s' if total != 1 else ''}; "
                    "all affected sources are blocked by safety checks"
                )
                self.send_notification("Updates blocked", self.source_summary.get_text())
        else:
            self.headline.set_text(cuc.ALL_CURRENT_STATUS)
            suffix = f" Unavailable: {', '.join(result.display_name for result in unavailable)}." if unavailable else ""
            self.subheadline.set_text(f"All installed sources are up to date.{suffix}")
            self.set_overview_badge("All current", "overview-current")
            self.source_summary.set_text("No updates are waiting to be installed")
        self.announce_current_status()
        if self.pending_install_summary:
            self.show_notice(self.last_install_summary.message())
            self.pending_install_summary = False
        return False

    def announce_current_status(self, *, manual: bool = False) -> None:
        """Keep the desktop workflow aligned with CUC's existing Piper announcements."""
        if not self.config.voice_enabled:
            if manual:
                self.show_notice("Piper voice is disabled in the CUC configuration.")
            return
        if self.state:
            cuc.speak_async(cuc.compose_summary_speech(self.state, self.config), self.config)

    def open_settings(self) -> None:
        SettingsWindow(self.config, self.apply_settings).present(self)

    def show_about(self) -> None:
        dialog = Adw.AboutDialog(
            application_name="Cachy Update Center",
            application_icon="software-update-available-symbolic",
            developer_name="Alvin and contributors",
            version=cuc.__version__,
            website="https://github.com/mezzenger/CachyOS-Updater",
            issue_url="https://github.com/mezzenger/CachyOS-Updater/issues",
            copyright="© 2026 Alvin",
        )
        dialog.set_developers(["Alvin"])
        dialog.add_credit_section(
            "Engineering assistance",
            ["OpenAI Codex — v0.5.0 reliability and security upgrade"],
        )
        dialog.present(self)

    def apply_settings(self, config: cuc.Config) -> None:
        self.config = config
        cuc.configure_logging(Path(config.log_file))
        self.session_state_path = Path(config.log_file).with_name("last-session.json")
        self.voice_button.set_icon_name(
            "audio-volume-high-symbolic" if config.voice_enabled else "audio-volume-muted-symbolic"
        )
        self.show_notice("Settings saved. The next refresh will use the new configuration.")

    def confirm_install(self, source: str | None) -> None:
        if self.update_in_progress or self.session.phase == cuc.SessionPhase.REVIEWING:
            self.show_notice("An update session is already running.")
            return
        if not self.state:
            return
        if source:
            targets = [source]
        else:
            targets = [
                source
                for source in SOURCE_ORDER
                if source in self.state.results and self.state.results[source].available
            ]
        if not targets:
            return
        self.show_install_review(targets, include_excluded=source is None)

    def confirm_install_now(self, source: str | None) -> None:
        if self.update_in_progress or self.session.phase == cuc.SessionPhase.REVIEWING:
            self.show_notice("An update session is already running.")
            return
        if not self.state:
            return
        installable = self.state.installable_sources()
        if source:
            targets = [source] if source in installable else []
        else:
            targets = installable
        if not targets:
            return
        if not self.session.begin_review(targets):
            self.show_notice("An update session is already running.")
            return
        self.persist_session("approval-started")
        total = sum(self.state.results[source].count for source in targets)
        source_names = ", ".join(self.state.results[source].display_name for source in targets)
        install_label = "Install now" if source else "Install all"
        dialog = Adw.AlertDialog(
            heading=f"Install {total} update{'s' if total != 1 else ''}?",
            body=(
                f"Eligible updates from {source_names} will be installed. "
                "You can continue without opening the optional package review."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", install_label)
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")

        def respond(_current: Adw.AlertDialog, response: str) -> None:
            if response == "install":
                self.launch_updates(targets)
            else:
                self.session.reset()
                self.persist_session("approval-cancelled")

        dialog.connect("response", respond)
        dialog.present(self)

    def show_install_review(
        self,
        targets: list[str],
        *,
        include_excluded: bool = False,
    ) -> None:
        if not self.state:
            return
        if not self.session.begin_review(targets):
            self.show_notice("Finish or close the current update review before starting another.")
            return
        self.persist_session("review-started")
        results = [self.state.results[source] for source in targets if source in self.state.results]

        dialog = Adw.AlertDialog(heading="Review updates")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("install", "Install reviewed updates")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("install")
        dialog.set_close_response("cancel")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_spacing(14)
        content.set_size_request(600, -1)

        title = Gtk.Label(xalign=0)
        title.add_css_class("title-2")
        content.append(title)
        content.append(
            Gtk.Label(
                label="Choose the sources to install. Nothing starts until you confirm below.",
                xalign=0,
                wrap=True,
            )
        )

        selections: dict[str, Gtk.CheckButton] = {}
        for result in results:
            check = Gtk.CheckButton(
                label=f"{result.display_name} — {result.count} update(s)"
            )
            check.set_active(result.install_allowed and not result.error)
            check.set_sensitive(result.install_allowed and not result.error)
            content.append(check)
            selections[result.source] = check
            packages = Gtk.Label(
                label="\n".join(f"• {item}" for item in result.items) or "• Package details unavailable",
                xalign=0,
                wrap=True,
                selectable=True,
            )
            packages.add_css_class("package-preview")
            packages.set_margin_start(24)
            content.append(packages)
            details = list(result.security_warnings)
            if result.security_advisory_updated_at:
                details.append(
                    f"Advisory data updated: {result.security_advisory_updated_at}"
                )
            if result.security_advisory_url:
                details.append(f"Advisory: {result.security_advisory_url}")
            if result.source == "aur":
                details.append(f"Safety metadata checked: {result.checked_at}")
            if details:
                detail = Gtk.Label(
                    label="\n".join(f"• {line}" for line in details),
                    xalign=0,
                    wrap=True,
                    selectable=True,
                )
                detail.add_css_class("package-preview")
                detail.set_margin_start(24)
                content.append(detail)

        if include_excluded:
            excluded = [
                result
                for result in self.state.results.values()
                if result.available and result.source not in selections
            ]
            if excluded:
                excluded_text = Gtk.Label(
                    label="Not selected because installation is blocked or its check failed:\n"
                    + "\n".join(
                        f"• {result.display_name}: "
                        f"{result.security_warnings[0] if result.security_warnings else result.error or 'not installable'}"
                        for result in excluded
                    ),
                    xalign=0,
                    wrap=True,
                )
                excluded_text.add_css_class("security-banner")
                content.append(excluded_text)

        def selected_targets() -> list[str]:
            return [
                source for source, check in selections.items() if check.get_active()
            ]

        def update_selection(*_args) -> None:
            selected = selected_targets()
            total = sum(self.state.results[source].count for source in selected)
            title.set_text(
                f"{total} update{'s' if total != 1 else ''} from "
                f"{len(selected)} source{'s' if len(selected) != 1 else ''}"
            )
            dialog.set_response_enabled("install", bool(selected))

        for check in selections.values():
            check.connect("toggled", update_selection)
        update_selection()

        def respond(_current: Adw.AlertDialog, response: str) -> None:
            selected = selected_targets()
            if response == "install" and selected:
                self.session.targets = selected
                self.launch_updates(selected)
            else:
                self.session.reset()
                self.persist_session("review-cancelled")

        dialog.connect("response", respond)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(420)
        scroll.set_child(content)
        dialog.set_extra_child(scroll)
        dialog.present(self)

    def launch_updates(self, targets: list[str]) -> None:
        if self.update_in_progress:
            self.show_notice("An update session is already running.")
            return
        if self.state:
            blocked = [
                self.state.results[source]
                for source in targets
                if source in self.state.results and not self.state.results[source].install_allowed
            ]
            if blocked:
                self.session.reset()
                self.show_notice("Safety checks blocked one or more selected update sources.")
                return
        assert self.state is not None
        preflight_issues = cuc.check_update_preflight(targets, self.state, self.config)
        for issue in preflight_issues:
            log = logging.error if issue.blocking else logging.warning
            log("Update preflight %s: %s", issue.code, issue.message)
        blocking_issues = [issue for issue in preflight_issues if issue.blocking]
        if blocking_issues:
            message = "Preflight blocked installation:\n" + "\n".join(
                f"• {issue.message}" for issue in blocking_issues
            )
            if any(issue.code == "package-manager-running" for issue in blocking_issues):
                self.show_preflight_retry(targets, message)
                self.send_notification("Update preflight failed", blocking_issues[0].message)
                return
            self.session.reset()
            self.show_notice(message)
            self.send_notification("Update preflight failed", blocking_issues[0].message)
            return
        warnings = [issue.message for issue in preflight_issues if not issue.blocking]
        if warnings:
            self.show_notice("Preflight warning: " + " ".join(warnings))

        command_pairs = [
            (
                source,
                cuc.PROVIDERS[source].build_command(
                    self.config,
                    source != "aur",
                ),
            )
            for source in targets
            if source in cuc.PROVIDERS
        ]
        missing_commands = [source for source, command in command_pairs if command is None]
        if missing_commands:
            self.session.reset()
            message = "No usable update command was found for: " + ", ".join(
                cuc.SOURCE_DISPLAY_NAMES.get(source, source)
                for source in missing_commands
            )
            logging.error(message)
            self.show_notice(message)
            return
        polkit = cuc.check_polkit_readiness()
        privileged_sources = [source for source in targets if source in ("pacman", "snap")]
        if privileged_sources and not polkit.ready:
            self.session.reset()
            message = (
                "Native Polkit integration is required for desktop system updates. "
                + polkit.message
                + " Install it or update that source manually."
            )
            logging.error(message)
            self.show_notice(message)
            return
        commands = [
            ["pkexec", str(POLKIT_HELPER), source]
            if source in ("pacman", "snap")
            else command
            for source, command in command_pairs
            if command is not None
        ]
        if "aur" in targets and secure_terminal_command(["true"]) is None:
            self.session.reset()
            self.show_notice("A terminal emulator is required for secure AUR authentication and review.")
            return
        self.session.targets = list(targets)
        self.session.commands = [list(command) for command in commands]
        self.persist_session("installation-approved")
        logging.info(
            "Update installation approved targets=%s authentication=%s",
            ",".join(targets),
            "polkit-and-terminal" if "aur" in targets else "polkit-or-unprivileged",
        )
        self.set_update_in_progress(True)
        try:
            self.run_updates_in_window(commands)
        except Exception as exc:
            self.abort_update(f"Could not start the update session: {exc}")

    def show_preflight_retry(self, targets: list[str], message: str) -> None:
        """Let transient package-manager conflicts be checked again in place."""
        dialog = Adw.AlertDialog(
            heading="Another update process is still running",
            body=message + "\n\nClose or finish that process, then retry the safety check.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("retry", "Retry safety check")
        dialog.set_response_appearance("retry", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("retry")
        dialog.set_close_response("cancel")

        def respond(_current: Adw.AlertDialog, response: str) -> None:
            if response == "retry":
                self.persist_session("preflight-retry")
                self.launch_updates(list(targets))
                return
            self.session.reset()
            self.persist_session("review-cancelled")

        dialog.connect("response", respond)
        dialog.present(self)

    def set_update_in_progress(self, active: bool) -> None:
        self.update_in_progress = active
        self.refresh_button.set_sensitive(not active and not self.check_in_progress)
        if active:
            self.review_all_button.set_sensitive(False)
            self.install_all_button.set_sensitive(False)
            for card in self.cards.values():
                card.review_button.set_sensitive(False)
                card.install_button.set_sensitive(False)
            return
        if self.state:
            for source, result in self.state.results.items():
                if source in self.cards:
                    self.cards[source].update(result)
            has_updates = any(result.available for result in self.state.results.values())
            has_installable_updates = bool(self.state.installable_sources())
            self.review_all_button.set_sensitive(has_updates)
            self.review_all_button.set_visible(has_updates)
            self.install_all_button.set_sensitive(has_installable_updates)
            self.install_all_button.set_visible(has_installable_updates)

    def abort_update(self, message: str) -> bool:
        logging.error("Desktop update aborted: %s", message)
        self.session.fail(message)
        self.persist_session("aborted")
        self.set_update_in_progress(False)
        self.show_notice(message)
        self.send_notification("Update did not start", message)
        return False

    def run_updates_in_window(self, commands: list[list[str]]) -> bool:
        """Run CUC's update commands while streaming their output into GTK."""
        self.session.begin_install()
        self.persist_session("installation-started")
        dialog = Adw.Dialog(title="Installing updates")
        dialog.set_content_width(760)
        dialog.set_content_height(480)
        dialog.set_can_close(False)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        cancel_button = Gtk.Button(label="Cancel update")
        close_button = Gtk.Button(label="Close")
        close_button.set_sensitive(False)
        header.pack_start(cancel_button)
        header.pack_end(close_button)
        toolbar.add_top_bar(header)
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        application = self.get_application()
        inhibit_cookie = 0
        if isinstance(application, Gtk.Application):
            inhibit_cookie = application.inhibit(
                self,
                Gtk.ApplicationInhibitFlags.LOGOUT | Gtk.ApplicationInhibitFlags.SUSPEND,
                "Installing system updates",
            )

        cancel_button.connect(
            "clicked",
            lambda *_args: (self.cancel_active_update(), cancel_button.set_sensitive(False)),
        )
        close_button.connect("clicked", lambda *_args: dialog.close())

        output = Gtk.TextView()
        output.set_editable(False)
        output.set_monospace(True)
        output.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        output.set_margin_top(12)
        output.set_margin_bottom(12)
        output.set_margin_start(12)
        output.set_margin_end(12)
        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_child(output)
        toolbar.set_content(scroll)
        dialog.set_child(toolbar)
        dialog.present(self)

        def append(text: str) -> bool:
            buffer = output.get_buffer()
            end = buffer.get_end_iter()
            buffer.insert(end, text)
            output.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0.0, 1.0)
            return False

        def finish(summary: cuc.InstallSummary) -> bool:
            message = summary.message()
            append(f"\n{message}\n")
            cancel_button.set_sensitive(False)
            close_button.set_sensitive(True)
            dialog.set_can_close(True)
            with self.process_lock:
                self.active_process = None
            self.cancel_event = None
            if inhibit_cookie and isinstance(application, Gtk.Application):
                application.uninhibit(inhibit_cookie)
            self.last_install_summary = summary
            if summary.successful():
                self.session.finish(message)
                notification_title = "Updates completed"
            else:
                self.session.fail(message)
                notification_title = "Update session needs attention"
            self.persist_session("installation-finished")
            logging.info("Desktop update summary: %s", message)
            self.set_update_in_progress(False)
            self.pending_install_summary = True
            self.send_notification(notification_title, message)
            self.refresh()
            return False

        command_sources = list(self.session.targets)

        def worker() -> None:
            summary = cuc.InstallSummary()

            def run_one(source: str, command: list[str]) -> cuc.ExecutionOutcome:
                if cancel_event.is_set():
                    return cuc.ExecutionOutcome(source, "cancelled", "Cancelled before this source started.")
                actual_command = command
                if source == "aur":
                    wrapped = secure_terminal_command(command)
                    if wrapped is None:
                        return cuc.ExecutionOutcome(source, "failed", "No secure terminal emulator was found.")
                    actual_command = wrapped
                    GLib.idle_add(
                        append,
                        "AUR review and authentication opened in a separate terminal. "
                        "CUC never receives your password.\n",
                    )
                else:
                    GLib.idle_add(append, f"$ {shlex.join(actual_command)}\n")
                logging.info(
                    "Desktop update command starting source=%s command=%s",
                    source,
                    shlex.join(actual_command),
                )
                try:
                    process = subprocess.Popen(
                        actual_command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        start_new_session=True,
                    )
                except OSError as exc:
                    logging.error("Could not start update source=%s: %s", source, exc)
                    return cuc.ExecutionOutcome(source, "failed", f"Could not start command: {exc}")
                with self.process_lock:
                    self.active_process = process
                timed_out = threading.Event()

                def force_kill() -> None:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except OSError:
                            pass

                def terminate_for_timeout() -> None:
                    if process.poll() is None:
                        timed_out.set()
                        logging.error(
                            "Desktop update timed out source=%s timeout=%ss",
                            source,
                            self.config.installation_timeout,
                        )
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except OSError:
                            pass
                        kill_timer = threading.Timer(5, force_kill)
                        kill_timer.daemon = True
                        kill_timer.start()

                timeout_timer = threading.Timer(
                    self.config.installation_timeout,
                    terminate_for_timeout,
                )
                timeout_timer.daemon = True
                timeout_timer.start()
                try:
                    assert process.stdout is not None
                    for line in process.stdout:
                        GLib.idle_add(append, line)
                    return_code = process.wait()
                finally:
                    timeout_timer.cancel()
                    with self.process_lock:
                        self.active_process = None
                logging.info(
                    "Desktop update command finished source=%s exit_code=%s",
                    source,
                    return_code,
                )
                if timed_out.is_set():
                    return cuc.ExecutionOutcome(
                        source,
                        "timed-out",
                        f"Exceeded {self.config.installation_timeout} seconds.",
                        return_code,
                    )
                if cancel_event.is_set():
                    return cuc.ExecutionOutcome(source, "cancelled", "Cancelled by the user.", return_code)
                if return_code:
                    return cuc.ExecutionOutcome(
                        source,
                        "failed",
                        f"Exited with status {return_code}.",
                        return_code,
                    )
                return cuc.ExecutionOutcome(source, "succeeded", "Completed successfully.", 0)

            outcomes = cuc.UPDATE_EXECUTOR.execute(zip(command_sources, commands), run_one)
            summary.extend_execution(outcomes)
            GLib.idle_add(finish, summary)

        threading.Thread(target=worker, daemon=False, name="cuc-update-executor").start()
        return False

    def cancel_active_update(self) -> None:
        logging.warning("Desktop update cancellation requested")
        self.session.request_cancel()
        self.persist_session("cancellation-requested")
        if self.cancel_event:
            self.cancel_event.set()
        with self.process_lock:
            process = self.active_process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except OSError as exc:
                self.show_notice(f"Could not signal the update process: {exc}")
                return

            def terminate_if_needed() -> None:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except OSError:
                        return

                    def kill_if_needed() -> None:
                        if process.poll() is None:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except OSError:
                                pass

                    kill_timer = threading.Timer(5, kill_if_needed)
                    kill_timer.daemon = True
                    kill_timer.start()

            terminate_timer = threading.Timer(5, terminate_if_needed)
            terminate_timer.daemon = True
            terminate_timer.start()

    def export_diagnostic_bundle(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = Path(self.config.preview_dir) / f"cuc-diagnostics-{timestamp}.zip"
        try:
            cuc.export_diagnostics(
                destination,
                self.config,
                state=self.state,
                app_version=cuc.__version__,
            )
        except OSError as exc:
            logging.error("Could not export diagnostics: %s", exc)
            self.show_notice(f"Could not export diagnostics: {exc}")
            return
        logging.info("Privacy-safe diagnostics exported to %s", destination)
        self.show_notice(f"Diagnostics exported to {destination}")


class CucDesktopApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        window = self.props.active_window
        if not window:
            window = UpdateWindow(self)
        window.present()


def main() -> int:
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        print("Unable to initialize GTK: no graphical display is available.", file=sys.stderr)
        return 1
    app = CucDesktopApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
