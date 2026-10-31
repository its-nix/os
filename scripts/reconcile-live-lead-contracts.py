#!/usr/bin/env python3
"""Preview narrowly scoped reconciliation of user-owned Lead SOUL.md files."""
from __future__ import annotations
import argparse
import difflib
import os
import hashlib
from pathlib import Path
import stat
import sys

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
    # Insert after the mission paragraph, before the following section.
    import re
    match = re.search(r"(?m)^## Mission\r?\n((?:[^#\r\n].*\r?\n?)+)", result)
    if not match:
        raise ValueError(f"{name}: mission paragraph boundary missing")
    end = match.end(1)
    result = result[:end] + "\n" + SECTION + result[end:]
    for old, new in REPLACEMENTS[name]:
        if result.count(old) or result.count(new) != 1:
            raise ValueError(f"{name}: expected post-replacement anchor missing or drifted")
    return result

def _open_dir(path: Path) -> int:
    """Walk absolute lexical components without following links; return pinned directory fd."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe non-absolute or traversing path: {path}")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    nxt = None
    try:
        parts = path.parts[1:]
        for index, component in enumerate(parts):
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            st = os.fstat(nxt)
            sticky_tmp = st.st_uid == 0 and bool(st.st_mode & stat.S_ISVTX) and not (st.st_mode & 0o002 == 0)
            if not stat.S_ISDIR(st.st_mode) or (st.st_mode & 0o022 and not sticky_tmp):
                raise ValueError(f"unsafe writable directory component: {path}")
            if st.st_uid not in (os.getuid(), 0) or (st.st_uid == 0 and st.st_mode & 0o022 and not sticky_tmp):
                raise ValueError(f"untrusted directory ownership: {path}")
            os.close(fd); fd = nxt; nxt = None
        return fd
    except OSError as exc:
        if nxt is not None: os.close(nxt)
        os.close(fd)
        raise ValueError(f"unsafe directory ancestry: {path}: {exc}") from exc
    except BaseException:
        if nxt is not None: os.close(nxt)
        os.close(fd); raise

def _open_backup_dir(path: Path) -> int:
    """Safely create missing owner-only backup components, never following links."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe backup directory: {path}")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            try:
                nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=fd)
                os.fsync(fd)
                nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            st = os.fstat(nxt)
            final = index == len(components) - 1
            if (not stat.S_ISDIR(st.st_mode)
                    or st.st_uid not in (0, os.getuid())
                    or st.st_mode & 0o022
                    or (final and (st.st_uid != os.getuid() or st.st_mode & 0o077))):
                os.close(nxt)
                raise ValueError(f"unsafe backup directory ancestry or final mode: {path}")
            os.close(fd); fd = nxt
        return fd
    except OSError as exc:
        os.close(fd)
        raise ValueError(f"unsafe backup directory ancestry: {path}: {exc}") from exc
    except BaseException:
        os.close(fd)
        raise

def _open_target(path: Path):
    if not path.is_absolute() or ".." in path.parts or path.name in ("", "."):
        raise ValueError(f"unsafe target path: {path}")
    parent = _open_dir(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise ValueError(f"unsafe target type, owner, link count, or mode: {path}")
        return parent, fd, st
    except OSError as exc:
        if fd is not None: os.close(fd)
        os.close(parent)
        raise ValueError(f"unsafe target path component: {path}: {exc}") from exc
    except BaseException:
        if fd is not None: os.close(fd)
        os.close(parent); raise

def _read_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), "r", encoding="utf-8") as f:
        return f.read()

def _write_temp(dirfd: int, prefix: str, data: bytes, mode: int) -> tuple[str, tuple[int, int]]:
    name = f"{prefix}{os.getpid()}-{os.urandom(12).hex()}"
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dirfd)
    identity = None
    try:
        st = os.fstat(fd)
        identity = (st.st_dev, st.st_ino)
        with os.fdopen(fd, "wb", closefd=True) as f:
            fd = -1
            f.write(data); f.flush(); os.fsync(f.fileno())
        return name, identity
    except BaseException as error:
        cleanup_errors = []
        if fd >= 0:
            try:
                os.close(fd)
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
        if identity is not None:
            try:
                _unlink_owned(dirfd, name, identity)
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
        if cleanup_errors:
            details = "; ".join(repr(exc) for exc in cleanup_errors)
            raise RuntimeError(f"temporary cleanup failed for {name}: {details}") from error
        if identity is None:
            raise RuntimeError(f"temporary file retained because its identity could not be verified: {name}") from error
        raise

def _unlink_owned(dirfd: int, name: str, identity: tuple[int, int]) -> None:
    try:
        st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        if (st.st_dev, st.st_ino) == identity:
            os.unlink(name, dir_fd=dirfd)
    except FileNotFoundError:
        pass

