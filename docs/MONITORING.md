# Monitoring foundation

`automation/monitoring/collector.py` is deterministic and read-only. It joins Nix OS `projects`/`profiles` declarations to a deliberately narrow runtime metadata snapshot (profile and board IDs, task metadata, status probes). It excludes paused declared projects, labels runtime-only projects/profiles unclassified, sorts output, and hashes task observations for idempotent dedupe. KPI values are emitted only for explicit `metrics` declarations with a source; absent source values are `unknown`, never inferred. Task output is allowlisted; do not add card bodies, prompts, logs, config, secrets, session content, or credentials.

## Inputs and safe integration

The CLI accepts explicit `--manifest` and either `--runtime` (a synthetic or vetted snapshot) or `--live`. Live mode invokes fixed read-only Hermes commands and requests archived Kanban boards/tasks. Status is reported available only when recognized healthy state text is present; empty, failed, or unrecognized responses remain unknown. Task states and integer epoch timestamps are normalized; stale means at least 72 hours. `throughput` counts observed completed tasks, while `throughput_7d` counts completed tasks with a completion timestamp in the last seven days. GitHub PR/check summaries are queried by read-only `gh` only for repositories explicitly declared in policy `repos` (project ID to `owner/repo`); summaries contain counts only. Undeclared repositories are never queried and raw PR fields/check metadata are discarded. Project and board archived/paused flags exclude them from project output.

The collector does not schedule itself, write live state, retry or dispatch fixes. `policy.json` is a policy proposal: 12h cadence, two bounded retry attempts, manual approvals required, `cron_mode=deny`, paused-project exclusion, critical-only alerts, weekly summary. Automatic recovery, Discord alerts, an operational weekly schedule, and an operational watchdog are not implemented or operational. No live schedule, task, notification, or configuration was created.

## Watchdog recommendation (design only)

When separately authorized, supervise the collector with an independent watchdog process/timer that checks gateway, scheduler and collector heartbeat, enforces a single-instance lock, bounds execution time, records only sanitized health counters, and emits critical alerts only after consecutive failures. Keep the collector read-only and watchdog output append-only outside Git; watchdog must not restart services or mutate Hermes unless a later explicit change grants that authority. A 12-hour data pass plus a shorter independent heartbeat check avoids one scheduler failure hiding itself.

## Local synthetic test

`python3 -m unittest tests.test_monitoring -v` exercises full collection twice using synthetic fixture inputs and temporary output. It asserts new project/profile/board discovery, paused exclusion, unclassified scope, stable dedupe, retry policy as inert data, and unknown KPI. No live Hermes state is touched.
