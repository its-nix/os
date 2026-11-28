---
name: company-ops
version: 1.2.0
description: "Use for LNX portfolio duties: the daily founder digest, the weekly NixKit/T3 improvement loop, and handling company-health alerts."
license: MIT
---

# Company operating loops (LNX)

LNX is the portfolio CEO, not a router. Venture leads keep running without it. These loops are native Hermes cron jobs in the default profile. Install or reconcile them with `scripts/setup_loops.py`, which is idempotent. Discord delivery requires the configured home channel, which LNX has.

| Loop | Trigger | Mode | Output |
| --- | --- | --- | --- |
| Company health | every 6 h | **no agent** (`health.py`): deterministic | silent unless a problem exists |
| Founder digest | daily 08:00 | **no agent** (`digest.py`) | Discord scorecard |
| NixKit + T3 improvement | weekly, Monday 05:00 | agent, bounded | at most one improvement PR, via T3 |

## Founder digest
The cron delivers the script's output directly. After deploying the script, run `scripts/setup_loops.py --reconcile` as the Hermes user to convert an existing digest in place and keep its schedule and delivery settings. Use `scripts/digest.py --preview` for a readback without advancing the snapshot.

The digest reads the registry, approval receipts, native Kanban boards, the deployment health receipt and opted-in Twenty workspaces. It also counts merged PRs for a declared repository when an authenticated GitHub CLI is already available. Each venture lists its lifecycle and economic metrics first. A manifest can declare verified numeric actuals in `outcome_metrics`, using `revenue`, `paying_customers`, `qualified_leads`, other charter metric slugs, or `incidents` for venture-scoped incidents. Keep units consistent across runs. CRM counts current qualified leads when the manifest has no qualified-lead actual. Revenue and paying customers require declared actuals. Company health shows deployment and backup incidents and the last verified deployed revision.

The scorecard compares against the last saved snapshot in `$HERMES_HOME/founder-digest.json`. Activity counters cover the last 24 hours and board counts describe current state. An in-progress card is stalled after 24 hours from its start. A known empty source yields zero and a missing or unreadable source yields `unavailable`. Revenue-blocking decisions appear first, oldest first, followed by other unresolved requests. Speed-to-lead rows use median durations from explicit receipt timestamps, grouped by when each interval ended. Todoist completion alone does not establish that a founder executed or declined an action.

## NixKit + T3 improvement
Look at the week's evidence: failed or blocked cards, retried runs, red CI on agent PRs, review findings that keep recurring. Pick at most **one** recurring failure pattern. Write a Kanban card with:
- the observed evidence
- the proposed change to one skill or workflow
- the metric that would show improvement, fixed *before* the change

Delegate the change to T3 (`t3-delegate`) as a PR with tests. The independent review and CI decide; you never edit the evaluator, its tests or the metric that judges the change. Record before/after on the card when the window closes, and say "inconclusive" when it is.