def reconcile(paths: dict[str, Path], apply: bool = False, backup_dir: Path | None = None) -> list[str]:
    plans = []
    for name in PROFILES:
        path = paths[name].absolute()
        parent, fd, st = _open_target(path)
        try:
            before = _read_fd(fd)
            parent_id = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
        finally:
            os.close(fd); os.close(parent)
        after = transform(name, before)
        plans.append((name, path, st, before, after, parent_id))
    diffs = ["".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), fromfile=str(p), tofile=str(p)+" (proposed)")) or f"{p}: already reconciled\\n" for _, p, _, a, b, _ in plans]
    if not apply or all(a == b for _, _, _, a, b, _ in plans):
        return diffs
    if backup_dir is None:
        backup_dir = Path.home() / ".hermes" / "lead-soul-reconcile-backups"
    backup_fd = _open_backup_dir(backup_dir.absolute())
    transaction = []
    committed = []
    try:
        # Stage all original backups first; each basename is exclusive and descriptor-bound.
        for name, path, expected, before, after, parent_id in plans:
            parent, fd, now = _open_target(path)
            try:
                if (os.fstat(parent).st_dev, os.fstat(parent).st_ino) != parent_id:
                    raise ValueError(f"target parent redirected since preview: {path}")
                if (now.st_dev, now.st_ino, now.st_mtime_ns) != (expected.st_dev, expected.st_ino, expected.st_mtime_ns) or _read_fd(fd) != before:
                    raise ValueError(f"target changed since preview: {path}")
            finally:
                os.close(fd); os.close(parent)
            bname = f"{name}-{os.getpid()}-{os.urandom(10).hex()}.SOUL.md"
            bfd = os.open(bname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=backup_fd)
            try:
                with os.fdopen(bfd, "wb") as out:
                    out.write(before.encode("utf-8")); out.flush(); os.fsync(out.fileno())
                bst = os.stat(bname, dir_fd=backup_fd, follow_symlinks=False)
            except BaseException:
                try: os.unlink(bname, dir_fd=backup_fd)
                except OSError: pass
                raise
            transaction.append({"name":name,"path":path,"st":expected,"before":before,"after":after,"parent_id":parent_id,"backup":bname,"backup_id":(bst.st_dev,bst.st_ino),"backup_size":bst.st_size,"backup_digest":hashlib.sha256(before.encode("utf-8")).digest()})
        os.fsync(backup_fd)
        # Stage one replacement at a time; pinned parent fd defeats path redirection.
        for item in transaction:
            parent, fd, now = _open_target(item["path"])
            staged = None
            try:
                if (os.fstat(parent).st_dev, os.fstat(parent).st_ino) != item["parent_id"]:
                    raise ValueError(f"target parent redirected before commit: {item['path']}")
                if (now.st_dev, now.st_ino, now.st_mtime_ns) != (item["st"].st_dev,item["st"].st_ino,item["st"].st_mtime_ns) or _read_fd(fd) != item["before"]:
                    raise ValueError(f"target changed before commit: {item['path']}")
                staged = _write_temp(parent, ".SOUL.stage.", item["after"].encode("utf-8"), stat.S_IMODE(item["st"].st_mode))
                current = os.stat(item["path"].name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev,current.st_ino)!=(item["st"].st_dev,item["st"].st_ino) or _read_fd(fd)!=item["before"]:
                    raise ValueError(f"target replaced during commit: {item['path']}")
                os.replace(staged[0], item["path"].name, src_dir_fd=parent, dst_dir_fd=parent)
                item["installed_id"] = staged[1]
                committed.append(item)
                os.fsync(parent)
                check = os.open(item["path"].name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    got = _read_fd(check)
                    if got != item["after"]: raise ValueError(f"exact readback failed: {item['path']}")
                    if (os.fstat(check).st_dev, os.fstat(check).st_ino) != item["installed_id"]:
                        raise ValueError(f"installed identity changed: {item['path']}")
                finally: os.close(check)
            finally:
                if staged is not None: _unlink_owned(parent, staged[0], staged[1])
                os.close(fd); os.close(parent)
    except BaseException as error:
        rollback_errors = []
        for item in reversed(committed):
            parent = None
            try:
                parent = _open_dir(item["path"].parent)
                if (os.fstat(parent).st_dev, os.fstat(parent).st_ino) != item["parent_id"]:
                    raise RuntimeError(f"rollback refused redirected parent: {item['path']}")
                current = os.stat(item["path"].name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev,current.st_ino) != item.get("installed_id"):
                    raise RuntimeError(f"rollback refused replaced target: {item['path']}")
                bfd = os.open(item["backup"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=backup_fd)
                try:
                    bst = os.fstat(bfd)
                    if (bst.st_dev, bst.st_ino) != item["backup_id"] or bst.st_size != item["backup_size"]:
                        raise RuntimeError(f"backup identity/size changed: {backup_dir / item['backup']}")
                    original = os.read(bfd, bst.st_size + 1)
                    if len(original) != item["backup_size"] or hashlib.sha256(original).digest() != item["backup_digest"]:
                        raise RuntimeError(f"backup content changed: {backup_dir / item['backup']}")
                finally: os.close(bfd)
                temp = _write_temp(parent, ".SOUL.rollback.", original, stat.S_IMODE(item["st"].st_mode))
                try: os.replace(temp[0], item["path"].name, src_dir_fd=parent, dst_dir_fd=parent); os.fsync(parent)
                finally: _unlink_owned(parent, temp[0], temp[1])
            except BaseException as exc: rollback_errors.append(f"{item['path']}: {exc}")
            finally:
                if parent is not None: os.close(parent)
        # Keep every recovery artifact if any rollback failed.
        if not rollback_errors:
            for item in transaction:
                try: _unlink_owned(backup_fd, item["backup"], item["backup_id"])
                except BaseException as exc: rollback_errors.append(f"backup cleanup {backup_dir / item['backup']}: {exc}")
        if rollback_errors:
            recovery = ", ".join(str(backup_dir / item["backup"]) for item in transaction)
            raise RuntimeError(f"apply failed ({error}); rollback/cleanup failed: {'; '.join(rollback_errors)}; recovery backups retained: {recovery}") from error
        raise
    finally:
        os.close(backup_fd)
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
