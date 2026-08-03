#!/usr/bin/env python3
"""Minimal allowlisted helper intended to be launched through Polkit/pkexec."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


COMMANDS = {
    "pacman": ["/usr/bin/pacman", "-Syu", "--noconfirm"],
    "snap": ["/usr/bin/snap", "refresh"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one allowlisted CUC system update")
    parser.add_argument("source", choices=sorted(COMMANDS))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        print("This helper must be authorized through Polkit.", file=sys.stderr)
        return 77
    command = COMMANDS[args.source]
    return subprocess.call(command, stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
