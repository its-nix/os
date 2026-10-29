#!/usr/bin/env python3
"""Gateway-independent health watchdog; only sends deduplicated critical alerts."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

SENDER = "lclaw.agent@gmail.com"
RECIPIENT = "lior3226@gmail.com"
SCHEDULER_MAX_AGE_SECONDS = 120
MONITOR_MAX_AGE_SECONDS = 13 * 3600  # 12h schedule plus one hour of bounded lateness
COMMAND_TIMEOUT_SECONDS = 8


def run_bounded(args: list[str], runner: Callable = subprocess.run):
    try:
        return runner(args, capture_output=True, text=True,
                      timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def safe_regular_file_age(path: Path | None, now: float, limit: int) -> bool:
    """Read freshness only from a non-symlink regular file and ancestry."""
    if path is None:
        return False
    try:
        path = Path(os.path.abspath(path))
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode):
                return False
        st = os.lstat(path)
        return stat.S_ISREG(st.st_mode) and 0 <= now - st.st_mtime <= limit
    except (OSError, ValueError):
        return False


def scheduler_heartbeat_age(text: str) -> int | None:
    lowered = text.lower()
    if "scheduler host:" not in lowered or "gateway is running" not in lowered:
        return None
    match = re.search(
        r"ticker heartbeat:\s*(\d+)\s*(s|sec|seconds|m|minutes|h|hours)\s+ago",
        lowered,
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    factor = 1 if unit in {"s", "sec", "seconds"} else 60 if unit in {"m", "minutes"} else 3600
    return amount * factor


def monitor_job_age(text: str, now: float) -> tuple[int, bool] | None:
    """Return (age_seconds, succeeded) for the named 12-hour monitor job."""
    clean = re.sub(r"\x1b\[[0-9;]*m", "", text)
    current: dict[str, str] | None = None
    target: dict[str, str] | None = None
    for line in clean.splitlines() + ["  000000000000 [end]"]:
        if re.match(r"\s*[0-9a-f]{12,}\s+\[[^]]+\]", line, re.I):
            if current and current.get("name") == "nix-portfolio-monitor-12h":
                target = current
                break
            current = {}
            continue
        if current is None:
            continue
        name = re.match(r"\s*Name:\s*(.*?)\s*$", line)
        last = re.match(r"\s*Last run:\s*(\S+)\s+(.*)$", line)
        if name:
            current["name"] = name.group(1)
        if last:
            current["last_at"] = last.group(1)
            current["status"] = last.group(2).strip().lower()
    if target is None and current and current.get("name") == "nix-portfolio-monitor-12h":
        target = current
    if not target or not target.get("last_at"):
        return None
    try:
        stamp = dt.datetime.fromisoformat(target["last_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        age = int(now - stamp.timestamp())
    except (ValueError, OverflowError, OSError):
        return None
    if age < 0:
        return None
    status = target.get("status", "")
    succeeded = status == "ok" or status.startswith("ok ")
    return age, succeeded


def inspect(gateway_unit: str, monitor_heartbeat: Path | None, now: float,
            runner: Callable = subprocess.run) -> list[str]:
    issues: list[str] = []
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}\.service", gateway_unit):
        issues.append("gateway service configuration invalid")
    else:
        service = run_bounded(["systemctl", "--user", "is-active", gateway_unit], runner)
        if service is None or service.returncode != 0 or service.stdout.strip() != "active":
            issues.append("gateway service is not active")

    cron = run_bounded(["hermes", "cron", "status"], runner)
    age = scheduler_heartbeat_age(cron.stdout) if cron is not None and cron.returncode == 0 else None
    if age is None:
        issues.append("scheduler heartbeat unavailable")
    elif age > SCHEDULER_MAX_AGE_SECONDS:
        issues.append("scheduler heartbeat stale")

    job_list = run_bounded(["hermes", "cron", "list", "--all"], runner)
    if job_list is None or job_list.returncode != 0:
        issues.append("monitor job status unavailable")
    else:
        monitor = monitor_job_age(job_list.stdout, now)
        if monitor is None:
            issues.append("monitor job has never completed or is unavailable")
        elif not monitor[1]:
            issues.append("monitor job failed or delivery failed")
        elif monitor[0] > MONITOR_MAX_AGE_SECONDS:
            issues.append("monitor job stale")

    if not safe_regular_file_age(monitor_heartbeat, now, MONITOR_MAX_AGE_SECONDS):
        issues.append("monitor-cycle heartbeat missing or stale")
    return issues


def _check_state_path(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    home = Path(os.path.abspath(Path.home()))
    if not absolute.is_relative_to(home):
        raise ValueError("watchdog state path must be beneath the current user's home")
    current = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
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
            raise ValueError("watchdog state path is unsafe")
        if (current == home or home in current.parents) and st.st_uid != os.getuid():
            raise ValueError("watchdog state path is not owned by the current user")
    parent_stat = os.lstat(absolute.parent)
    if (stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077):
        raise ValueError("watchdog state directory must be private and user-owned")
    try:
        st = os.lstat(absolute)
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o600):
        raise ValueError("watchdog state file is unsafe")


def _state_stat(parent_fd: int, name: str):
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o600):
        raise ValueError("watchdog state file is unsafe")
    return st


def _read_state_at(parent_fd: int, name: str) -> str:
    named = _state_stat(parent_fd, name)
    if named is None:
        return ""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError("watchdog state is unreadable") from exc
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                or opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) != 0o600):
            raise ValueError("watchdog state changed during read")
        chunks = bytearray()
        while len(chunks) <= 4096:
            block = os.read(fd, min(1024, 4097 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        if len(chunks) > 4096:
            raise ValueError("watchdog state exceeds the configured size limit")
        after = _state_stat(parent_fd, name)
        if after is None or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("watchdog state changed during read")
        try:
            value = json.loads(chunks.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("watchdog state is unreadable") from exc
        if not isinstance(value, dict) or not isinstance(value.get("fingerprint"), str):
            raise ValueError("watchdog state is malformed")
        return value["fingerprint"]
    finally:
        os.close(fd)


def _write_state_at(parent_fd: int, name: str, fingerprint: str) -> None:
    existing = _state_stat(parent_fd, name)
    temp_name = f".watchdog-{secrets.token_hex(12)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
    try:
        remaining = memoryview(json.dumps({"fingerprint": fingerprint}).encode("utf-8") + bytes((10,)))
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise ValueError("watchdog state write was incomplete")
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        current = _state_stat(parent_fd, name)
        if (existing is None) != (current is None) or (
            existing is not None and current is not None
            and (existing.st_dev, existing.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("watchdog state changed before replacement")
        os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _open_lock_file(path: Path) -> tuple[int, int]:
    """Open a private, single-link lock relative to its validated directory."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        parent_fd = os.open(path.parent, flags)
    except OSError as exc:
        raise ValueError("watchdog lock directory is unsafe") from exc
    try:
        parent = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise ValueError("watchdog lock directory must be private and user-owned")
        lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            lock_fd = os.open(path.name, lock_flags, 0o600, dir_fd=parent_fd)
        except OSError as exc:
            raise ValueError("watchdog lock file is unsafe") from exc
        try:
            opened = os.fstat(lock_fd)
            named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                    or opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) != 0o600
                    or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                raise ValueError("watchdog lock file must be a private single-link regular file")
            return parent_fd, lock_fd
        except Exception:
            os.close(lock_fd)
            raise
    except Exception:
        os.close(parent_fd)
        raise


