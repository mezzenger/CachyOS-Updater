//! The GitHub updater's centered speech zigzag, rendered with GTK/Cairo.
use super::gtk::*;
use std::{ffi::c_int, time::Instant};

struct State {
    level: f64,
    started: Instant,
}
pub struct SpeechWave {
    pub widget: Widget,
    state: *mut State,
}
impl SpeechWave {
    pub unsafe fn new() -> Self {
        unsafe {
            let widget = gtk_drawing_area_new();
            gtk_widget_set_size_request(widget, -1, 64);
            gtk_widget_set_hexpand(widget, 1);
            gtk_widget_set_tooltip_text(
                widget,
                c"Follows speech volume; gently pulses while checking".as_ptr(),
            );
            gtk_accessible_update_property(widget, 4, c"Update voice waveform".as_ptr(), -1);
            let state = Box::into_raw(Box::new(State {
                level: 0.0,
                started: Instant::now(),
            }));
            gtk_drawing_area_set_draw_func(widget, Some(draw), state.cast(), Some(destroy));
            gtk_widget_set_visible(widget, 0);
            Self { widget, state }
        }
    }
    pub unsafe fn update(&self, checking: bool, speech: Option<f64>) {
        unsafe {
            let active = checking || speech.is_some();
            if (gtk_widget_get_visible(self.widget) != 0) != active {
                gtk_widget_set_visible(self.widget, active as c_int);
            }
            if !active || gtk_widget_get_mapped(self.widget) == 0 {
                return;
            }
            let mut animate: c_int = 1;
            g_object_get(
                gtk_settings_get_default(),
                c"gtk-enable-animations".as_ptr(),
                &mut animate as *mut c_int,
                std::ptr::null::<std::ffi::c_char>(),
            );
            let state = &mut *self.state;
            let target = if animate == 0 {
                0.12
            } else {
                speech.unwrap_or_else(|| {
                    0.08 + 0.05 * (1.0 + (state.started.elapsed().as_secs_f64() * 3.2).sin())
                })
            };
            let next = if animate == 0 {
                target
            } else {
                state.level + (target - state.level) * 0.65
            };
            if (next - state.level).abs() > 0.0001 {
                state.level = next;
                gtk_widget_queue_draw(self.widget);
            }
        }
    }
}
unsafe extern "C" fn destroy(data: Widget) {
    unsafe {
        drop(Box::from_raw(data as *mut State));
    }
}
unsafe extern "C" fn draw(_: Widget, cr: Widget, width: c_int, height: c_int, data: Widget) {
    unsafe {
        let level = (*(data as *const State)).level;
        let span = 92.0 + 72.0 * level;
        let amplitude = 3.0 + 23.0 * level;
        let left = (width as f64 - span) / 2.0;
        let center = height as f64 / 2.0;
        cairo_set_source_rgb(cr, 60.0 / 255.0, 129.0 / 255.0, 87.0 / 255.0);
        cairo_set_line_width(cr, 3.0);
        cairo_set_line_cap(cr, 1);
        cairo_set_line_join(cr, 1);
        cairo_move_to(cr, left, center);
        for i in 1..16 {
            let taper = (i as f64 * std::f64::consts::PI / 16.0).sin();
            let sign = if i % 2 == 1 { -1.0 } else { 1.0 };
            cairo_line_to(
                cr,
                left + span * i as f64 / 16.0,
                center + sign * amplitude * taper,
            );
        }
        cairo_line_to(cr, left + span, center);
        cairo_stroke(cr);
    }
}
