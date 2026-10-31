# Lead continuity correction record

This repository-only correction makes no claim of end-to-end autonomous recovery. Unit tests exercise fixtures and pure preview helpers in process; no live native Kanban subscription, webhook, delivery, wake, or Lead execution was performed.

The repository implementation now collects exact-head check-run evidence from mocked/read-only collector paths, pinned to each PR's current `headRefOid`, with aggregate caps across mapped repositories (100 REST calls, 300 evidence/check rows, 8,000 serialized evidence bytes). This closes the prior implementation gap in source; it does not establish current live GitHub state. Missing/malformed or incomplete REST evidence and cap exhaustion remain partial/unknown, not green. No live `gh` request or network call was performed for this correction; the candidate still requires independent live readback by the Lead before any operational decision.

The GitHub adapter's stable event/idempotency keys are preview correlation data. `accept_once` is memory-only and does not provide durable replay suppression. Durable native delivery/idempotency must be verified separately; no custom decision store or recovery engine is used. Decisions belong to the project Lead and are recorded in native Kanban.

No listener or server was opened during this task, even on loopback. Tests use fixtures and mocked subprocess responses only. No runtime config/profile, webhook, subscription, permission, timer, or job was modified or activated. Live readback, delivery, wake, and role execution remain gated on separate authorization and verification.
