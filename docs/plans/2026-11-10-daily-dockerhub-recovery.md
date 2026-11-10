# Daily private Docker Hub recovery

> Implement inline with test-first changes and one fresh whole-branch review. The user explicitly requested daily private Docker Hub backups and authorized creating a properly named repository under their account. No additional plan approval is needed.

**Goal:** Publish a verified, dated `nixos-recovery` image daily to the user's private Docker Hub repository, preserving accumulated Hermes learning and complete Paperclip state.

**Design:** Briefly stop both native services under the deployment lock, capture the entire persistent deployment root, then restart services before building/uploading. Include Hermes profiles, memories, sessions, skills, credentials and groups; Paperclip's cold PostgreSQL data, files and matching keys; KB/Kit inputs and revision metadata. A recovery image extends the exact official Hermes runtime and explicitly embeds the archive in its writable layer. An isolated, network-disabled restore validates file coverage, SQLite integrity and Paperclip PostgreSQL readability before any private publication. Use native Docker login and a systemd timer on the VPS; no self-hosted GitHub runner, extra encryption or live agent execution in the restore test.

## Work and checks

- [ ] Inventory native data and registry access; obtain Docker Hub login through its native store.
- [ ] Write failing tests for incomplete/corrupt archives, unsafe links, nonempty restore targets and failed publication ordering.
- [ ] Implement full-state capture/restore by reusing existing recovery validation helpers; keep private diagnostics out of logs.
- [ ] Verify real restored PostgreSQL and all durable Hermes state in isolated scratch containers; restore services on every failure path.
- [ ] Create `nixos-recovery` privately under the authenticated user's account; fail closed if repository visibility cannot be verified.
- [ ] Publish the first dated image, pull/verify its remote digest and restored contents, and record a success receipt.
- [ ] Install and enable a persistent daily systemd timer, with failed-run retries and readable status.
- [ ] Update current OS documentation/status, run existing checks and fresh review, merge and verify CI/CD.

## Invariants

- Never publish state to a public or unverified repository, Git, CI artifacts or shared caches.
- Never delete or overwrite live state; restored databases never share the production directories or network.
- Capture stopped writers, preserve modes/ownership, verify all regular files and approved links.
- Always restart services after capture/failure; keep the previous successful backup receipt on failure.
- Dated registry images are retained; no registry deletion or unrequested retention policy.
