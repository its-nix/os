#!/usr/bin/env python3
"""Preview-first, additive Hermes cron reconciler for the portfolio monitor."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
EXPECTED_JOBS = {
    "nix-portfolio-monitor-12h": {
        "name": "nix-portfolio-monitor-12h", "kind": "monitor", "profile": "lnx",
        "hermes_profile": "default", "schedule": "0 */12 * * *",
        "prompt_file": "portfolio-monitor-prompt.md", "script_file": "monitor_snapshot.py",
        "execution_mode": "monitor", "workdir": "${HOME}/os",
        "delivery": "discord:1563181903253737542", "continuity": True,
    },
    "nix-portfolio-executive-weekly": {
        "name": "nix-portfolio-executive-weekly", "kind": "executive_summary", "profile": "lnx",
        "hermes_profile": "default", "schedule": "0 10 * * 1",
        "prompt_file": "executive-summary-prompt.md", "script_file": "executive_snapshot.py",
        "execution_mode": "script", "workdir": "${HOME}/os",
        "delivery": "discord:1563181903253737542", "continuity": True,
    },
}


class Refusal(RuntimeError):
    pass


def _within_root(root: Path, rel: str) -> Path:
    candidate = root / rel
    resolved = candidate.resolve(strict=True)
    if root.resolve(strict=True) not in resolved.parents or not resolved.is_file():
        raise Refusal("managed source path escapes monitoring directory or is not a file")
    return resolved


def load_config(path: Path = ROOT / "jobs.json") -> list[dict]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise Refusal("unsupported or malformed jobs manifest")
    jobs = config.get("jobs")
    if config.get("schema_version") != 1 or set(config) != {"schema_version", "timezone", "jobs"} or config.get("timezone") != "Asia/Jerusalem" or not isinstance(jobs, list):
        raise Refusal("unsupported or malformed jobs manifest")
    names = [j.get("name") for j in jobs if isinstance(j, dict)]
    if len(names) != len(jobs) or len(set(names)) != len(names) or set(names) != set(EXPECTED_JOBS):
        raise Refusal("invalid or duplicate desired job names")
    for job in jobs:
        if job != EXPECTED_JOBS.get(job.get("name")):
            raise Refusal("job definition differs from the reviewed safe shape")
        required = ("name", "hermes_profile", "schedule", "prompt_file", "script_file", "execution_mode", "workdir", "delivery")
        if not all(isinstance(job.get(k), str) and job[k] for k in required):
            raise Refusal("missing required string fields")
        if not NAME.fullmatch(job["name"]):
            raise Refusal("invalid job name")
        if job["execution_mode"] not in {"script", "monitor"}:
            raise Refusal("execution_mode must be script or monitor")
        if job.get("continuity") is not True:
            raise Refusal("monitor jobs must use continuity for cross-run deduplication")
        job["prompt"] = _within_root(ROOT, job["prompt_file"]).read_text(encoding="utf-8")
        job["script_source"] = _within_root(ROOT, job["script_file"])
    return jobs


def parse_list(text: str) -> dict[str, dict]:
    """Parse only stable fields printed by the installed `hermes cron list` CLI."""
    found: dict[str, dict] = {}
    current: dict | None = None
    for line in text.splitlines():
        job_start = re.match(r"\s*([0-9a-f]{12,})\s+\[([^]]+)\]", line, re.I)
        if job_start:
            current = {"id": job_start.group(1), "state": job_start.group(2).lower()}
            continue
        if current is None:
            continue
        for label, key in (("Name", "name"), ("Schedule", "schedule"), ("Deliver", "deliver"), ("Script", "script"), ("Monitor", "monitor_script")):
            match = re.match(rf"\s*{label}:\s*(.*?)\s*$", line)
            if match:
                current[key] = match.group(1).split(" (", 1)[0]
                if key == "name":
                    if current["name"] in found:
                        current["duplicate"] = True
                        found[current["name"]]["duplicate"] = True
                    found[current["name"]] = current
                break
    return found


def preview(jobs: list[dict], listings: dict[str, dict]) -> list[str]:
    plan = []
    for job in jobs:
        actual = listings.get(job["hermes_profile"], {}).get(job["name"])
        if actual:
            if actual.get("duplicate"):
                raise Refusal(f"duplicate existing job name: {job['name']}")
            if "paused" in actual.get("state", "") or "disabled" in actual.get("state", ""):
                raise Refusal(f"same-name job is paused/disabled; refusing to resume: {job['name']}")
            if actual.get("schedule") != job["schedule"] or actual.get("deliver") != job["delivery"]:
                raise Refusal(f"same-name job drift; refusing to edit: {job['name']}")
            wanted_key = "monitor_script" if job["execution_mode"] == "monitor" else "script"
            other_key = "script" if wanted_key == "monitor_script" else "monitor_script"
            actual_script = actual.get(wanted_key)
            expected_script = f"nix-monitoring/{job['script_file']}"
            if actual_script != expected_script or actual.get(other_key):
                raise Refusal(f"same-name script/mode drift; refusing to edit: {job['name']}")
            plan.append(f"EXISTS (not modified): {job['name']} [{job['hermes_profile']}]")
        else:
            plan.append(f"CREATE (preview only): {job['name']} [{job['hermes_profile']}] @ {job['schedule']} -> {job['delivery']}")
    return plan


def idempotency_key(event_key: str, action_identity: str) -> str:
    import hashlib
    if not event_key or not action_identity:
        raise ValueError("stable event key and action identity are required")
    return hashlib.sha256((event_key + "\0" + action_identity).encode()).hexdigest()


def task_exists(tasks: list[dict], key: str) -> bool:
    marker = f"monitor_idempotency_key={key}"
    return any(isinstance(t, dict) and (t.get("idempotency_key") == key or marker in str(t.get("body", ""))) for t in tasks)


def lead_for_project(manifest: dict, project_id: str) -> str | None:
    project = next((p for p in manifest.get("projects", []) if p.get("id") == project_id), None)
    if not project or project.get("paused") is True or project.get("archived") is True:
        return None
    wanted = project.get("lead") or manifest.get("routing", {}).get(project_id)
    return next((p["id"] for p in manifest.get("profiles", [])
                 if p.get("id") == wanted and p.get("kind") == "lead"), None)


def retry_allowed(attempts: int, policy: dict) -> bool:
    cap = policy.get("max_fix_attempts", 0)
    return isinstance(attempts, int) and not isinstance(attempts, bool) and isinstance(cap, int) and not isinstance(cap, bool) and 0 <= attempts < cap


def verify_live_controls(profile: str, hermes: str, runner: Callable) -> None:
    """Require the user's manual/deny controls and gateway-dispatch policy before writes."""
    env = os.environ.copy()
    env["HERMES_HOME"] = str(_hermes_home(profile))
    for key in ("approvals", "kanban.dispatch_in_gateway"):
        result = runner([hermes, "config", "get", key, "--json"], capture_output=True,
                        text=True, check=False, timeout=20, env=env)
        if result.returncode:
            raise Refusal(f"could not verify required safety control: {key}")
        try:
            value = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise Refusal(f"required safety control is unreadable: {key}") from exc
        if key == "approvals":
            if not isinstance(value, dict) or value.get("mode") != "manual" or value.get("cron_mode") != "deny":
                raise Refusal("required manual approval and cron-deny controls are not active")
        elif value is not True:
            raise Refusal("required gateway dispatch control is not active")


