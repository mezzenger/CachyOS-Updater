//! Shared wording for the original compact dashboard and Piper announcements.
use crate::{
    config::Config,
    updates::{Outcome, UpdateResult},
};

pub struct InstallationReport {
    pub title: &'static str,
    pub body: String,
    pub speech: String,
}

/// Describe recorded outcomes only; a completed process may have failed,
/// timed out, or been cancelled, and a preflight error installed nothing.
pub fn installation_report(
    result: &Result<Vec<Outcome>, String>,
    reboot: bool,
) -> InstallationReport {
    let successful = result.as_ref().is_ok_and(|outcomes| {
        !outcomes.is_empty() && outcomes.iter().all(|o| o.status == "succeeded")
    });
    let title = if successful {
        "Updates completed"
    } else {
        "Updates need attention"
    };
    let details = match result {
        Err(e) => format!("Installation blocked: {e}"),
        Ok(outcomes) if outcomes.is_empty() => "No update results were recorded.".into(),
        Ok(outcomes) => outcomes
            .iter()
            .map(|o| {
                let status = match o.status.as_str() {
                    "succeeded" => "Updated successfully",
                    "cancelled" => "Cancelled",
                    "timed-out" => "Timed out",
                    "skipped" => "Skipped",
                    _ => "Failed",
                };
                let detail = if o.status == "succeeded" {
                    String::new()
                } else {
                    format!("\n{}", o.message)
                };
                format!("{}: {status}{detail}", name(&o.source))
            })
            .collect::<Vec<_>>()
            .join("\n\n"),
    };
    let reboot_note = if reboot {
        "\n\nA reboot is recommended."
    } else {
        ""
    };
    InstallationReport {
        title,
        body: format!(
            "{details}{reboot_note}\n\nFull command output is saved in the application log."
        ),
        speech: format!("{title}. {details}{reboot_note}"),
    }
}
pub fn name(source: &str) -> &str {
    match source {
        "pacman" => "Pacman",
        "flatpak" => "Flatpak",
        s => s,
    }
}
pub struct CardText {
    pub status: String,
    pub count: String,
    pub detail: String,
    pub class: &'static str,
    pub actions: bool,
}
pub fn card(result: Option<&UpdateResult>) -> CardText {
    let (status, count, detail, class, actions) = match result {
        None => (
            "Checking…".into(),
            "—".into(),
            "Contacting this update source.".into(),
            "",
            false,
        ),
        Some(r) if !r.enabled => (
            "Disabled".into(),
            "—".into(),
            "Enable this source in Settings to include it in update checks.".into(),
            "status-ok",
            false,
        ),
        Some(r) if !r.available_on_system => (
            "Not installed".into(),
            "—".into(),
            r.error.clone().unwrap_or_default(),
            "status-error",
            false,
        ),
        Some(r) if r.error.is_some() => (
            "Check failed".into(),
            "!".into(),
            r.error.clone().unwrap_or_default(),
            "status-error",
            false,
        ),
        Some(r) if !r.items.is_empty() => (
            format!(
                "{} {} ready",
                r.items.len(),
                if r.items.len() == 1 {
                    "update"
                } else {
                    "updates"
                }
            ),
            r.items.len().to_string(),
            "Review package details, or install when ready.".into(),
            "status-warn",
            true,
        ),
        Some(_) => (
            "Up to date".into(),
            "0".into(),
            "Nothing pending from this source.".into(),
            "status-ok",
            false,
        ),
    };
    CardText {
        status,
        count,
        detail,
        class,
        actions,
    }
}
pub struct Overview {
    pub headline: String,
    pub subtitle: String,
    pub summary: String,
    pub badge: &'static str,
    pub class: &'static str,
}
pub fn overview(results: &[UpdateResult], reboot: bool) -> Overview {
    let failed: Vec<_> = results
        .iter()
        .filter(|r| r.enabled && r.available_on_system && r.error.is_some())
        .map(|r| name(&r.source))
        .collect();
    let total: usize = results
        .iter()
        .filter(|r| r.eligible())
        .map(|r| r.items.len())
        .sum();
    let sources = results.iter().filter(|r| r.eligible()).count();
    if !failed.is_empty() {
        return Overview {
            headline: "Update check needs attention".into(),
            subtitle: format!("Could not check: {}", failed.join(", ")),
            summary: "Install actions are available only for sources checked successfully".into(),
            badge: "Needs attention",
            class: "overview-error",
        };
    }
    if total > 0 {
        return Overview {
            headline: format!(
                "{total} installable update{}",
                if total == 1 { "" } else { "s" }
            ),
            subtitle: "Review package details, or update all eligible sources now.".into(),
            summary: format!(
                "{total} installable update{} across {sources} source{}",
                if total == 1 { "" } else { "s" },
                if sources == 1 { "" } else { "s" }
            ),
            badge: "Ready to install",
            class: "overview-ready",
        };
    }
    let unavailable: Vec<_> = results
        .iter()
        .filter(|r| r.enabled && !r.available_on_system)
        .map(|r| name(&r.source))
        .collect();
    let suffix = if unavailable.is_empty() {
        String::new()
    } else {
        format!(" Unavailable: {}.", unavailable.join(", "))
    };
    Overview {
        headline: if results.iter().all(|r| !r.enabled) {
            "No update sources enabled".into()
        } else {
            "Annoyingly current".into()
        },
        subtitle: format!(
            "Your system is up to date.{}{suffix}",
            if reboot {
                " A reboot is recommended."
            } else {
                ""
            }
        ),
        summary: "No updates are waiting to be installed".into(),
        badge: "",
        class: "overview-current",
    }
}
pub fn speech(results: &[UpdateResult], config: &Config, reboot: bool) -> String {
    let failed: Vec<_> = results
        .iter()
        .filter(|r| r.enabled && r.available_on_system && r.error.is_some())
        .map(|r| name(&r.source))
        .collect();
    if !failed.is_empty() {
        return format!(
            "Update check incomplete. Could not check {}.",
            failed.join(", ")
        );
    }
    if !results.iter().any(|r| r.enabled && !r.items.is_empty()) {
        if reboot {
            return format!(
                "{}.. Your system is up to date. A reboot is recommended.",
                config.spoken_name
            );
        }
        return format!(
            "{}.. Your system is currently up to date and is operating nominally!",
            config.spoken_name
        );
    }
    results
        .iter()
        .filter(|r| r.enabled && !r.items.is_empty())
        .map(|r| format!("{} has {} updates.", name(&r.source), r.items.len()))
        .collect::<Vec<_>>()
        .join(" ")
}
