# Hermes Recovery Images Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan inline.

**Goal:** Preserve the complete portable Hermes state in dated private Docker images, built daily and verified by an isolated restore.

**Architecture:** Use native full backup for consistent SQLite snapshots, then embed the archive in a pinned Hermes runtime. A small standard-library recovery command validates and restores into a fresh volume without starting services. A trusted daily Actions job in a private control repository runs on the source host and publishes only after verification.

**Tech Stack:** Python standard library, unittest, Docker, GitHub Actions and GHCR.

**Spec:** The agreed conversation: include memories, sessions, learned skills, profiles and credentials; private registry; no additional encryption; retain older images; verify restores.

## Global Constraints

- No live state or credentials in Git, Actions artifacts, caches, or command output.
- Preview first; explicit apply and publish flags. No automatic gateway or cron activation.
- Pin the runtime by digest and require its source revision to match the installed Hermes checkout.
- Restore only into an empty directory. Preserve dated images; no automatic deletion.
- Public PR CI uses synthetic fixtures only. The daily source-host job has no PR trigger.

## Review Focus

- Unsafe ZIP paths and symlinks must fail before creating a destination.
- Corrupt databases or archives must prevent publication.
- Missing learned skills, including symlinked installations, must not silently pass.
- A restore must never start a gateway or touch an existing home.
- Registry access alone does not prove privacy; publication must check package visibility.

### Task 1: Verified archive restoration

**Files:** `automation/recovery/recovery.py`, `tests/test_recovery.py`.
**Interface:** `inspect_archive(path)` validates a native full backup; `restore(archive, destination)` restores to `destination/hermes` and `destination/user`.

- [x] Add a failing test preserving memories, sessions, learned skills, named profiles and SQLite rows.
- [x] Implement validated extraction, hash readback, database integrity checks and owner-only permissions.
- [x] Add and pass tests for unsafe members, corrupt databases, nonempty destinations and inert runtime markers.

### Task 2: Preview-first build and publication

**Files:** `automation/recovery/create.py`, `automation/recovery/Dockerfile`, `tests/test_recovery_create.py`.
**Interface:** CLI receives explicit source home, Hermes binary/source, immutable runtime image and GHCR image name. Default preview; `--apply` backs up, builds and verifies; `--publish` additionally checks private visibility and pushes one dated tag.

- [x] Add failing tests for preview, revision mismatch, missing skill coverage and verification-before-push.
- [x] Implement private temporary context, native backup, manifest, image build, isolated canary and private registry preflight.
- [x] Pass focused tests and a real Docker canary with synthetic state.

### Task 3: Daily workflow and operations

**Files:** `.github/workflows/hermes-recovery.yml`, `docs/HERMES-RECOVERY.md`, `README.md`, `docs/STATE-AND-SECRETS.md`.

- [x] Add trusted scheduled/manual workflow, disabled until repository variables identify the source host and private package.
- [x] Document private package setup, runner identity, runtime revision upgrades, retention, restore and activation.
- [x] Run repository checks and obtain a fresh whole-branch review, then create a PR.

The workflow is intentionally inert in the public OS repository. Copy it to a private control repository before registering the state-holding source runner. Whole-branch review and PR checks are recorded in the PR.
