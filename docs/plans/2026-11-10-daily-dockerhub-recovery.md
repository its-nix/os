# Daily private Docker Hub recovery

> Implement inline with test-first changes and one fresh whole-branch review. The user explicitly requested daily private Docker Hub backups and authorized creating a properly named repository under their account. No additional plan approval is needed.

**Goal:** Publish a verified, dated `nixos-recovery` image daily to the user's private Docker Hub repository, preserving accumulated Hermes learning and complete Paperclip state.

**Design:** Briefly stop both native services under the deployment lock, capture the entire persistent deployment root, then restart services before building/uploading. Include Hermes profiles, memories, sessions, skills, credentials and groups; Paperclip's cold PostgreSQL data, files and matching keys; KB/Kit inputs and revision metadata. A recovery image extends the exact official Hermes runtime and explicitly embeds the archive in its writable layer. An isolated, network-disabled restore validates file coverage, SQLite integrity and Paperclip PostgreSQL readability before any private publication. Use native Docker login and a systemd timer on the VPS; no self-hosted GitHub runner, extra encryption or live agent execution in the restore test.

## Work and checks

- [x] Inventory native data, official runtime paths and registry access.
- [x] Obtain Docker Hub login through the nixos account’s native store.
- [x] Write failing tests for incomplete/corrupt archives, unsafe links, nonempty restore targets and failed publication ordering.
- [x] Implement full-state capture/restore by reusing existing recovery validation helpers; keep private diagnostics out of logs.
- [x] Verify real restored PostgreSQL and all durable Hermes state in isolated scratch containers; restore services on every failure path.
- [x] Create `nixos-recovery` privately under the authenticated user's account; fail closed if repository visibility cannot be verified.
- [x] Publish the first dated image, pull/verify its remote digest and restored contents, and record a success receipt.
- [x] Install and enable a persistent daily systemd timer, with failed-run retries and readable status.

Source delivery is evidenced by the accompanying documentation PR’s review, checks, merge and deployment records.

## Invariants

- Never publish state to a public or unverified repository, Git, CI artifacts or shared caches.
- Never delete or overwrite live state; restored databases never share the production directories or network.
- Capture stopped writers, preserve modes/ownership, verify all regular files and approved links.
- Always restart services after capture/failure; keep the previous successful backup receipt on failure.
- Dated registry images are retained; no registry deletion or unrequested retention policy.

## Verification evidence

Before registry activation on 2026-11-10: full Linux suite passed 287 tests (4 optional skips), source verifier and syntax checks passed, and independent review found no remaining blockers. Real VPS local-only recovery passed from the full native state, including standalone PostgreSQL and the recovery image. Both services returned healthy; temporary holder/image cleanup completed. At that point Docker Hub authentication, private repository creation, first remote digest readback and timer activation were pending; no state had been uploaded.

2026-11-10 activation: private personal `liornis/nixos-recovery` created with explicit privacy readback (anonymous metadata 404). First published tag `20261110T174037864615Z` completed at 17:45 UTC, 5,771 entries, manifest `sha256:d15ce5a2ac023b6019683955754fe3dd0e6c17420ddfc59b84a1112c5f038338`. Remote file/SQLite/PostgreSQL restoration passed; native systemd service exited 0. Persistent daily timer enabled, active/waiting for 2026-11-11 03:17 UTC. Both services healthy. The source documentation update and its CI/CD verification follow separately; future daily successes must be checked from native receipts.
