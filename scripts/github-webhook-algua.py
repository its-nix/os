#!/usr/bin/env python3
"""Bounded stdin adapter binding for Algua's governed GitHub repository."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.monitoring.github_webhook import adapt_payload, infer_event

try:
    raw = sys.stdin.buffer.read(256_001)
    if len(raw) > 256_000:
        raise ValueError("payload exceeds byte limit")
    payload = json.loads(raw)
    event = infer_event(payload)
    result = adapt_payload(event, payload, approved_repository="its-nix/algua") if event else None
    if result is not None:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print("[SILENT]")
except (ValueError, TypeError, json.JSONDecodeError):
    print("[SILENT]")
    sys.exit(0)
