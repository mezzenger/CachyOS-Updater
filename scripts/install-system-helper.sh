#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root." >&2
  exit 77
fi

cuc_source_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
install -d -m 755 /usr/lib/cachyos-update-center
install -o root -g root -m 755 "$cuc_source_dir/cuc_privileged.py" \
  /usr/lib/cachyos-update-center/cuc-privileged
install -o root -g root -m 644 "$cuc_source_dir/packaging/io.github.alvin.Cuc.policy" \
  /usr/share/polkit-1/actions/io.github.alvin.Cuc.policy
echo "Installed the optional CUC Polkit helper."
