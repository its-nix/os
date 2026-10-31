# Lead continuity contract (issue #49)

Status: repository contract based on merged monitoring PR #48 and verified native Hermes interfaces. This does not claim runtime activation, live delivery, role execution, or successful wake.

## Authority and source of truth

The manifest-declared project Lead owns objective acceptance and every recovery decision. Monitoring may collect bounded observations and PR #48's existing recovery planner may produce a preview; neither selects an action, creates a task, or substitutes for Lead judgment. The Lead rereads native Kanban/GitHub evidence, decides the next step, and records durable evidence on the native parent task. No independent decision ledger, event store, or workflow engine is introduced. Preserve manual approvals, cron denial, and gateway dispatch settings.

## Native Kanban contract

Use Hermes Kanban's durable parent/child links, task events and subscription cursor claims; the supported notifier mode is `notify+wake`. The subscription command contract is `hermes kanban notify-subscribe` with `--platform`, `--chat-id`, `--notifier-profile`, and `--delivery-mode notify+wake`. Route values are supplied by the repository manifest/configuration; do not bake project IDs or channel IDs into generic code. This is a supported interface contract, not evidence that any subscription is installed or a wake delivered. A notifier event is a hint: Lead rereads authoritative task state and avoids acting on stale/duplicate signals.

Webhook routing, if separately authorized and configured, uses the native `subscribe --route-profile <profile> --cron-job <existing job>` surface. This repository change does not activate it. Existing permissions, `approvals.mode=manual`, `approvals.cron_mode=deny`, and `kanban.dispatch_in_gateway=true` remain unchanged.

## Evidence boundaries

PR #48's `automation/monitoring/collector.py` gathers aggregate PR check counts/failure identities, but does not collect each PR's current `headRefOid` or CI run/check IDs and conclusions for that exact SHA. Therefore exact-head GitHub evidence is unknown/not available from this collector; do not derive it from aggregate failures or fabricate a matching SHA. Preserve unknown/partial until a separate authorized read obtains PR head SHA and check evidence, and the Lead reads it back. The pure normalization helpers can validate an explicitly supplied SHA, but do not themselves establish evidence provenance.

GitHub route envelope/event keys are deterministic correlation values only. `accept_once` is an in-process fixture helper; it loses state with the process. Neither hashing nor a preview envelope guarantees durable replay suppression, delivery, or wake. Native route/task-level idempotency and live behavior require separate verification.

## Tests and claims

Tests here exercise sanitization, exact-head input validation, deterministic preview output, and mocked/in-process boundaries. They do not demonstrate a live webhook, listener, subscription, notification, Lead wake, decision, or recovery execution. Reuse PR #48's monitoring/recovery implementation; do not add another collector, recovery engine, store, or workflow.

## Rollout gate

No listener/server was opened for this correction, including loopback. No runtime configuration/profile, webhook, subscription, permission, timer, or job was changed or activated. Live effective-config readback and delivery/wake verification remain gated on separate authorization. Tests/mocks are not operational proof. Protected actions and existing approval boundaries remain in force.
