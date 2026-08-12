# Accessibility and Keyboard Support

The desktop application uses visible labels for primary actions, accessible
names for icon-only controls, a natural top-to-bottom focus order, and standard
GTK controls that expose their state to assistive technologies.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+R` | Refresh update status |
| `Ctrl+,` | Open Settings |
| `Ctrl+U` | Review installable updates |
| `Ctrl+D` | Export a privacy-safe diagnostic bundle |
| `Enter` | Activate the focused button or accept the default dialog action |
| `Escape` | Close or cancel the active dialog where GTK permits cancellation |

## Review checklist

- Icon-only controls have accessible labels and tooltips.
- Update sources use text in addition to color for every state.
- Review choices use native check buttons with exposed selected state.
- AUR authentication and package review use the user's accessible terminal;
  the desktop application does not collect passwords.
- Long package and command output is selectable and scrollable.
- Destructive or privileged actions require an explicit review and confirmation.
- Focus is moved to the primary action for package-list and review dialogs.
- Status messages remain visible after checks and installations.

Visual and screen-reader behavior should be manually checked on GTK releases
supported by CachyOS because assistive-technology integration cannot be fully
validated in headless CI.
