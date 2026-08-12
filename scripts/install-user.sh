#!/bin/sh
set -eu

cuc_source_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cuc_data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
cuc_install_root="$cuc_data_home/cachyos-update-center"
cuc_version=$(sed -n 's/^__version__ = "\([0-9][0-9A-Za-z.+-]*\)"$/\1/p' "$cuc_source_dir/cuc_backend.py")
if [ -z "$cuc_version" ]; then
  echo "Could not determine the application version." >&2
  exit 1
fi
cuc_install_dir="$cuc_install_root/$cuc_version"
cuc_current_dir="$cuc_install_root/current"
cuc_application_dir="$cuc_data_home/applications"

mkdir -p "$cuc_install_dir/data" "$cuc_application_dir"
rm -f -- "$cuc_install_dir/cuc.py"
install -m 755 "$cuc_source_dir/cuc_desktop.py" "$cuc_install_dir/cuc_desktop.py"
install -m 644 "$cuc_source_dir/cuc_backend.py" "$cuc_install_dir/cuc_backend.py"
install -m 644 "$cuc_source_dir/cuc_advisory.py" "$cuc_install_dir/cuc_advisory.py"
install -m 644 "$cuc_source_dir/cuc_controller.py" "$cuc_install_dir/cuc_controller.py"
install -m 644 "$cuc_source_dir/cuc_diagnostics.py" "$cuc_install_dir/cuc_diagnostics.py"
install -m 644 "$cuc_source_dir/cuc_providers.py" "$cuc_install_dir/cuc_providers.py"
install -m 644 "$cuc_source_dir/data/aur-security-advisories.json" \
  "$cuc_install_dir/data/aur-security-advisories.json"
ln -sfn "$cuc_version" "$cuc_current_dir"
sed "s|^Exec=.*|Exec=$cuc_current_dir/cuc_desktop.py|" \
  "$cuc_source_dir/io.github.alvin.Cuc.desktop" \
  | sed "s|^TryExec=.*|TryExec=$cuc_current_dir/cuc_desktop.py|" \
  > "$cuc_application_dir/io.github.alvin.Cuc.desktop"
chmod 644 "$cuc_application_dir/io.github.alvin.Cuc.desktop"

echo "Installed CachyOS Update Center for the current user."
echo "Versioned application files: $cuc_install_dir"
echo "Active release: $cuc_current_dir"
