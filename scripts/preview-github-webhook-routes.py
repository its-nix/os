#!/usr/bin/env python3
"""Preview native agent-mode route argv; never executes subscriptions."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def render(contract=None, manifest=None):
    contract = contract or json.loads((ROOT / "automation/monitoring/github-webhook-routes.json").read_text())
    manifest = manifest or json.loads((ROOT / "nix-os.yaml").read_text())
    output = []
    for route in contract["routes"]:
        profiles = [p for p in manifest["profiles"] if p.get("id") == route["lead"] and p.get("kind") == "lead"]
        bindings = [r for r in manifest["discord_profile_routes"] if r.get("profile") == route["lead"]]
        if len(profiles) != 1 or not isinstance(profiles[0].get("hermes_profile"), str) or not profiles[0]["hermes_profile"]:
            raise ValueError(f"expected exactly one valid Lead profile for {route['lead']}")
        if len(bindings) != 1:
            raise ValueError(f"expected exactly one Discord binding for {route['lead']}")
        binding = bindings[0]
        if (binding.get("platform") != "discord" or not isinstance(binding.get("chat_id"), str)
                or re.fullmatch(r"[1-9][0-9]{0,19}", binding["chat_id"]) is None
                or binding.get("name") != route["route_name"]):
            raise ValueError(f"invalid Discord binding for {route['lead']}")
        profile = profiles[0]["hermes_profile"]
        events = ",".join(contract["events"])
        output.append(["hermes", "webhook", "subscribe", route["route_name"], "--route-profile", profile,
                       "--script", route["script"], "--events", events,
                       "--deliver", "discord", "--deliver-chat-id", binding["chat_id"]])
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="print argv only (default and only mode)")
    parser.add_argument("--staging-plan", action="store_true", help="print source-to-HERMES_HOME/scripts staging map")
    args = parser.parse_args()
    contract = json.loads((ROOT / "automation/monitoring/github-webhook-routes.json").read_text())
    if args.staging_plan:
        print(json.dumps({"activation": "disabled", "writes_performed": False,
                          "target_root": "$HERMES_HOME/scripts",
                          "source_to_target": contract["staging"]["source_to_target"]}, indent=2))
    else:
        print(json.dumps(render(), indent=2))
