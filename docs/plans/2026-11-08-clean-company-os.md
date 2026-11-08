# Clean Company OS Implementation Plan

> **For agentic workers:** Use executing-plans inline, with test-first changes and a fresh whole-branch review. The user has approved proceeding; do not add an extra plan-approval round.

**Goal:** Deliver a clean official-image VPS pilot with native groups and a first-class company knowledge loop, ready for the user's later Codex login.

**Architecture:** Native Paperclip orchestration invokes exact Hermes profiles; native Bot Groups provide discussions. Existing Git-backed knowledge tooling supplies reviewed organizational context. Isolate this rollout from the stopped previous deployment and the separate recovery PR.

**Tech Stack:** Docker Compose, official images, Python standard library/unittest, existing KB tools, GitHub CLI.

**Spec:** [Clean company OS](../CLEAN-COMPANY-OS.md).

## Global Constraints

- Fresh `nix-clean` project and state; no old VPS configuration, patches, volumes or credentials.
- No provider authentication until Lior connects Codex; no invented successful agent runs.
- Native image entrypoints, built-in adapter, `/p/<profile>` mapping and native Bot Groups; no custom bridge.
- Loopback-only listeners; trusted single-operator pilot; scoped secrets stay outside Git/output.
- KB schema/review remains in `its-nix/knowledgebase`; no automatic promotion or transcript ingestion.
- Keep recovery PR separate; no backup publication until a full isolated restore passes.

## Review Focus

- A profile key must not authenticate another profile's requests.
- A healthy process must not be reported as a successful authenticated agent task.
- Restarting must preserve profile, group and controller state.
- Private KB content/credentials must not enter the public OS repository or logs.
- Current-only retrieval must withhold unsupported/stale knowledge and preserve citations.

### Task 1: Declare the redesign and pinned inputs

**Files:** `docs/CLEAN-COMPANY-OS.md`, this plan, `scripts/verify.py`, `tests/test_repository.py`, `deploy/versions.json`.
**Produces:** explicit design, immutable image/source/KB/Kit pins, and continued repository safety checks.

- [ ] Add a failing verifier regression allowing the explicitly approved Paperclip design while retaining other legacy/secret rejection.
- [ ] Update only the obsolete name prohibition; preserve historical receipts and existing deployment semantics.
- [ ] Record image digests and source revisions from actual official registry readback; validate pins.
- [ ] Run focused tests and repository verification; commit.

### Task 2: Prepare the fresh native stack

**Files:** `deploy/compose.yaml`, `deploy/README.md`, `deploy/roles/{lead,engineer,reviewer}.md`, `scripts/prepare-clean-os.py`, `tests/test_clean_os.py`.
**Consumes:** immutable pins and the approved design.
**Produces:** preview-first preparation of fresh native homes and profile instructions; official Compose services on loopback.

- [ ] Write a failing test for preview non-mutation and refusal to overwrite existing homes/credentials.
- [ ] Implement minimal file preparation and validation; use native tools for profile/service/agent operations.
- [ ] Verify Compose configuration, image startup and source revisions on the VPS under the fresh project.
- [ ] Read back profile API scopes and native group capabilities. Preserve stopped old deployments and unrelated workloads.
- [ ] Verify restart persistence. Keep provider-dependent work pending and unattended schedules disabled.

### Task 3: Integrate the knowledge contract and pilot identities

**Files:** `deploy/README.md`, role instructions, `deploy/pilot.json`; companion KB contract PR.
**Consumes:** native runtime endpoints, exact profile names and pinned company KB.
**Produces:** matching Paperclip/Hermes identities and native group plus current-only KB retrieval and explicit knowledge-impact handoff.

- [ ] Use native controller APIs/CLI to register the pilot and group; verify identity readback.
- [ ] Exercise existing KB unit tests, validation/retrieval and curation on the immutable read-only copy; do not print private note bodies.
- [ ] Propose the focused KB terminology/ownership update without promoting provisional company claims.
- [ ] Record ready/pending evidence. A missing native capability blocks that step without introducing a bridge.

### Task 4: Verify, review and hand off staging

**Files:** deployment guide and PR descriptions.
**Produces:** verified staged deployment and reviewable OS/KB PRs; explicit Codex-dependent acceptance checklist.

- [ ] Run the OS suite/verifier, syntax and Compose checks; run the KB's existing checks.
- [ ] Obtain one fresh whole-branch review; fix consequential findings with regressions.
- [ ] Open focused PRs and check CI. Document fresh state paths, access commands and exact remaining authenticated checks.
- [ ] After Codex is connected, prove a real three-agent task, connector read/write boundaries, knowledge proposal and container-native recovery before scheduling or broader rollout.
