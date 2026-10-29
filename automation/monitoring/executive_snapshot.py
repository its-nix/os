#!/usr/bin/env python3
"""Render the sanitized executive-summary snapshot without touching monitor liveness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .monitor_snapshot import MAX_OUTPUT_BYTES, build_snapshot
except ImportError:
    from monitor_snapshot import MAX_OUTPUT_BYTES, build_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path.cwd() / "nix-os.yaml")
    parser.add_argument("--runtime", type=Path, help="synthetic JSON fixture; live collection is skipped")
    args = parser.parse_args(argv)
    snapshot = build_snapshot(args.manifest, args.runtime)
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_OUTPUT_BYTES:
        parser.error("sanitized portfolio snapshot exceeds configured output bound")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
