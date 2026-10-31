#!/usr/bin/env python3
"""Preview/apply the governed Discord-to-lead Hermes profile routes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "nix-os.yaml"
ID = re.compile(r"^[0-9]{15,22}$")
EXPECTED_FIELDS = {"name", "platform", "guild_id", "chat_id", "profile"}


def hermes(*args: str) -> str:
    command = [os.environ.get("HERMES_BIN", "hermes"), "-p", "default", *args]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Hermes command failed ({' '.join(command)}): {detail}")
    return result.stdout.strip()


def load_routes() -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    routes = manifest.get("discord_profile_routes")
    if not isinstance(routes, list) or len(routes) != 3:
        raise ValueError("manifest must declare exactly three Discord lead routes")
    profiles = {item["id"]: item for item in manifest.get("profiles", [])}
    names: set[str] = set()
    channels: set[str] = set()
    guilds: set[str] = set()
    checked: list[dict[str, str]] = []
    for index, route in enumerate(routes):
        if not isinstance(route, dict) or set(route) != EXPECTED_FIELDS:
            raise ValueError(f"route {index} has unexpected fields")
        if route["platform"] != "discord":
            raise ValueError(f"route {index} is not a Discord route")
        for key in ("guild_id", "chat_id"):
            if not isinstance(route[key], str) or not ID.fullmatch(route[key]):
                raise ValueError(f"route {index} {key} must be a quoted Discord snowflake")
        profile = profiles.get(route["profile"])
        if not profile or profile.get("kind") != "lead":
            raise ValueError(f"route {index} must target a declared project lead")
        if not route["name"] or route["name"] in names:
            raise ValueError(f"route {index} has a missing or duplicate name")
        if route["chat_id"] in channels:
            raise ValueError(f"route {index} duplicates a Discord channel")
        names.add(route["name"])
        channels.add(route["chat_id"])
        guilds.add(route["guild_id"])
        checked.append({key: route[key] for key in ("name", "platform", "guild_id", "chat_id", "profile")})
    if len(guilds) != 1:
        raise ValueError("all governed project channels must belong to one Discord server")
    return checked


def get_json(key: str) -> Any:
    return json.loads(hermes("config", "get", key, "--json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write routes using Hermes config CLI")
    args = parser.parse_args()

    try:
        desired = load_routes()
        if get_json("gateway.multiplex_profiles") is not True:
            raise RuntimeError("gateway.multiplex_profiles is not true; refusing to configure profile routes")
        current = get_json("gateway.profile_routes")
        if not isinstance(current, list):
            raise RuntimeError("gateway.profile_routes is not a list; refusing to replace it")
        if current == desired:
            print("Discord profile routes already match the governed manifest.")
            return 0
        if current:
            raise RuntimeError("unexpected existing profile routes; reconcile them deliberately before applying")

        print("Discord channel routing:")
        for route in desired:
            print(f"  {route['chat_id']} -> {route['profile']} ({route['name']})")
        if not args.apply:
            print("PREVIEW only. Run with --apply to write routes; then restart the Hermes gateway.")
            return 0

        payload = json.dumps(desired, separators=(",", ":"))
        hermes("config", "set", "gateway.profile_routes", payload)
        actual = get_json("gateway.profile_routes")
        if actual != desired:
            raise RuntimeError("route readback does not match the governed manifest")
        print("Applied and verified route configuration. Restart the Hermes gateway to activate it.")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
