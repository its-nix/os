#!/usr/bin/env python3
"""Preview native agent-mode route argv; never executes subscriptions."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def render():
    contract = json.loads((ROOT / "automation/monitoring/github-webhook-routes.json").read_text())
    manifest = json.loads((ROOT / "nix-os.yaml").read_text())
    output = []
    for route in contract["routes"]:
        profile = next(p["hermes_profile"] for p in manifest["profiles"] if p["id"] == route["lead"] and p["kind"] == "lead")
        events = ",".join(contract["events"])
        output.append(["hermes", "webhook", "subscribe", route["route_name"], "--route-profile", profile,
                       "--script", route["script"], "--events", events])
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="print argv only (default and only mode)")
    parser.parse_args()
    print(json.dumps(render(), indent=2))
