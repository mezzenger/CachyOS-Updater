#!/bin/sh
set -eu

cuc_source_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cuc_data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
cuc_install_root="$cuc_data_home/cachyos-update-center"
cuc_version=$(sed -n 's/^version = "\([0-9][0-9A-Za-z.+-]*\)"$/\1/p' "$cuc_source_dir/Cargo.toml")
if [ -z "$cuc_version" ]; then
  echo "Could not determine the application version." >&2
  exit 1
fi
cuc_install_dir="$cuc_install_root/$cuc_version"
cuc_current_dir="$cuc_install_root/current"
cuc_application_dir="$cuc_data_home/applications"
cuc_icon_dir="$cuc_data_home/icons/hicolor/scalable/apps"

# Build without modifying an installed release until compilation succeeds.
(cd "$cuc_source_dir" && cargo build --release --locked)
mkdir -p "$cuc_install_dir" "$cuc_application_dir" "$cuc_icon_dir"
# Replace atomically so an open application can keep using its current inode.
cuc_pending_binary=$(mktemp "$cuc_install_dir/.cuc-desktop.XXXXXX")
trap 'rm -f -- "$cuc_pending_binary"' EXIT HUP INT TERM
install -m 755 "$cuc_source_dir/target/release/cuc-desktop" "$cuc_pending_binary"
mv -f -- "$cuc_pending_binary" "$cuc_install_dir/cuc-desktop"
trap - EXIT HUP INT TERM
mkdir -p "$cuc_install_dir/assets/source-icons"
install -m 644 "$cuc_source_dir/assets/source-icons/pacman.png" \
  "$cuc_source_dir/assets/source-icons/flatpak.png" "$cuc_install_dir/assets/source-icons/"
ln -sfn "$cuc_version" "$cuc_current_dir"
sed "s|^Exec=.*|Exec=$cuc_current_dir/cuc-desktop|" \
  "$cuc_source_dir/io.github.alvin.Cuc.desktop" \
  | sed "s|^TryExec=.*|TryExec=$cuc_current_dir/cuc-desktop|" \
  > "$cuc_application_dir/io.github.alvin.Cuc.desktop"
chmod 644 "$cuc_application_dir/io.github.alvin.Cuc.desktop"
install -m 644 "$cuc_source_dir/assets/io.github.alvin.Cuc.svg" \
  "$cuc_icon_dir/io.github.alvin.Cuc.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache --force --ignore-theme-index "$cuc_data_home/icons/hicolor"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$cuc_application_dir"
fi

echo "Installed CachyOS Update Center for the current user."
echo "Versioned application files: $cuc_install_dir"
echo "Active release: $cuc_current_dir"
