# Lead Continuity Implementation Plan

> **For Hermes:** Implement through the subagent-driven-development workflow, with tests first and separate specification/quality reviews.

**Goal:** Keep every governed Lead responsible for the verified project objective across child-task, worker, review, PR and CI interruptions, with direct-to-Lead wake/escalation and a bounded recovery fallback.

**Architecture:** Preserve Hermes Kanban as the durable task ledger and use its native per-task subscriptions with `notify+wake` for child lifecycle handoffs. Extend the merged monitoring foundation with a small generic, preview-first reconciler that observes sanitized Kanban/GitHub evidence and reawakens the owning Lead through Hermes' profile-bound webhook route; the Lead, not the monitor, chooses the next authorized action. Keep all listener/job activation and Discord test messages separately gated; never expose a public listener or create Kanban tasks from a monitor script.

**Tech Stack:** Python standard library; existing `automation/monitoring/{collector.py,monitor_snapshot.py,recovery.py,reconcile.py}`; Hermes Kanban notify subscriptions and profile-bound webhook routes; GitHub CLI/API read-only evidence; unittest.

---

## Verified runtime constraints

- Installed Hermes is `v0.22.5+1714.g9c36150` under `${HOME}/.hermes/hermes-agent`, clean working tree and one local commit ahead of origin. Read-only CLI help exposes `kanban notify-subscribe --delivery-mode notify+wake --notifier-profile <profile>`; this proves interface flags only, not subscription or wake delivery.
- Native Kanban task creation auto-subscribes the originating gateway session by default; subscriptions are task-specific, durable, and can wake the source profile. Worker lifecycle events are durable; observer hooks are best-effort and are not the delivery guarantee.
- Hermes source/CLI exposes profile-bound webhook routes, but no live webhook-to-Discord delivery has been verified. `platforms.webhook.enabled` is unset/not enabled. Do not enable it, bind any listener (including loopback), open a port, install a timer, or send live Discord test messages as part of repository implementation. Any later listener/service rollout needs separate approval, preview/readback, and network-security review; prefer a supported local polling path when it avoids inbound exposure.
- `approvals.mode=manual`, `approvals.cron_mode=deny`, and `kanban.dispatch_in_gateway=true` must remain unchanged. Never let monitoring directly create tasks, alter code, merge, deploy, or cross finance/privacy/credential boundaries.
- The OS source now includes merged PR #48 (`cf8dd8bb798c71caf05fa4ca9bb8550780ac3bc4`) and PR #52 (`f0f00cd7b5e403e108db93bba1ab66e77467e986`). #50 and #51 remain open pending live delivery/runtime proof.

## Current implementation snapshot (2026-10-31)

The candidate branch is a limited, repository-only preview contract—not completion of #49. It includes bounded pure GitHub fixture adaptation and `automation/monitoring/github-webhook-routes.json`, a non-active contract for per-project native agent-mode routes bound through `nix-os.yaml` project/Lead/channel mappings. The former cron-style envelope and in-process dedupe helper are removed: neither models native admission. Native durable admission suppresses delivery retries, not duplicate Lead actions or side effects; Lead must reread canonical Kanban and live exact-head PR state before acting. This repository contract is not an enabled route. No live ingress, secrets, listener/restart, delivery/readbacks, subscriptions, or wakes are configured or verified; those remain separately gated.

Read-only active-runtime observations: manifest and `gateway.profile_routes` match the three project-channel→Lead mappings; webhook enablement is unset/not enabled; no Kanban continuity subscriptions are installed for #49; `approvals.mode=manual`, `approvals.cron_mode=deny`, and `kanban.dispatch_in_gateway=true`; the existing MeaBeclick profile Cron job's latest run did not deliver because that profile has no enabled Discord platform. These observations do not authorize runtime changes.

## Task 1: Define durable Lead objective and event evidence contract

**Objective:** Add a generic, explicit objective/child/PR/evidence identity contract and tests without changing existing authority boundaries.

**Files:**
- Modify: `nix-os.yaml`
- Modify: `profiles/lnx/ROLE.md`, `profiles/pulse/ROLE.md`, `profiles/algua/ROLE.md`, `profiles/meabeclick/ROLE.md`
- Modify: `docs/ARCHITECTURE.md`, `docs/MONITORING.md`
- Test: `tests/test_repository.py`, `tests/test_monitoring.py`

**Acceptance:** One reusable contract for new governed projects; parent objective is not complete when a child/PR completes; Leads own outcome/recovery/direct communication; specialists report to Lead; LNX only observes portfolio/systemic exceptions. Existing approval rules remain identical.

## Task 2: Add generic, idempotent continuity signal normalization

**Objective:** Normalize fresh, complete task and exact-head GitHub evidence into stable per-project Lead wake events.

**Files:**
- Create: `automation/monitoring/continuity.py`
- Modify: `automation/monitoring/collector.py` only for the minimum bounded sanitized exact-head per-check evidence needed
- Test: `tests/test_monitoring_continuity.py`, `tests/test_monitoring_sqlite_observer.py`

**Tests first:** Duplicate/reordered CI events deduplicate; failure-to-pass transition changes event identity; stale/missing/wrong-head evidence yields unknown and never a false success; paused/unmapped project and malformed source fail closed; private PR/task bodies never appear in event payload.

## Task 3: Add profile-bound Lead wake reconciliation in preview-first mode

**Objective:** Turn normalized events into a generic, stable webhook handoff for the declared Lead and its existing Discord project route, without executing recovery policy.

**Files:**
- Create: `automation/monitoring/lead_wake.py`
- Create: `scripts/reconcile-lead-continuity.py`
- Modify: `automation/monitoring/jobs.json` and `automation/monitoring/reconcile.py` only if required by the proven runtime interface
- Test: `tests/test_monitoring_lead_wake.py`, `tests/test_monitoring_scheduler.py`

**Constraints:** Default is preview only. Validate only manifest-declared project/Lead/channel mappings. Route profile and destination must match. Local-only delivery; never infer a public URL, create/open external GitHub webhooks, read secrets into logs, or bypass the Hermes webhook/approval interfaces. Unknown listener configuration refuses. Repeated event keys do not create duplicate tasks or unbounded retries.

## Task 4: Prove event-to-wake and missed-event fallback with injected events

**Objective:** Exercise the generic end-to-end orchestration path without sending to live Discord or activating a schedule.

**Files:**
- Test: `tests/test_monitoring_continuity_integration.py`
- Test fixtures: `tests/fixtures/monitoring/`

Inject a simulated exact-head CI failure or worker interruption, verify the correct Lead/profile route receives one wake payload, confirm the Lead action is left to the agent, ensure duplicate events are suppressed, then inject a later passing exact-head observation and verify completion evidence. Simulate a missed event and prove bounded reconciliation produces the same Lead wake. Include a negative test showing merge/deploy/live-trading/destructive actions still require approval.

## Task 5: Review, PR, CI and rollout gate

- Run `python3 scripts/verify.py`, `python3 -m unittest discover -s tests -v`, `git diff --check`, and any monitoring systemd syntax checks.
- Review exact-head CI/review state and read the opened PR back.
- Keep runtime scheduling/webhook enablement inactive. A separate approval is required for localhost listener/service rollout and for messages in live project channels; after approval, back up, preview, apply through supported Hermes commands, and read back the served profiles, route profile, loopback bind, subscriptions, timer state, and sanitized direct-delivery evidence.
- Do not close #49 until a real runtime test has shown the Lead wakes without Lior prompting, chooses/executes authorized recovery, validates exact-head evidence, or proactively escalates the genuine human-only decision directly in the responsible Lead channel.
