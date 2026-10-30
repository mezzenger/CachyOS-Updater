#!/bin/sh
set -eu

cuc_data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
cuc_install_dir="$cuc_data_home/cachyos-update-center"
cuc_launcher="$cuc_data_home/applications/io.github.alvin.Cuc.desktop"
cuc_icon="$cuc_data_home/icons/hicolor/scalable/apps/io.github.alvin.Cuc.svg"

rm -rf -- "$cuc_install_dir"
rm -f -- "$cuc_launcher" "$cuc_icon"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache --force --ignore-theme-index "$cuc_data_home/icons/hicolor"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$cuc_data_home/applications"
fi
echo "Removed the per-user CachyOS Update Center installation."
