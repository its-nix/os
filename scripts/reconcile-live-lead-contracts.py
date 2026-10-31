#!/usr/bin/env python3
"""Preview narrowly scoped reconciliation of user-owned Lead SOUL.md files."""
from __future__ import annotations
import argparse
import difflib
import os
from pathlib import Path
import stat
import sys
import tempfile

PROFILES = ("pulse", "algua", "meabeclick")
SECTION = """## Direct reporting and native continuity (managed)

Report project status, outcomes, and required decisions directly to Lior in this profile's project channel. LNX is an independent portfolio/systemic observer, not a routine relay or approval intermediary. This section supersedes earlier instructions to hand off ordinary status or human-only decisions through LNX. For human-only decisions, proactively ask Lior directly with the specific choice, reason, attempts, impact, and evidence.

Own one durable Kanban parent per accepted spec-level objective; create native child tasks, retain source and acceptance checks, and keep the parent open until independently verified. Treat events as prompts, reread native state, and reject stale/duplicate signals. Supported continuity CLI: `hermes kanban notify-subscribe --platform ... --chat-id ... --notifier-profile ... --delivery-mode notify+wake`; for thread/forum sources include `--parent-chat-id ...` and/or `--guild-id ...` as required by the source. Resolve all values from configuration; never hardcode channel IDs. This describes CLI support, not an installed subscription or delivered wake. Preserve available exact-head SHA, CI run/check IDs and conclusions, review ID/state, and acceptance evidence; otherwise mark unknown/partial. Do not create parallel task/decision systems.
"""
# Exact whole-paragraph anchors; a mismatch is intentionally a hard stop.
REPLACEMENTS = {
    "pulse": [("Keep LNX informed through concise evidence-backed handoffs rather than raw transcript dumps.", "Keep Lior informed directly with concise evidence-backed reports; LNX retains portfolio/systemic oversight only."),
              ("7. Escalate genuine product/commercial judgment to Lior via LNX.", "7. Escalate genuine product/commercial judgment directly to Lior.")],
    "algua": [("LNX is the portfolio orchestrator; Lior is the human decision-maker and the only authority for protected live-trading ceremonies.", "LNX is the portfolio/systemic observer; report directly to Lior, who is the human decision-maker and the only authority for protected live-trading ceremonies.")],
    "meabeclick": [("LNX is the portfolio orchestrator; Lior is the human decision-maker.", "LNX is the portfolio/systemic observer; report directly to Lior, the human decision-maker.")],
}

def transform(name: str, text: str) -> str:
    if "## Direct reporting and native continuity (managed)" in text:
        if text.count("## Direct reporting and native continuity (managed)") != 1 or SECTION not in text:
            raise ValueError(f"{name}: unexpected or modified managed section")
        return text
    result = text
    for old, new in REPLACEMENTS[name]:
        if result.count(old) != 1:
            raise ValueError(f"{name}: expected unique anchor missing or changed: {old!r}")
        result = result.replace(old, new, 1)
    # Insert after existing mission paragraph before the next section, preserving all other bytes.
    marker = "\n## "
    pos = result.find(marker)
    if pos < 0:
        raise ValueError(f"{name}: expected section boundary missing")
    return result[:pos] + "\n\n" + SECTION.rstrip() + result[pos:]

def check_file(path: Path):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ValueError(f"unsafe non-regular or symlink target: {path}")
    if st.st_nlink != 1:
        raise ValueError(f"refusing hard-linked target: {path}")
    if st.st_mode & 0o022:
        raise ValueError(f"refusing group/world-writable target: {path}")
    return st

def reconcile(paths: dict[str, Path], apply: bool = False, backup_dir: Path | None = None) -> list[str]:
    plans = []
    for name in PROFILES:
        path = paths[name]
        st = check_file(path)
        before = path.read_text(encoding="utf-8")
        after = transform(name, before)
        plans.append((name, path, st, before, after))
    diffs = []
    for name, path, _, before, after in plans:
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=str(path), tofile=str(path)+" (proposed)"))
        diffs.append(diff or f"{path}: already reconciled\n")
    if not apply or all(a == b for _, _, _, a, b in plans):
        return diffs
    if backup_dir is None:
        backup_dir = Path.home() / ".hermes" / "backups" / "lead-soul-reconcile"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if backup_dir.stat().st_mode & 0o077:
        raise ValueError(f"backup directory must be owner-only: {backup_dir}")
    saved = []
    try:
        # Revalidate snapshots immediately before backup and replacement.
        for name, path, st, before, after in plans:
            current_st = check_file(path)
            if current_st.st_ino != st.st_ino or current_st.st_mtime_ns != st.st_mtime_ns or path.read_text(encoding="utf-8") != before:
                raise ValueError(f"target changed since preview: {path}")
            backup = backup_dir / f"{name}-{os.getpid()}-{st.st_mtime_ns}.SOUL.md"
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out: out.write(before)
            saved.append((path, st, before, after, backup))
        for path, st, before, after, backup in saved:
            fd, temp = tempfile.mkstemp(prefix=".SOUL.md.", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.write(after); out.flush(); os.fsync(out.fileno())
                os.chmod(temp, stat.S_IMODE(st.st_mode))
                now = check_file(path)
                if now.st_ino != st.st_ino or path.read_text(encoding="utf-8") != before:
                    raise ValueError(f"target changed before replacement: {path}")
                os.replace(temp, path)
            finally:
                if os.path.exists(temp): os.unlink(temp)
            if path.read_text(encoding="utf-8") != after:
                raise ValueError(f"exact readback failed: {path}")
    except Exception:
        # Restore only paths this invocation replaced, from exact snapshots via atomic replacement.
        for path, st, before, after, backup in reversed(saved):
            try:
                if path.read_text(encoding="utf-8") == after:
                    fd, temp = tempfile.mkstemp(prefix=".SOUL.rollback.", dir=path.parent)
                    with os.fdopen(fd, "w", encoding="utf-8") as out: out.write(before)
                    os.chmod(temp, stat.S_IMODE(st.st_mode)); os.replace(temp, path)
            except OSError: pass
        raise
    return diffs

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="write after review; default is preview only")
    p.add_argument("--hermes-home", type=Path, default=Path(os.environ.get("HERMES_HOME", Path.home()/".hermes")))
    args = p.parse_args()
    paths = {n: args.hermes_home/"profiles"/n/"SOUL.md" for n in PROFILES}
    try:
        for diff in reconcile(paths, args.apply): print(diff, end="")
        print("APPLIED and read back exactly." if args.apply else "PREVIEW ONLY; no files changed. Re-run with --apply only after explicit authorization.")
        return 0
    except (OSError, UnicodeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr); return 1
if __name__ == "__main__": raise SystemExit(main())
