# Lead continuity contract (issue #49)

Status: repository-only preview contract based on merged monitoring PR #48. CLI help/source evidence is not proof of a live subscription, delivery, or wake; none is claimed.

## Authority and source of truth

The manifest-declared project Lead owns objective acceptance and every recovery decision. Monitoring may collect bounded observations and PR #48's existing recovery planner may produce a preview; neither selects an action, creates a task, or substitutes for Lead judgment. The Lead rereads native Kanban/GitHub evidence, decides the next step, and records durable evidence on the native parent task. No independent decision ledger, event store, or workflow engine is introduced. Preserve manual approvals, cron denial, and gateway dispatch settings.

## Native Kanban contract

Use Hermes Kanban's durable parent/child links, task events and subscription cursor claims; the supported notifier mode is `notify+wake`. The subscription command contract is `hermes kanban notify-subscribe` with `--platform`, `--chat-id`, `--notifier-profile`, and `--delivery-mode notify+wake`. Route values are supplied by the repository manifest/configuration; do not bake project IDs or channel IDs into generic code. Automated read-only `hermes kanban notify-subscribe --help` and source-contract checks establish CLI interface exposure only. They do not invoke the mutating subcommand or prove a subscription is installed, a notification delivered, or a wake received. A notifier event is a hint: Lead rereads authoritative task state and avoids acting on stale/duplicate signals.

Webhook routing, if separately authorized and configured, uses the native `subscribe --route-profile <profile> --cron-job <existing job>` surface. This repository change does not activate it. Existing permissions, `approvals.mode=manual`, `approvals.cron_mode=deny`, and `kanban.dispatch_in_gateway=true` remain unchanged.

## Evidence boundaries

The collector obtains each PR's check-run records from GitHub REST at the immutable `headRefOid`, and includes numeric check-run IDs only when every record validates against that exact SHA. Exact-head evidence is available only when the REST response is complete and non-empty and all records validate: unique positive IDs, allowlisted status/conclusion, valid supplied timestamps, and matching SHA. A null `started_at` is accepted only for statuses where it is semantically valid; a completed run needs a valid completion timestamp and conclusion. Missing required keys, malformed timestamps, duplicate IDs, total-count mismatch, wrong SHA, empty sets, API errors, and caps remain partial/unknown, not success. Aggregate resource limits apply across all mapped repositories: 100 REST calls, 300 PR evidence rows and 300 check rows (separate counters), and 8,000 bytes of serialized sanitized PR evidence output; exhausted limits stop further requests. Rejected or truncated rows make the affected repository partial/unknown. Review evidence remains unknown/not collected. Event-to-wake, delivery, recovery, and issue closure are not verified.

GitHub route envelope/event keys (including `correlation_key`) are deterministic correlation values only and do not enforce deduplication. `accept_once` is an in-process fixture helper; it loses state with the process. Neither hashing nor a preview envelope guarantees durable replay suppression, delivery, or wake. Native route/task-level idempotency and live behavior require separate verification.

## Tests and claims

Tests here exercise sanitization, exact-head input validation, deterministic preview output, the read-only CLI help flags when Hermes CLI is available, and mocked/in-process boundaries. They do not demonstrate a live webhook, listener, subscription, notification, Lead wake, decision, or recovery execution. Reuse PR #48's monitoring/recovery implementation; do not add another collector, recovery engine, store, or workflow.

## Rollout gate

No listener/server was opened for this correction, including loopback. No runtime configuration/profile, webhook, subscription, permission, timer, or job was changed or activated. Live effective-config readback and delivery/wake verification remain gated on separate authorization. Tests/mocks are not operational proof. Protected actions and existing approval boundaries remain in force.
