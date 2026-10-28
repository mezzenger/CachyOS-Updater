//! Small GTK4 C ABI surface. GTK owns widget trees; the application state owns
//! callbacks until g_application_run returns. All calls occur on the main thread.
//! This avoids a second binding toolchain and uses the system GTK/libadwaita ABI.
#![allow(unsafe_op_in_unsafe_fn)]
use std::ffi::{CStr, CString, c_char, c_int, c_uint, c_void};
pub type Widget = *mut c_void;
pub type Callback = unsafe extern "C" fn();
#[link(name = "gtk-4")]
unsafe extern "C" {
    pub fn gtk_drawing_area_new() -> Widget;
    pub fn gtk_drawing_area_set_draw_func(
        w: Widget,
        draw: Option<unsafe extern "C" fn(Widget, Widget, c_int, c_int, Widget)>,
        data: Widget,
        destroy: Option<unsafe extern "C" fn(Widget)>,
    );
    pub fn gtk_widget_queue_draw(w: Widget);
    pub fn gtk_widget_get_mapped(w: Widget) -> c_int;
    pub fn gtk_widget_get_visible(w: Widget) -> c_int;
    pub fn gtk_settings_get_default() -> Widget;
    pub fn gtk_application_new(id: *const c_char, flags: c_int) -> Widget;
    pub fn gtk_window_set_title(w: Widget, title: *const c_char);
    pub fn gtk_window_set_default_size(w: Widget, width: c_int, height: c_int);
    pub fn gtk_window_get_default_size(w: Widget, width: *mut c_int, height: *mut c_int);
    pub fn gtk_window_present(w: Widget);
    pub fn gtk_window_close(w: Widget);
    pub fn gtk_event_controller_key_new() -> Widget;
    pub fn gtk_event_controller_set_propagation_phase(w: Widget, phase: c_int);
    pub fn gtk_widget_add_controller(w: Widget, controller: Widget);
    pub fn gtk_window_is_maximized(w: Widget) -> c_int;
    pub fn gtk_window_maximize(w: Widget);
    pub fn gtk_widget_set_visible(w: Widget, visible: c_int);
    pub fn gtk_widget_set_sensitive(w: Widget, sensitive: c_int);
    pub fn gtk_widget_set_hexpand(w: Widget, expand: c_int);
    pub fn gtk_widget_set_vexpand(w: Widget, expand: c_int);
    pub fn gtk_widget_set_margin_top(w: Widget, margin: c_int);
    pub fn gtk_widget_set_margin_bottom(w: Widget, margin: c_int);
    pub fn gtk_widget_set_margin_start(w: Widget, margin: c_int);
    pub fn gtk_widget_set_margin_end(w: Widget, margin: c_int);
    pub fn gtk_widget_add_css_class(w: Widget, name: *const c_char);
    pub fn gtk_accessible_update_property(w: Widget, first_property: c_int, ...);
    pub fn gtk_widget_set_tooltip_text(w: Widget, text: *const c_char);
    pub fn gtk_widget_remove_css_class(w: Widget, name: *const c_char);
    pub fn gtk_widget_set_size_request(w: Widget, width: c_int, height: c_int);
    pub fn gtk_widget_set_halign(w: Widget, align: c_int);
    pub fn gtk_widget_set_valign(w: Widget, align: c_int);
    pub fn gtk_grid_new() -> Widget;
    pub fn gtk_grid_set_column_homogeneous(w: Widget, homogeneous: c_int);
    pub fn gtk_grid_set_row_homogeneous(w: Widget, homogeneous: c_int);
    pub fn gtk_grid_set_column_spacing(w: Widget, spacing: c_uint);
    pub fn gtk_grid_attach(
        w: Widget,
        child: Widget,
        col: c_int,
        row: c_int,
        width: c_int,
        height: c_int,
    );
    pub fn gtk_button_new_from_icon_name(name: *const c_char) -> Widget;
    pub fn gtk_button_set_icon_name(w: Widget, name: *const c_char);
    pub fn gtk_image_new_from_icon_name(name: *const c_char) -> Widget;
    pub fn gtk_label_set_selectable(w: Widget, selectable: c_int);
    pub fn gtk_scrolled_window_set_policy(w: Widget, horizontal: c_int, vertical: c_int);
    pub fn gtk_css_provider_new() -> Widget;
    pub fn gtk_css_provider_load_from_data(provider: Widget, data: *const c_char, len: isize);
    pub fn gtk_style_context_add_provider_for_display(
        display: Widget,
        provider: Widget,
        priority: c_uint,
    );
    pub fn gdk_display_get_default() -> Widget;
    pub fn gtk_string_list_new(strings: *const *const c_char) -> Widget;
    pub fn gtk_widget_get_width(w: Widget) -> c_int;
    pub fn gtk_widget_get_height(w: Widget) -> c_int;
    pub fn gtk_widget_paintable_new(w: Widget) -> Widget;
    pub fn gtk_snapshot_new() -> Widget;
    pub fn gdk_paintable_snapshot(p: Widget, snapshot: Widget, width: f64, height: f64);
    pub fn gtk_snapshot_to_node(snapshot: Widget) -> Widget;
    pub fn gtk_native_get_renderer(w: Widget) -> Widget;
    pub fn gsk_renderer_render_texture(renderer: Widget, node: Widget, viewport: Widget) -> Widget;
    pub fn gdk_texture_save_to_png(texture: Widget, filename: *const c_char) -> c_int;
    pub fn gsk_render_node_unref(node: Widget);
    pub fn gtk_image_new_from_paintable(paintable: Widget) -> Widget;
    pub fn gtk_image_set_pixel_size(w: Widget, size: c_int);
    pub fn gdk_texture_new_from_bytes(bytes: Widget, error: *mut Widget) -> Widget;
    pub fn gtk_box_new(orientation: c_int, spacing: c_int) -> Widget;
    pub fn gtk_box_append(w: Widget, child: Widget);
    pub fn gtk_label_new(text: *const c_char) -> Widget;
    pub fn gtk_label_set_text(w: Widget, text: *const c_char);
    pub fn gtk_label_set_wrap(w: Widget, wrap: c_int);
    pub fn gtk_label_set_xalign(w: Widget, align: f32);
    pub fn gtk_button_new_with_label(text: *const c_char) -> Widget;
    pub fn gtk_check_button_new_with_label(text: *const c_char) -> Widget;
    pub fn gtk_check_button_get_active(w: Widget) -> c_int;
    pub fn gtk_check_button_set_active(w: Widget, active: c_int);
    pub fn gtk_text_view_set_cursor_visible(w: Widget, visible: c_int);
    pub fn gtk_editable_set_text(w: Widget, text: *const c_char);
    pub fn gtk_editable_get_text(w: Widget) -> *const c_char;
    pub fn gtk_text_view_new() -> Widget;
    pub fn gtk_text_view_set_editable(w: Widget, editable: c_int);
    pub fn gtk_text_view_set_monospace(w: Widget, mono: c_int);
    pub fn gtk_text_view_set_wrap_mode(w: Widget, mode: c_int);
    pub fn gtk_text_view_get_buffer(w: Widget) -> Widget;
    pub fn gtk_text_buffer_set_text(w: Widget, text: *const c_char, len: c_int);
    pub fn gtk_text_buffer_insert_at_cursor(w: Widget, text: *const c_char, len: c_int);
    pub fn gtk_scrolled_window_new() -> Widget;
    pub fn gtk_scrolled_window_set_child(w: Widget, child: Widget);
    pub fn gtk_scrolled_window_set_min_content_height(w: Widget, height: c_int);
    pub fn gtk_widget_get_clipboard(w: Widget) -> Widget;
    pub fn gtk_application_inhibit(
        app: Widget,
        window: Widget,
        flags: c_uint,
        reason: *const c_char,
    ) -> c_uint;
    pub fn gtk_application_uninhibit(app: Widget, cookie: c_uint);
}
#[link(name = "adwaita-1")]
unsafe extern "C" {
    pub fn adw_init();
    pub fn adw_dialog_new() -> Widget;
    pub fn adw_dialog_set_child(w: Widget, child: Widget);
    pub fn adw_dialog_set_can_close(w: Widget, can_close: c_int);
    pub fn adw_header_bar_pack_start(w: Widget, child: Widget);
    pub fn adw_alert_dialog_new(heading: *const c_char, body: *const c_char) -> Widget;
    pub fn adw_alert_dialog_set_heading(w: Widget, heading: *const c_char);
    pub fn adw_alert_dialog_set_body(w: Widget, body: *const c_char);
    pub fn adw_alert_dialog_add_response(w: Widget, id: *const c_char, label: *const c_char);
    pub fn adw_alert_dialog_set_response_appearance(
        w: Widget,
        id: *const c_char,
        appearance: c_int,
    );
    pub fn adw_alert_dialog_get_response_enabled(w: Widget, id: *const c_char) -> c_int;
    pub fn adw_alert_dialog_set_response_enabled(w: Widget, id: *const c_char, enabled: c_int);
    pub fn adw_alert_dialog_set_default_response(w: Widget, id: *const c_char);
    pub fn adw_alert_dialog_set_close_response(w: Widget, id: *const c_char);
    pub fn adw_alert_dialog_set_extra_child(w: Widget, child: Widget);
    pub fn adw_application_window_new(app: Widget) -> Widget;
    pub fn adw_application_window_set_content(w: Widget, content: Widget);
    pub fn adw_toolbar_view_new() -> Widget;
    pub fn adw_toolbar_view_set_content(w: Widget, content: Widget);
    pub fn adw_toolbar_view_add_top_bar(w: Widget, bar: Widget);
    pub fn adw_toolbar_view_add_bottom_bar(w: Widget, bar: Widget);
    pub fn adw_header_bar_new() -> Widget;
    pub fn adw_header_bar_pack_end(w: Widget, child: Widget);
    pub fn adw_clamp_new() -> Widget;
    pub fn adw_clamp_set_maximum_size(w: Widget, size: c_int);
    pub fn adw_clamp_set_tightening_threshold(w: Widget, threshold: c_int);
    pub fn adw_clamp_set_child(w: Widget, child: Widget);
    pub fn adw_preferences_dialog_new() -> Widget;
    pub fn adw_preferences_dialog_add(w: Widget, page: Widget);
    pub fn adw_dialog_set_title(w: Widget, title: *const c_char);
    pub fn adw_dialog_set_content_width(w: Widget, width: c_int);
    pub fn adw_dialog_set_content_height(w: Widget, height: c_int);
    pub fn adw_dialog_present(w: Widget, parent: Widget);
    pub fn adw_dialog_close(w: Widget) -> c_int;
    pub fn adw_dialog_force_close(w: Widget);
    pub fn adw_preferences_page_new() -> Widget;
    pub fn adw_preferences_page_add(w: Widget, group: Widget);
    pub fn adw_preferences_group_new() -> Widget;
    pub fn adw_preferences_group_set_title(w: Widget, title: *const c_char);
    pub fn adw_preferences_group_set_description(w: Widget, description: *const c_char);
    pub fn adw_preferences_group_add(w: Widget, child: Widget);
    pub fn adw_preferences_row_set_title(w: Widget, title: *const c_char);
    pub fn adw_preferences_row_set_use_markup(w: Widget, markup: c_int);
    pub fn adw_switch_row_new() -> Widget;
    pub fn adw_switch_row_get_active(w: Widget) -> c_int;
    pub fn adw_switch_row_set_active(w: Widget, active: c_int);
    pub fn adw_entry_row_new() -> Widget;
    pub fn adw_spin_row_new_with_range(min: f64, max: f64, step: f64) -> Widget;
    pub fn adw_spin_row_get_value(w: Widget) -> f64;
    pub fn adw_spin_row_set_value(w: Widget, value: f64);
    pub fn adw_action_row_new() -> Widget;
    pub fn adw_action_row_set_subtitle(w: Widget, subtitle: *const c_char);
    pub fn adw_action_row_add_prefix(w: Widget, child: Widget);
    pub fn adw_action_row_add_suffix(w: Widget, child: Widget);
    pub fn adw_action_row_set_activatable_widget(w: Widget, child: Widget);
    pub fn adw_combo_row_new() -> Widget;
    pub fn adw_combo_row_set_model(w: Widget, model: Widget);
    pub fn adw_combo_row_set_selected(w: Widget, selected: c_uint);
    pub fn adw_combo_row_get_selected(w: Widget) -> c_uint;
    pub fn adw_about_dialog_new() -> Widget;
    pub fn adw_about_dialog_set_application_name(w: Widget, name: *const c_char);
    pub fn adw_about_dialog_set_application_icon(w: Widget, name: *const c_char);
    pub fn adw_about_dialog_set_version(w: Widget, version: *const c_char);
    pub fn adw_about_dialog_set_comments(w: Widget, comments: *const c_char);
}
#[link(name = "gio-2.0")]
unsafe extern "C" {
    pub fn g_application_run(app: Widget, argc: c_int, argv: *mut *mut c_char) -> c_int;
    pub fn g_application_quit(app: Widget);
    pub fn g_notification_new(title: *const c_char) -> Widget;
    pub fn g_notification_set_body(n: Widget, body: *const c_char);
    pub fn g_application_send_notification(app: Widget, id: *const c_char, notification: Widget);
}
#[link(name = "gobject-2.0")]
unsafe extern "C" {
    pub fn g_signal_connect_data(
        instance: Widget,
        signal: *const c_char,
        callback: Option<Callback>,
        data: Widget,
        destroy: Option<unsafe extern "C" fn(Widget, Widget)>,
        flags: c_int,
    ) -> u64;
    pub fn g_object_unref(object: Widget);
    pub fn g_object_ref_sink(object: Widget) -> Widget;
}
#[link(name = "glib-2.0")]
unsafe extern "C" {
    pub fn g_timeout_add(
        interval: c_uint,
        callback: Option<unsafe extern "C" fn(Widget) -> c_int>,
        data: Widget,
    ) -> c_uint;
    pub fn g_source_remove(id: c_uint) -> c_int;
    pub fn g_bytes_new_static(data: *const c_void, size: usize) -> Widget;
    pub fn g_bytes_unref(bytes: Widget);
}
#[link(name = "gtk-4")]
unsafe extern "C" {
    pub fn gdk_clipboard_set_text(clipboard: Widget, text: *const c_char);
}
pub fn c(s: &str) -> CString {
    CString::new(s.replace('\0', "�")).unwrap()
}
pub unsafe fn label(s: &str) -> Widget {
    let w = gtk_label_new(c(s).as_ptr());
    gtk_label_set_wrap(w, 0);
    gtk_label_set_xalign(w, 0.0);
    w
}
pub unsafe fn button(s: &str) -> Widget {
    gtk_button_new_with_label(c(s).as_ptr())
}
pub unsafe fn set_label(w: Widget, s: &str) {
    gtk_label_set_text(w, c(s).as_ptr());
}
pub unsafe fn entry_text(w: Widget) -> String {
    CStr::from_ptr(gtk_editable_get_text(w))
        .to_string_lossy()
        .into()
}
pub unsafe fn text(w: Widget, s: &str) {
    gtk_text_buffer_set_text(gtk_text_view_get_buffer(w), c(s).as_ptr(), -1);
}
pub unsafe fn append(w: Widget, s: &str) {
    gtk_text_buffer_insert_at_cursor(gtk_text_view_get_buffer(w), c(s).as_ptr(), -1);
}
pub unsafe fn margins(w: Widget, n: i32) {
    gtk_widget_set_margin_top(w, n);
    gtk_widget_set_margin_bottom(w, n);
    gtk_widget_set_margin_start(w, n);
    gtk_widget_set_margin_end(w, n);
}
pub unsafe fn scroll(child: Widget, height: i32) -> Widget {
    let w = gtk_scrolled_window_new();
    gtk_scrolled_window_set_child(w, child);
    gtk_scrolled_window_set_min_content_height(w, height);
    gtk_widget_set_vexpand(w, 1);
    gtk_widget_set_hexpand(w, 1);
    w
}
pub unsafe fn view() -> Widget {
    let w = gtk_text_view_new();
    gtk_text_view_set_editable(w, 0);
    gtk_text_view_set_monospace(w, 1);
    gtk_text_view_set_wrap_mode(w, 3);
    margins(w, 8);
    w
}
pub unsafe fn connect(
    w: Widget,
    signal: &str,
    callback: unsafe extern "C" fn(Widget, Widget),
    data: Widget,
) {
    g_signal_connect_data(
        w,
        c(signal).as_ptr(),
        Some(std::mem::transmute::<
            unsafe extern "C" fn(Widget, Widget),
            Callback,
        >(callback)),
        data,
        None,
        0,
    );
}
pub unsafe fn connect_close(
    w: Widget,
    callback: unsafe extern "C" fn(Widget, Widget) -> c_int,
    data: Widget,
) {
    g_signal_connect_data(
        w,
        c"close-request".as_ptr(),
        Some(std::mem::transmute::<
            unsafe extern "C" fn(Widget, Widget) -> c_int,
            Callback,
        >(callback)),
        data,
        None,
        0,
    );
}

