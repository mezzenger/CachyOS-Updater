#!/bin/sh
set -eu

cuc_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cuc_data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
cuc_install_dir="$cuc_data_home/cachyos-update-center"
cuc_application_dir="$cuc_data_home/applications"

mkdir -p "$cuc_install_dir/data" "$cuc_application_dir"
install -m 755 "$cuc_source_dir/cuc.py" "$cuc_install_dir/cuc.py"
install -m 755 "$cuc_source_dir/cuc_desktop.py" "$cuc_install_dir/cuc_desktop.py"
install -m 644 "$cuc_source_dir/data/aur-security-advisories.json" \
  "$cuc_install_dir/data/aur-security-advisories.json"
sed "s|^Exec=.*|Exec=$cuc_install_dir/cuc_desktop.py|" \
  "$cuc_source_dir/io.github.alvin.Cuc.desktop" \
  | sed "s|^TryExec=.*|TryExec=$cuc_install_dir/cuc_desktop.py|" \
  > "$cuc_application_dir/io.github.alvin.Cuc.desktop"
chmod 644 "$cuc_application_dir/io.github.alvin.Cuc.desktop"

echo "Installed CachyOS Update Center for the current user."
echo "Application files: $cuc_install_dir"
