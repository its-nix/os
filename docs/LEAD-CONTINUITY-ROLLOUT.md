# Lead continuity correction record

This repository-only correction makes no claim of end-to-end autonomous recovery. Unit tests exercise fixtures and pure preview helpers in process; no live native Kanban subscription, webhook, delivery, wake, or Lead execution was performed.

The exact-head GitHub gate remains unresolved by the existing PR #48 collector: it reports aggregate check counts and bounded failure identities but not a PR's current full head SHA together with check-run IDs/conclusions for that exact SHA. Until independently collected and read back by the Lead, exact-head evidence is unknown/partial, not green.

The GitHub adapter's stable event/idempotency keys are preview correlation data. `accept_once` is memory-only and does not provide durable replay suppression. Durable native delivery/idempotency must be verified separately; no custom decision store or recovery engine is used. Decisions belong to the project Lead and are recorded in native Kanban.

No listener or server was opened during this task, even on loopback. No runtime config/profile, webhook, subscription, permission, timer, or job was modified or activated. Live readback, delivery, wake, and role execution remain gated on separate authorization and verification.
