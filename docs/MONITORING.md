# Monitoring foundation

`automation/monitoring/collector.py` is deterministic and read-only. It joins Nix OS `projects`/`profiles` declarations to a deliberately narrow runtime metadata snapshot (profile and board IDs, task metadata, status probes). It excludes paused declared projects, labels runtime-only projects/profiles unclassified, sorts output, and hashes task observations for idempotent dedupe. KPI values are emitted only for explicit `metrics` declarations with a source; absent source values are `unknown`, never inferred. Task output is allowlisted; do not add card bodies, prompts, logs, config, secrets, session content, or credentials.

## Inputs and safe integration

The CLI accepts explicit `--manifest` and either `--runtime` (a synthetic or vetted snapshot) or `--live`. Live mode invokes only fixed read-only Hermes commands: profile list, board list JSON, board task list JSON, cron list, and gateway status. Task and status data is reconstructed from strict allowlists; bodies, prompts, logs, config, secrets, sessions, workspace paths, and credentials are discarded. The adapter reports only whether gateway/scheduler commands returned usable output, not independent service heartbeats. It does not currently query GitHub PRs/checks, project KPI sources, or scheduler execution history; those remain unimplemented evidence sources. A future GitHub adapter must require an explicit repository mapping. Process/gateway/scheduler/heartbeat probes should expose state only; do not collect command lines, environment, logs, or payloads. Throughput currently means count of observed tasks whose status is `done`/`completed`; age and stale status use explicit task timestamps and the policy threshold.

The collector does not schedule itself, write live state, retry or dispatch fixes. `policy.json` is a policy proposal: 12h cadence, two bounded retry attempts, manual approvals required, `cron_mode=deny`, paused-project exclusion, critical-only alerts, weekly summary. No real schedule, task, notification, or configuration was created.

## Watchdog recommendation (design only)

When separately authorized, supervise the collector with an independent watchdog process/timer that checks gateway, scheduler and collector heartbeat, enforces a single-instance lock, bounds execution time, records only sanitized health counters, and emits critical alerts only after consecutive failures. Keep the collector read-only and watchdog output append-only outside Git; watchdog must not restart services or mutate Hermes unless a later explicit change grants that authority. A 12-hour data pass plus a shorter independent heartbeat check avoids one scheduler failure hiding itself.

## Local synthetic test

`python3 -m unittest tests.test_monitoring -v` exercises full collection twice using synthetic fixture inputs and temporary output. It asserts new project/profile/board discovery, paused exclusion, unclassified scope, stable dedupe, retry policy as inert data, and unknown KPI. No live Hermes state is touched.
