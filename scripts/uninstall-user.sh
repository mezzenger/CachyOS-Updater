#!/bin/sh
set -eu

cuc_data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
cuc_install_dir="$cuc_data_home/cachyos-update-center"
cuc_launcher="$cuc_data_home/applications/io.github.alvin.Cuc.desktop"

rm -rf -- "$cuc_install_dir"
rm -f -- "$cuc_launcher"
echo "Removed the per-user CachyOS Update Center installation."
