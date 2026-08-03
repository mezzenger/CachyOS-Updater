#!/usr/bin/env python3
"""Native GNOME dashboard for the CachyOS Update Center."""

from __future__ import annotations

import os
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import cuc


APP_ID = "io.github.alvin.Cuc"
PIPER_VOICE_ROOT = Path("/usr/share/piper-voices")
SOURCE_ORDER = ("pacman", "aur", "flatpak", "snap")
SOURCE_ICONS = {
    "pacman": "system-software-update-symbolic",
    "aur": "applications-system-symbolic",
    "flatpak": "package-x-generic-symbolic",
    "snap": "system-run-symbolic",
}
AUTHENTICATION_TIMEOUT = 30
POLKIT_HELPER = Path("/usr/lib/cachyos-update-center/cuc-privileged")


def create_sudo_askpass() -> tuple[tempfile.TemporaryDirectory[str], str]:
    """Create an askpass helper that reads from an inherited anonymous pipe."""
    directory = tempfile.TemporaryDirectory(prefix="cuc-askpass-")
    path = Path(directory.name) / "askpass.sh"
    path.write_text(
        "#!/bin/sh\n"
        "IFS= read -r password <&\"${CUC_ASKPASS_FD:?}\"\n"
        "printf '%s\\n' \"$password\"\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return directory, str(path)


class SourceCard(Gtk.Box):
    """One compact update-source summary card."""

    def __init__(self, source: str, on_install) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.source = source
        self.on_install = on_install
        self.add_css_class("source-card")
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_size_request(280, -1)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(6)
        self.set_margin_end(6)

        top = Gtk.Box(spacing=12)
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

        footer = Gtk.Box()
        footer.set_halign(Gtk.Align.END)
        self.install_button = Gtk.Button(label="Install updates")
        self.install_button.set_icon_name("system-software-install-symbolic")
        self.install_button.add_css_class("primary-action")
        self.install_button.connect("clicked", lambda *_: self.on_install(self.source))
        footer.append(self.install_button)
        self.append(footer)

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
        self.all_packages = []
        self.has_updates = False
        self.more_packages.set_visible(False)
        self.install_button.set_label("Review updates")
        self.install_button.set_sensitive(False)

    def update(self, result: cuc.UpdateResult) -> None:
        self.title.set_text(result.display_name)
        self.status.remove_css_class("status-ok")
        self.status.remove_css_class("status-warn")
        self.status.remove_css_class("status-error")
        self.items.set_text("")
        self.all_packages = []
        self.has_updates = False
        self.more_packages.set_visible(False)

        if not result.available_on_system:
            self.status.set_text("Not installed")
            self.status.add_css_class("status-error")
            self.count.set_text("—")
            self.detail.set_text(result.error or result.command_hint)
            self.install_button.set_sensitive(False)
            return
        if result.error:
            self.status.set_text("Check failed")
            self.status.add_css_class("status-error")
            self.count.set_text("!")
            self.detail.set_text(result.error)
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
            self.install_button.set_label("Review safety" if not result.install_allowed else "Review updates")
            self.install_button.set_sensitive(True)
            return

        self.status.set_text("Up to date")
        self.status.add_css_class("status-ok")
        self.count.set_text("0")
        self.detail.set_text("Nothing pending from this source.")
        self.install_button.set_sensitive(False)

    def show_all_packages(self, _button: Gtk.LinkButton) -> bool:
        root = self.get_root()
        dialog = Gtk.Dialog(
            transient_for=root if isinstance(root, Gtk.Window) else None,
            modal=True,
            title=f"{self.source.upper() if self.source == 'aur' else self.source.capitalize()} updates",
        )
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        install_button = dialog.add_button("Review selection", Gtk.ResponseType.ACCEPT)
        install_button.add_css_class("primary-action")
        result_can_install = bool(self.has_updates)
        install_button.set_sensitive(result_can_install)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.set_default_size(620, 500)

        def respond(current: Gtk.Dialog, response: int) -> None:
            current.close()
            if response == Gtk.ResponseType.ACCEPT and self.has_updates:
                self.on_install(self.source)

        dialog.connect("response", respond)

        content = dialog.get_content_area()
        content.set_spacing(12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        title = Gtk.Label(label=f"{len(self.all_packages)} packages available", xalign=0)
        title.add_css_class("title-3")
        content.append(title)

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
        content.append(scroll)
        dialog.present()
        install_button.grab_focus()
        return True


class SettingsWindow(Adw.PreferencesWindow):
    """Editable front end for CUC's persistent configuration."""

    def __init__(self, parent: Gtk.Window, config: cuc.Config, on_saved) -> None:
        super().__init__(transient_for=parent, modal=True, title="Cachy Update Center Settings")
        self.set_default_size(620, 620)
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
            config = cuc.Config(
                voice_enabled=self.voice_enabled.get_active(),
                spoken_name=required(self.spoken_name, "Spoken name"),
                aur_helper=self.aur_helpers[self.aur_helper.get_selected()],
                command_timeout=int(self.timeout.get_value()),
                piper_model=self.selected_voice_model(),
                preview_dir=required(self.preview_dir, "Preview cache directory"),
                log_file=required(self.log_file, "Log file"),
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
        self.set_default_size(1040, 760)
        self.set_size_request(480, 540)
        self.config_warning: str | None = None
        self.config = self.load_config()
        self.state: cuc.AppState | None = None
        self.check_in_progress = False
        self.update_in_progress = False
        self.session = cuc.UpdateSession()
        self.active_process: subprocess.Popen[str] | None = None
        self.cancel_event: threading.Event | None = None
        self.process_lock = threading.Lock()
        self.cards: dict[str, SourceCard] = {}

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)
        self.voice_button = Gtk.Button.new_from_icon_name(
            "audio-volume-high-symbolic" if self.config.voice_enabled else "audio-volume-muted-symbolic"
        )
        self.voice_button.set_tooltip_text("Repeat update status with Piper")
        self.voice_button.connect("clicked", lambda *_: self.announce_current_status(manual=True))
        header.pack_end(self.voice_button)
        settings_button = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        settings_button.set_tooltip_text("Settings")
        settings_button.connect("clicked", lambda *_: self.open_settings())
        header.pack_end(settings_button)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scroll)
        clamp = Adw.Clamp(maximum_size=1160, tightening_threshold=880)
        scroll.set_child(clamp)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.set_margin_top(28)
        page.set_margin_bottom(32)
        page.set_margin_start(24)
        page.set_margin_end(24)
        clamp.set_child(page)

        hero = Gtk.Box(spacing=18)
        hero.add_css_class("overview-panel")
        hero_icon_well = Gtk.Box()
        hero_icon_well.add_css_class("overview-icon-well")
        hero_icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
        hero_icon.set_pixel_size(40)
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
        self.refresh_button.set_tooltip_text("Refresh update status")
        self.refresh_button.add_css_class("circular")
        self.refresh_button.set_valign(Gtk.Align.CENTER)
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        actions.append(self.refresh_button)
        self.install_all_button = Gtk.Button(label="Review updates")
        self.install_all_button.set_icon_name("system-software-install-symbolic")
        self.install_all_button.add_css_class("primary-action")
        self.install_all_button.set_valign(Gtk.Align.CENTER)
        self.install_all_button.set_sensitive(False)
        self.install_all_button.connect("clicked", lambda *_: self.confirm_install(None))
        actions.append(self.install_all_button)
        hero.append(actions)
        page.append(hero)

        workflow = Gtk.Box(spacing=10)
        workflow.add_css_class("workflow")
        for number, title in (("1", "Check"), ("2", "Review"), ("3", "Install")):
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
        page.append(source_header)

        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_min_children_per_line(1)
        grid.set_max_children_per_line(2)
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        grid.set_homogeneous(True)
        grid.set_valign(Gtk.Align.START)
        for source in SOURCE_ORDER:
            card = SourceCard(source, self.confirm_install)
            self.cards[source] = card
            grid.append(card)
        page.append(grid)

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("footer")
        self.last_checked = Gtk.Label(label="Not checked yet", xalign=0)
        self.last_checked.add_css_class("dim-label")
        footer.append(self.last_checked)
        open_terminal = Gtk.Button(label="Open terminal")
        open_terminal.set_halign(Gtk.Align.END)
        open_terminal.set_hexpand(True)
        open_terminal.connect("clicked", lambda *_: self.open_terminal())
        footer.append(open_terminal)
        page.append(footer)

        self.install_css()
        self.refresh()

    def load_config(self) -> cuc.Config:
        try:
            return cuc.Config.load(cuc.DEFAULT_CONFIG_PATH)
        except cuc.ConfigError as exc:
            self.config_warning = f"Configuration could not be read; using defaults for this session. {exc}"
            return cuc.Config()

    def install_css(self) -> None:
        css = b"""
        .overview-panel {
          padding: 24px;
          border-radius: 18px;
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
          min-width: 72px;
          min-height: 72px;
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
          padding: 20px;
          border-radius: 20px;
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
          min-height: 3.2em;
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
          padding: 16px 4px 0;
          border-top: 1px solid @borders;
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

    def set_overview_badge(self, text: str, style: str) -> None:
        for css_class in ("overview-checking", "overview-ready", "overview-current", "overview-error"):
            self.overview_badge.remove_css_class(css_class)
        self.overview_badge.set_text(text)
        self.overview_badge.add_css_class(style)

    def refresh(self) -> None:
        if self.update_in_progress:
            self.show_notice("Wait for the current update session to finish before refreshing.")
            return
        if self.check_in_progress:
            return
        self.check_in_progress = True
        self.state = None
        self.refresh_button.set_sensitive(False)
        self.install_all_button.set_sensitive(False)
        self.install_all_button.set_label("Review updates")
        self.notice.set_visible(False)
        self.security_notice.set_visible(False)
        if self.config_warning:
            self.show_notice(self.config_warning)
        self.headline.set_text("Checking your system")
        self.subheadline.set_text("Pacman, AUR, Flatpak, and Snap are being checked in parallel.")
        self.set_overview_badge("Checking", "overview-checking")
        self.source_summary.set_text("Checking Pacman, AUR, Flatpak, and Snap")
        for card in self.cards.values():
            card.show_checking()
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        try:
            state = cuc.check_all_parallel(self.config, show_progress=False)
            GLib.idle_add(self._finish_refresh, state, None)
        except Exception as exc:  # defensive boundary between worker and UI
            GLib.idle_add(self._finish_refresh, None, str(exc))

    def _finish_refresh(self, state: cuc.AppState | None, error: str | None) -> bool:
        self.check_in_progress = False
        self.refresh_button.set_sensitive(True)
        if error or state is None:
            self.headline.set_text("Update check failed")
            self.subheadline.set_text("Try refreshing again. No update action has been started.")
            self.set_overview_badge("Check failed", "overview-error")
            self.source_summary.set_text("No update actions are available until the next successful check")
            self.show_notice(error or "Unknown update check error")
            return False

        self.state = state
        for source in SOURCE_ORDER:
            result = state.results.get(source)
            if result:
                self.cards[source].update(result)
        total = sum(result.count for result in state.results.values())
        failures = state.failed_results()
        unavailable = state.unavailable_results()
        targets = [source for source, result in state.results.items() if result.available]
        self.install_all_button.set_sensitive(bool(targets))
        if total:
            self.install_all_button.set_label(f"Review {total} update{'s' if total != 1 else ''}")
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
        elif total:
            self.headline.set_text(f"{total} update{'s' if total != 1 else ''} ready")
            self.subheadline.set_text("Review package and safety details before installing.")
            self.set_overview_badge("Ready to install", "overview-ready")
            blocked = [result for result in state.results.values() if result.available and not result.install_allowed]
            suffix = f"; {len(blocked)} source blocked by safety checks" if blocked else ""
            self.source_summary.set_text(
                f"{total} pending update{'s' if total != 1 else ''} across "
                f"{len(targets)} source{'s' if len(targets) != 1 else ''}{suffix}"
            )
        else:
            self.headline.set_text("Everything is current")
            suffix = f" Unavailable: {', '.join(result.display_name for result in unavailable)}." if unavailable else ""
            self.subheadline.set_text(f"All installed sources are up to date.{suffix}")
            self.set_overview_badge("All current", "overview-current")
            self.source_summary.set_text("No updates are waiting to be installed")
        self.announce_current_status()
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
        SettingsWindow(self, self.config, self.apply_settings).present()

    def apply_settings(self, config: cuc.Config) -> None:
        self.config = config
        self.voice_button.set_icon_name(
            "audio-volume-high-symbolic" if config.voice_enabled else "audio-volume-muted-symbolic"
        )
        self.show_notice("Settings saved. The next refresh will use the new configuration.")

    def confirm_install(self, source: str | None) -> None:
        if self.update_in_progress or self.session.phase in (
            cuc.SessionPhase.REVIEWING,
            cuc.SessionPhase.AUTHENTICATING,
        ):
            self.show_notice("An update session is already running.")
            return
        if not self.state:
            return
        if source:
            targets = [source]
        else:
            targets = [name for name, result in self.state.results.items() if result.available]
        if not targets:
            return
        self.show_install_review(targets)

    def show_install_review(self, targets: list[str]) -> None:
        if not self.state:
            return
        if not self.session.begin_review(targets):
            self.show_notice("Finish or close the current update review before starting another.")
            return
        results = [self.state.results[source] for source in targets if source in self.state.results]
        blocked = [result for result in results if not result.install_allowed]
        total = sum(result.count for result in results)

        dialog = Gtk.Dialog(transient_for=self, modal=True, title="Review updates")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        continue_button = dialog.add_button("Install reviewed updates", Gtk.ResponseType.ACCEPT)
        continue_button.add_css_class("primary-action")
        continue_button.set_sensitive(not blocked)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT if not blocked else Gtk.ResponseType.CANCEL)
        dialog.set_default_size(640, 440)

        content = dialog.get_content_area()
        content.set_spacing(14)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)

        title = Gtk.Label(
            label=f"{total} update{'s' if total != 1 else ''} from "
            f"{len(results)} source{'s' if len(results) != 1 else ''}",
            xalign=0,
        )
        title.add_css_class("title-2")
        content.append(title)
        content.append(
            Gtk.Label(
                label="Nothing has been installed yet. Confirm the sources and safety findings below.",
                xalign=0,
                wrap=True,
            )
        )

        review_lines: list[str] = []
        for result in results:
            review_lines.append(f"{result.display_name} — {result.count} update(s)")
            review_lines.extend(f"  • {warning}" for warning in result.security_warnings)
        if blocked:
            review_lines.append("")
            review_lines.append(
                "Installation is blocked. Resolve the AUR safety findings and refresh before trying again."
            )
        review = Gtk.Label(label="\n".join(review_lines), xalign=0, wrap=True, selectable=True)
        review.add_css_class("package-preview")
        review.set_vexpand(True)
        content.append(review)

        def respond(current: Gtk.Dialog, response: int) -> None:
            current.close()
            if response == Gtk.ResponseType.ACCEPT and not blocked:
                self.launch_updates(targets)
            else:
                self.session.reset()

        dialog.connect("response", respond)
        dialog.present()

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
        commands = cuc.build_update_commands(targets, self.config, noninteractive=True)
        if "aur" not in targets and POLKIT_HELPER.is_file() and cuc.command_exists("pkexec"):
            polkit_sources = {"pacman": "pacman", "snap": "snap"}
            commands = [
                ["pkexec", str(POLKIT_HELPER), polkit_sources[command[1]]]
                if command and command[0] == "sudo" and command[1] in polkit_sources
                else command
                for command in commands
            ]
        if not commands:
            self.session.reset()
            self.show_notice("No usable update command was found for the selected source.")
            return

        self.set_update_in_progress(True)
        # Authenticate before commands that can request sudo credentials. A
        # Flatpak-only update does not need a password and can start directly.
        needs_authentication = any(cuc.update_command_needs_authentication(command) for command in commands)
        if needs_authentication:
            self.session.begin_authentication(commands)
            self.request_update_password(commands)
        else:
            self.run_updates_in_window(commands, "")

    def set_update_in_progress(self, active: bool) -> None:
        self.update_in_progress = active
        self.refresh_button.set_sensitive(not active and not self.check_in_progress)
        if active:
            self.install_all_button.set_sensitive(False)
            for card in self.cards.values():
                card.install_button.set_sensitive(False)
            return
        if self.state:
            for source, result in self.state.results.items():
                if source in self.cards:
                    self.cards[source].update(result)
            self.install_all_button.set_sensitive(any(result.available for result in self.state.results.values()))

    def abort_update(self, message: str) -> bool:
        self.session.fail(message)
        self.set_update_in_progress(False)
        self.show_notice(message)
        return False

    def request_update_password(self, commands: list[list[str]]) -> None:
        dialog = Gtk.Dialog(transient_for=self, modal=True, title="Administrator password")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Continue", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        content = dialog.get_content_area()
        content.set_spacing(12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.append(Gtk.Label(label="Enter your password to start the update.", xalign=0))
        password = Gtk.PasswordEntry(show_peek_icon=True)
        content.append(password)
        password.connect("activate", lambda *_: dialog.response(Gtk.ResponseType.ACCEPT))

        def respond(current: Gtk.Dialog, response: int) -> None:
            value = password.get_text() if response == Gtk.ResponseType.ACCEPT else None
            current.close()
            if value is not None:
                self.authenticate_and_run_updates(commands, value)
            else:
                self.session.reset()
                self.set_update_in_progress(False)

        dialog.connect("response", respond)
        dialog.present()
        password.grab_focus()

    def authenticate_and_run_updates(self, commands: list[list[str]], password: str) -> None:
        """Validate sudo in the app, then use its timestamp for the update terminal."""
        def worker() -> None:
            credential = password
            try:
                auth = subprocess.run(
                    ["sudo", "-S", "-p", "", "-v"], input=f"{credential}\n", text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                    timeout=AUTHENTICATION_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                credential = ""
                GLib.idle_add(self.abort_update, "Administrator verification timed out. No updates were started.")
                return
            except OSError as exc:
                credential = ""
                GLib.idle_add(self.abort_update, f"Could not verify administrator access: {exc}")
                return
            if auth.returncode:
                credential = ""
                GLib.idle_add(self.abort_update, "Password was not accepted. No updates were started.")
                return
            GLib.idle_add(self.run_updates_in_window, commands, credential)
            credential = ""

        threading.Thread(target=worker, daemon=True).start()

    def run_updates_in_window(self, commands: list[list[str]], password: str) -> bool:
        """Run CUC's update commands while streaming their output into GTK."""
        self.session.begin_install()
        dialog = Gtk.Dialog(transient_for=self, modal=True, title="Installing updates")
        cancel_button = dialog.add_button("Cancel update", Gtk.ResponseType.CANCEL)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(760, 480)
        close_button = dialog.get_widget_for_response(Gtk.ResponseType.CLOSE)
        close_button.set_sensitive(False)
        cancel_event = threading.Event()
        self.cancel_event = cancel_event

        def respond(current: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.CANCEL and not close_button.get_sensitive():
                self.cancel_active_update()
                cancel_button.set_sensitive(False)
                return
            if response == Gtk.ResponseType.CLOSE and close_button.get_sensitive():
                current.close()

        dialog.connect("response", respond)

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
        dialog.get_content_area().append(scroll)
        dialog.present()

        def append(text: str) -> bool:
            buffer = output.get_buffer()
            end = buffer.get_end_iter()
            buffer.insert(end, text)
            output.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0.0, 1.0)
            return False

        def finish(message: str) -> bool:
            append(message)
            cancel_button.set_sensitive(False)
            close_button.set_sensitive(True)
            with self.process_lock:
                self.active_process = None
            self.cancel_event = None
            self.session.finish(message.strip())
            self.set_update_in_progress(False)
            self.refresh()
            return False

        commands = [["sudo", "-A", *command[1:]] if command[0] == "sudo" else command for command in commands]

        def worker() -> None:
            try:
                askpass_dir, askpass_path = create_sudo_askpass()
            except OSError as exc:
                GLib.idle_add(finish, f"Could not prepare administrator authentication: {exc}\n")
                return
            try:
                for command in commands:
                    if cancel_event.is_set():
                        GLib.idle_add(finish, "Update cancelled before the next source started.\n")
                        return
                    GLib.idle_add(append, f"$ {shlex.join(command)}\n")
                    child_env = os.environ.copy()
                    pass_fds: tuple[int, ...] = ()
                    read_fd: int | None = None
                    if cuc.update_command_needs_authentication(command):
                        read_fd, write_fd = os.pipe()
                        os.write(write_fd, ((password + "\n") * 32).encode("utf-8"))
                        os.close(write_fd)
                        child_env["SUDO_ASKPASS"] = askpass_path
                        child_env["CUC_ASKPASS_FD"] = str(read_fd)
                        pass_fds = (read_fd,)
                    try:
                        process = subprocess.Popen(
                            command,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                            env=child_env,
                            pass_fds=pass_fds,
                            start_new_session=True,
                        )
                    except OSError as exc:
                        if read_fd is not None:
                            os.close(read_fd)
                        GLib.idle_add(finish, f"Could not start command: {exc}\n")
                        return
                    if read_fd is not None:
                        os.close(read_fd)
                    with self.process_lock:
                        self.active_process = process
                    assert process.stdout is not None
                    for line in process.stdout:
                        GLib.idle_add(append, line)
                    if process.wait() != 0:
                        if cancel_event.is_set():
                            GLib.idle_add(finish, "Update cancelled. Review the output before continuing.\n")
                        else:
                            GLib.idle_add(finish, f"Update stopped with exit status {process.returncode}.\n")
                        return
                    with self.process_lock:
                        self.active_process = None
                GLib.idle_add(finish, "Updates completed successfully.\n")
            except Exception as exc:
                GLib.idle_add(finish, f"Update session failed unexpectedly: {exc}\n")
            finally:
                askpass_dir.cleanup()

        threading.Thread(target=worker, daemon=True).start()
        return False

    def cancel_active_update(self) -> None:
        self.session.request_cancel()
        if self.cancel_event:
            self.cancel_event.set()
        with self.process_lock:
            process = self.active_process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except OSError as exc:
                self.show_notice(f"Could not signal the update process: {exc}")

    def open_terminal(self) -> None:
        terminal = next((name for name in ("kgx", "gnome-terminal", "xterm") if shutil.which(name)), None)
        if terminal is None:
            self.show_notice("No terminal emulator was found.")
            return
        try:
            subprocess.Popen([terminal], start_new_session=True)
        except OSError as exc:
            self.show_notice(f"Could not open the terminal: {exc}")


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
