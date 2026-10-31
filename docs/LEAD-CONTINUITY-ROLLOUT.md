# Lead continuity: preview-first rollout record

## Preview results (isolated; 2026-10-31)

- `python3 -m unittest tests.test_lead_recovery_e2e -v`: **3 passed**. Injected child failure and review terminal observations plus a GitHub check bound to an exact 40-character PR head; verified the normalized signal routes to the named Lead job envelope, repeat delivery is suppressed, and explicitly allowlisted recovery remains a preview proposal.
- The same test suite verifies an exited/crashed worker is reconciled when its terminal event is absent, arbitrary stderr is excluded, and a protected/unallowlisted action yields a human-approval escalation instead of a recovery proposal.
- `python3 -m unittest tests.test_monitoring_lead_decision tests.test_github_signals tests.test_lead_continuity_contract -v`: **9 passed**.
- Existing isolated runtime smoke evidence recorded in the task thread: disposable `HERMES_HOME`, profile-bound `webhook -> cron_job` probe; loopback `/health` returned `ok`, injected webhook returned HTTP 202, and the no-agent profile Cron marker ran. No live profile, Discord route, or user message was used in this task.

## Runtime readback and rollout gate

This checkout's runtime configuration was **not activated or modified**. No production profile/config/service readback was performed; this is intentional, since runtime activation and permission changes require explicit authorization. Static evidence is limited to the repository continuity contract and tests asserting the declared Lead event/wake contract. Before any real rollout, an authorized operator must read back the effective configuration and service state, confirm the intended profile-bound routes and notification permissions, run a preview against a disposable test target, inspect resulting task/evidence records, then request explicit approval before enabling behavior. Keep runtime changes and permission grants out of this change.

## Boundaries

All generated decisions are `preview_only`; this code does not dispatch recovery, post Discord messages, or alter permissions. Exact-head evidence must match the observed PR head. Duplicate suppression in the end-to-end test is injected/in-process; durable production replay protection belongs to the native route/task idempotency seam. Do not treat these tests or the isolated smoke as proof of live operational delivery.
