#!/usr/bin/env python3
"""Validate and sign an AUR advisory with an offline Ed25519 private key."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("advisory", type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = args.advisory.read_bytes()
    # Reject malformed JSON before producing a release signature. Runtime
    # schema validation remains authoritative in cuc_advisory.py.
    json.loads(payload.decode("utf-8"))
    private_key = serialization.load_pem_private_key(
        args.private_key.read_bytes(),
        password=None,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("The advisory signing key must be Ed25519.")
    destination = args.output or args.advisory.with_suffix(args.advisory.suffix + ".sig")
    destination.write_bytes(base64.b64encode(private_key.sign(payload)) + b"\n")
    destination.chmod(0o644)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
