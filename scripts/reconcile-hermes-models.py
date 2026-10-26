#!/usr/bin/env python3
"""Preview or reconcile the approved per-profile Hermes model routing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "nix-os.yaml"
APPROVED_PROVIDER = "openai-codex"
APPROVED_MODELS = {
    "default": "gpt-6-luna",
    "pulse": "gpt-6-luna",
    "meabeclick": "gpt-6-luna",
    "meabeclickux": "gpt-6-luna",
    "meabeclickgrowth": "gpt-6-luna",
    "algua": "gpt-6.1-sol",
    "meabeclickeng": "gpt-6.1-sol",
}
APPROVED_CRON_JOBS = [
    {
        "hermes_profile": "default",
        "name": "Weekly Hermes aggregate usage review",
        "provider": "openai-codex",
        "model": "gpt-6-luna",
    }
]
CONFIG_KEYS = (("model.provider", APPROVED_PROVIDER), ("model.default", None))


class ReconciliationError(RuntimeError):
    """A safe, operator-facing reconciliation failure."""


def fail(message: str, status: int = 1) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(status)


def load_routing() -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"cannot read declarative manifest: {exc}") from exc

    profiles = manifest.get("profiles")
    routing = manifest.get("hermes_model_routing")
    if not isinstance(profiles, list) or not isinstance(routing, dict):
        raise ReconciliationError("manifest profiles/model routing are missing or malformed")

    expected_profile_ids = {
        "lnx": "default",
        "pulse": "pulse",
        "algua": "algua",
        "meabeclick": "meabeclick",
        "meabeclickux": "meabeclickux",
        "meabeclickeng": "meabeclickeng",
        "meabeclickgrowth": "meabeclickgrowth",
    }
    if len(profiles) != len(expected_profile_ids) or any(not isinstance(item, dict) for item in profiles):
        raise ReconciliationError("manifest must contain exactly seven profile objects")
    identifiers = [item.get("id") for item in profiles]
    if any(not isinstance(identifier, str) for identifier in identifiers):
        raise ReconciliationError("manifest profile IDs must be strings")
    if len(set(identifiers)) != len(identifiers):
        raise ReconciliationError("manifest profile IDs must be unique")
    try:
        manifest_profiles = {
            item["id"]: item["hermes_profile"]
            for item in profiles
        }
    except (KeyError, TypeError) as exc:
        raise ReconciliationError("manifest profile mapping is malformed") from exc
    if manifest_profiles != expected_profile_ids:
        raise ReconciliationError(
            "manifest Hermes profile mapping does not match the approved seven-profile topology"
        )

    provider = routing.get("provider")
    models = routing.get("models")
    cron_jobs = routing.get("managed_cron_jobs")
    if provider != APPROVED_PROVIDER or models != APPROVED_MODELS:
        raise ReconciliationError(
            "manifest model routing does not match the approved provider/profile mapping"
        )
    if cron_jobs != APPROVED_CRON_JOBS:
        raise ReconciliationError(
            "manifest managed cron routing does not match the approved stable-name mapping"
        )
    return list(APPROVED_MODELS.items()), APPROVED_CRON_JOBS


def hermes_executable() -> str:
    requested = os.environ.get("HERMES_BIN", "hermes")
    if "/" in requested:
        path = Path(requested)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ReconciliationError(f"Hermes executable not found: {requested}")
        return str(path)
    resolved = shutil.which(requested)
    if resolved is None:
        raise ReconciliationError(f"Hermes executable not found: {requested}")
    return resolved


def validate_profile_directories(profiles: list[tuple[str, str]]) -> None:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    paths = {
        profile: home if profile == "default" else home / "profiles" / profile
        for profile, _model in profiles
    }
    failures = []
    for profile, path in paths.items():
        if path.is_symlink():
            failures.append(f"{profile} is a symlink ({path})")
        elif not path.is_dir():
            failures.append(f"{profile} is absent ({path})")
    if failures:
        raise ReconciliationError(
            "required Hermes profiles are not safe and present; refusing to mutate: "
            + "; ".join(failures)
        )


def run_hermes(binary: str, profile: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = [binary, "-p", profile, *args]
    try:
        return subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise ReconciliationError(
            f"could not execute Hermes for profile {profile}: {exc}"
        ) from exc


def read_json_string(binary: str, profile: str, key: str) -> str:
    result = run_hermes(binary, profile, "config", "get", key, "--json")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ReconciliationError(
            f"config readback failed for profile {profile}, key {key}: {detail}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReconciliationError(
            f"config readback for profile {profile}, key {key} was not valid JSON: {exc}"
        ) from exc
    if type(value) is not str:
        raise ReconciliationError(
            f"config readback for profile {profile}, key {key} had unexpected type "
            f"{type(value).__name__}; expected string"
        )
    return value


def set_value(binary: str, profile: str, key: str, value: str) -> None:
    result = run_hermes(binary, profile, "config", "set", key, value)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ReconciliationError(
            f"config setter failed for profile {profile}, key {key}: {detail}"
        )


def profile_home(base: Path, profile: str) -> Path:
    return base if profile == "default" else base / "profiles" / profile


def read_cron_jobs(base: Path, profile: str) -> list[dict[str, Any]]:
    path = profile_home(base, profile) / "cron" / "jobs.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"cannot read governed cron state for {profile}: {exc}") from exc
    if isinstance(value, dict):
        value = value.get("jobs")
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ReconciliationError(f"governed cron state for {profile} is not a job list")
    return value


def locate_managed_cron_jobs(
    binary: str, base: Path, desired_jobs: list[dict[str, str]]
) -> list[tuple[dict[str, str], str]]:
    for profile in sorted({item["hermes_profile"] for item in desired_jobs}):
        result = run_hermes(binary, profile, "cron", "list", "--all")
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise ReconciliationError(f"cron preflight failed for profile {profile}: {detail}")
    located: list[tuple[dict[str, str], str]] = []
    for desired in desired_jobs:
        jobs = read_cron_jobs(base, desired["hermes_profile"])
        matches = [job for job in jobs if job.get("name") == desired["name"]]
        if len(matches) != 1:
            raise ReconciliationError(
                f"managed cron job {desired['name']!r} must exist exactly once in "
                f"profile {desired['hermes_profile']}; found {len(matches)}"
            )
        identifier = matches[0].get("id") or matches[0].get("job_id")
        if not isinstance(identifier, str) or not identifier:
            raise ReconciliationError(f"managed cron job {desired['name']!r} has no stable ID")
        located.append((desired, identifier))
    return located


def verify_managed_cron_job(base: Path, desired: dict[str, str], identifier: str) -> None:
    jobs = read_cron_jobs(base, desired["hermes_profile"])
    matches = [
        job
        for job in jobs
        if (job.get("id") or job.get("job_id")) == identifier
        and job.get("name") == desired["name"]
    ]
    if len(matches) != 1:
        raise ReconciliationError(f"managed cron job readback disappeared: {desired['name']!r}")
    actual = matches[0]
    if type(actual.get("provider")) is not str or type(actual.get("model")) is not str:
        raise ReconciliationError(
            f"managed cron job readback had unexpected provider/model types: {desired['name']!r}"
        )
    if actual["provider"] != desired["provider"] or actual["model"] != desired["model"]:
        raise ReconciliationError(
            f"managed cron job readback mismatch for {desired['name']!r}: "
            f"expected {desired['provider']}/{desired['model']}, "
            f"got {actual['provider']}/{actual['model']}"
        )


def preview(
    profiles: list[tuple[str, str]], cron_jobs: list[dict[str, str]]
) -> None:
    print("Hermes model routing: PREVIEW (no Hermes commands or files will be changed)")
    for profile, model in profiles:
        print(f"  {profile}: provider={APPROVED_PROVIDER}, model={model}")
        print(
            f"    hermes -p {profile} config set model.provider {APPROVED_PROVIDER}"
        )
        print(f"    hermes -p {profile} config set model.default {model}")
    for job in cron_jobs:
        print(
            f"  managed cron by stable name: {job['hermes_profile']}:{job['name']} "
            f"-> {job['provider']}/{job['model']}"
        )
    print("PREVIEW only; rerun with --apply after confirming every profile exists.")


def apply(
    profiles: list[tuple[str, str]], cron_jobs: list[dict[str, str]]
) -> None:
    binary = hermes_executable()
    validate_profile_directories(profiles)
    base = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))

    # Preflight every typed read before the first mutation. Any inaccessible,
    # malformed, or surprising profile config fails the entire run closed.
    for profile, _model in profiles:
        for key, _expected in CONFIG_KEYS:
            read_json_string(binary, profile, key)
    located_jobs = locate_managed_cron_jobs(binary, base, cron_jobs)

    changed = False
    try:
        for profile, model in profiles:
            expected_values = (
                ("model.provider", APPROVED_PROVIDER),
                ("model.default", model),
            )
            for key, expected in expected_values:
                set_value(binary, profile, key, expected)
                changed = True
                actual = read_json_string(binary, profile, key)
                if actual != expected:
                    raise ReconciliationError(
                        f"config readback mismatch for profile {profile}, key {key}: "
                        f"expected {expected!r}, got {actual!r}"
                    )
            print(f"[verified] {profile}: {APPROVED_PROVIDER}/{model}")
        for desired, identifier in located_jobs:
            result = run_hermes(
                binary,
                desired["hermes_profile"],
                "cron", "edit", identifier,
                "--provider", desired["provider"],
                "--model", desired["model"],
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
                raise ReconciliationError(
                    f"managed cron model setter failed for {desired['name']!r}: {detail}"
                )
            changed = True
            verify_managed_cron_job(base, desired, identifier)
            print(
                f"[verified] cron {desired['name']}: "
                f"{desired['provider']}/{desired['model']}"
            )
    except ReconciliationError as exc:
        if changed:
            raise ReconciliationError(
                f"{exc}; routing may be partially applied; correct the error and rerun --apply"
            ) from exc
        raise

    print("Verified provider and model routing for all seven Hermes profiles.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or apply the approved Hermes profile model mapping."
    )
    parser.add_argument(
        "--apply", action="store_true", help="set and verify every declared provider/model"
    )
    args = parser.parse_args()
    try:
        profiles, cron_jobs = load_routing()
        if args.apply:
            apply(profiles, cron_jobs)
        else:
            preview(profiles, cron_jobs)
    except ReconciliationError as exc:
        fail(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