def _hermes_home(profile: str) -> Path:
    base = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return base if profile == "default" else base / "profiles" / profile


def list_profile(profile: str, hermes: str = "hermes", runner: Callable = subprocess.run) -> dict[str, dict]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(_hermes_home(profile))
    result = runner([hermes, "cron", "list", "--all"], capture_output=True, text=True,
                    check=False, timeout=20, env=env)
    if result.returncode:
        raise Refusal(f"could not read cron list for profile {profile}")
    return parse_list(result.stdout)


def _ensure_owned_directory(path: Path) -> Path:
    """Create/validate a non-symlink directory beneath the current user's home."""
    path = Path(os.path.abspath(path))
    home = Path(os.path.abspath(Path.home()))
    if not path.is_relative_to(home):
        raise Refusal("runtime scripts directory must be beneath the current user's home")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                pass
            st = os.lstat(current)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise Refusal("runtime scripts path contains a symlink or non-directory")
        if current == home or home in current.parents:
            if st.st_uid != os.getuid():
                raise Refusal("runtime scripts path is not owned by the current user")
    return path


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise Refusal("short write while installing managed runtime file")
        view = view[written:]


def _validate_managed_file(dir_fd: int, name: str, expected: bytes, mode: int) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise Refusal(f"managed runtime file is unsafe: {name}") from exc
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) != mode or st.st_size != len(expected)
                or st.st_size > 1_000_000):
            raise Refusal(f"managed runtime file has unsafe type, owner, mode, or size: {name}")
        chunks = []
        remaining = st.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise Refusal(f"managed runtime file changed while reading: {name}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise Refusal(f"managed runtime file grew while reading: {name}")
        return b"".join(chunks) == expected
    finally:
        os.close(fd)


def _install_managed_file(dir_fd: int, name: str, data: bytes, mode: int) -> None:
    if len(data) > 1_000_000:
        raise Refusal("managed runtime source exceeds the configured size bound")
    if _validate_managed_file(dir_fd, name, data, mode):
        return
    temp_name = f".monitor-install-{secrets.token_hex(12)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, mode)
        _write_all(fd, data)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.link(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    except FileExistsError as exc:
        if not _validate_managed_file(dir_fd, name, data, mode):
            raise Refusal(f"managed runtime script drift; refusing to overwrite: {name}") from exc
    finally:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
    os.fsync(dir_fd)
    if not _validate_managed_file(dir_fd, name, data, mode):
        raise Refusal(f"managed runtime file failed readback: {name}")


def install_runtime_scripts(jobs: list[dict], scripts_root: Path) -> dict[str, Path]:
    dest = _ensure_owned_directory(scripts_root / "nix-monitoring")
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    dir_fd = os.open(dest, dir_flags)
    try:
        dir_stat = os.fstat(dir_fd)
        if dir_stat.st_uid != os.getuid() or stat.S_IMODE(dir_stat.st_mode) & 0o077:
            raise Refusal("runtime scripts directory must be private and user-owned")
        installed = {}
        for job in jobs:
            src = job["script_source"]
            _install_managed_file(dir_fd, src.name, src.read_bytes(), 0o700)
            installed[job["name"]] = dest / src.name
        # Snapshot and recovery helpers import the collector/read policy beside themselves.
        for name in ("collector.py", "policy.json", "recovery.py"):
            src = _within_root(ROOT, name)
            _install_managed_file(dir_fd, name, src.read_bytes(), 0o600)
        return installed
    finally:
        os.close(dir_fd)


def apply_missing(jobs: list[dict], listings: dict[str, dict], *, hermes: str = "hermes",
                  scripts_root: Path | None = None, runner: Callable = subprocess.run) -> list[str]:
    """Create only absent jobs after a full read-only preflight; never edit/resume."""
    preview(jobs, listings)
    for profile in sorted({job["hermes_profile"] for job in jobs}):
        verify_live_controls(profile, hermes, runner)
    if scripts_root is None:
        scripts_root = Path.home() / ".hermes" / "scripts"
    installed = install_runtime_scripts(jobs, scripts_root)
    outcomes = []
    for job in jobs:
        if listings.get(job["hermes_profile"], {}).get(job["name"]):
            outcomes.append(f"EXISTS (unchanged): {job['name']}")
            continue
        workdir = job["workdir"].replace("${HOME}", str(Path.home()))
        script_flag = "--monitor-script" if job["execution_mode"] == "monitor" else "--script"
        script_path = Path(installed[job["name"]]).relative_to(Path(os.path.abspath(scripts_root)))
        argv = [hermes, "cron", "create", "--name", job["name"], "--deliver", job["delivery"],
                script_flag, script_path.as_posix(), "--workdir", workdir]
        if job["continuity"]:
            argv.append("--continuity")
        argv.extend([job["schedule"], job["prompt"]])
        env = os.environ.copy()
        env["HERMES_HOME"] = str(_hermes_home(job["hermes_profile"]))
        result = runner(argv, capture_output=True, text=True, check=False, timeout=60, env=env)
        if result.returncode:
            raise Refusal(f"creation failed for {job['name']}; partial application may exist; read back before retry")
        outcomes.append(f"CREATED: {job['name']}")
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "jobs.json")
    parser.add_argument("--hermes", default="hermes")
    parser.add_argument("--apply", action="store_true", help="create missing named jobs only; never edit or resume")
    args = parser.parse_args(argv)
    try:
        jobs = load_config(args.config)
        listings = {profile: list_profile(profile, args.hermes) for profile in sorted({j["hermes_profile"] for j in jobs})}
        if args.apply:
            outcomes = apply_missing(jobs, listings, hermes=args.hermes)
            print("\n".join(outcomes))
        else:
            print("PREVIEW ONLY — no cron mutations will be made")
            print("\n".join(preview(jobs, listings)))
        return 0
    except (OSError, ValueError, Refusal) as exc:
        parser.exit(2, f"REFUSED: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