def send_deduplicated(issues: list[str], state_path: Path,
                      runner: Callable = subprocess.run) -> bool:
    """Send one email per unchanged issue set; clear the fingerprint when healthy."""
    _check_state_path(state_path)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    parent_fd, lock_fd = _open_lock_file(lock_path)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        old = _read_state_at(parent_fd, state_path.name)
        if not issues:
            _write_state_at(parent_fd, state_path.name, "")
            return False
        if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9 :._-]{1,120}", item) for item in issues):
            raise ValueError("watchdog issue text is not allowlisted")
        fingerprint = hashlib.sha256("\n".join(sorted(set(issues))).encode()).hexdigest()
        if fingerprint == old:
            return False
        body = "Critical health checks failed:\n" + "\n".join("- " + item for item in sorted(set(issues))) + "\n"
        sent = run_bounded(
            ["gws", "gmail", "+send", "--to", RECIPIENT, "--from", SENDER,
             "--subject", "CRITICAL: monitoring watchdog", "--body", body],
            runner,
        )
        if sent is None or sent.returncode != 0:
            return False
        _write_state_at(parent_fd, state_path.name, fingerprint)
        return True
    finally:
        os.close(lock_fd)
        os.close(parent_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-unit", default="hermes-gateway.service")
    parser.add_argument("--monitor-heartbeat", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=Path.home() / ".local/state/hermes-monitoring/watchdog.json")
    args = parser.parse_args(argv)
    issues = inspect(args.gateway_unit, args.monitor_heartbeat, time.time())
    try:
        send_deduplicated(issues, args.state)
    except (OSError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