pub unsafe fn source_icon(source: &str) -> Widget {
    let bytes: &'static [u8] = if source == "pacman" {
        include_bytes!("../assets/source-icons/pacman.png")
    } else {
        include_bytes!("../assets/source-icons/flatpak.png")
    };
    let data = g_bytes_new_static(bytes.as_ptr().cast(), bytes.len());
    let texture = gdk_texture_new_from_bytes(data, std::ptr::null_mut());
    let image = gtk_image_new_from_paintable(texture);
    gtk_image_set_pixel_size(image, 32);
    if !texture.is_null() {
        g_object_unref(texture);
    }
    g_bytes_unref(data);
    image
}

pub unsafe fn icon_button(icon: &str, tooltip: &str) -> Widget {
    let w = gtk_button_new_from_icon_name(c(icon).as_ptr());
    gtk_widget_set_tooltip_text(w, c(tooltip).as_ptr());
    // GTK_ACCESSIBLE_PROPERTY_LABEL = 4; string property, terminated by -1.
    gtk_accessible_update_property(w, 4, c(tooltip).as_ptr(), -1i32);
    w
}
pub unsafe fn css(w: Widget, class: &str) {
    gtk_widget_add_css_class(w, c(class).as_ptr());
}
pub unsafe fn row_title(w: Widget, title: &str) {
    adw_preferences_row_set_use_markup(w, 0);
    adw_preferences_row_set_title(w, c(title).as_ptr());
}
pub unsafe fn class_status(w: Widget, class: &str, classes: &[&str]) {
    for name in classes {
        gtk_widget_remove_css_class(w, c(name).as_ptr());
    }
    if !class.is_empty() {
        css(w, class);
    }
}
pub unsafe fn app_icon() -> Widget {
    let bytes = include_bytes!("../assets/io.github.alvin.Cuc.svg");
    let data = g_bytes_new_static(bytes.as_ptr().cast(), bytes.len());
    let texture = gdk_texture_new_from_bytes(data, std::ptr::null_mut());
    let image = gtk_image_new_from_paintable(texture);
    gtk_image_set_pixel_size(image, 48);
    if !texture.is_null() {
        g_object_unref(texture);
    }
    g_bytes_unref(data);
    image
}
/// Render an application-owned widget for offline visual QA without a desktop capture.
pub unsafe fn snapshot_window(window: Widget, path: &std::path::Path) -> Result<(), String> {
    let width = gtk_widget_get_width(window);
    let height = gtk_widget_get_height(window);
    let paintable = gtk_widget_paintable_new(window);
    let snapshot = gtk_snapshot_new();
    gdk_paintable_snapshot(paintable, snapshot, width as f64, height as f64);
    let node = gtk_snapshot_to_node(snapshot);
    g_object_unref(snapshot);
    g_object_unref(paintable);
    if node.is_null() {
        return Err("Window has not been drawn".into());
    }
    let renderer = gtk_native_get_renderer(window);
    let texture = gsk_renderer_render_texture(renderer, node, std::ptr::null_mut());
    gsk_render_node_unref(node);
    if texture.is_null() {
        return Err("Could not render window".into());
    }
    let saved = gdk_texture_save_to_png(texture, c(&path.display().to_string()).as_ptr());
    g_object_unref(texture);
    if saved == 0 {
        return Err("Could not save window image".into());
    }
    Ok(())
}

#[link(name = "cairo")]
unsafe extern "C" {
    pub fn cairo_set_source_rgb(cr: Widget, r: f64, g: f64, b: f64);
    pub fn cairo_set_line_width(cr: Widget, width: f64);
    pub fn cairo_set_line_cap(cr: Widget, cap: c_int);
    pub fn cairo_set_line_join(cr: Widget, join: c_int);
    pub fn cairo_move_to(cr: Widget, x: f64, y: f64);
    pub fn cairo_line_to(cr: Widget, x: f64, y: f64);
    pub fn cairo_stroke(cr: Widget);
}
#[link(name = "gobject-2.0")]
unsafe extern "C" {
    pub fn g_object_get(object: Widget, first: *const c_char, ...);
}
