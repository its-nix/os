---
name: company-ops
description: "Use for LNX portfolio duties: the daily founder digest, the weekly NixKit/T3 improvement loop, and handling company-health alerts."
license: MIT
---

# Company operating loops (LNX)

LNX is the portfolio CEO, not a router. Venture leads keep running without it. These loops are native Hermes cron jobs in the default profile. Install or reconcile them with `scripts/setup_loops.py`, which is idempotent.

| Loop | Trigger | Mode | Output |
| --- | --- | --- | --- |
| Company health | every 6 h | **no agent** (`health.py`): deterministic | silent unless a problem exists |
| Founder digest | daily 08:00 | agent, bounded | one short Discord message |
| NixKit + T3 improvement | weekly, Monday 05:00 | agent, bounded | at most one improvement PR, via T3 |

## Founder digest
Read the venture registry (`venture.py status`) and each venture board. Report:
- progress against each charter's metric, from source systems only
- verified PRs merged or deployed
- blocked cards older than 24 h
- approval requests waiting on the founder
- incidents and unexpected cost

No narration of activity without outcomes. If nothing changed, say so in one line.

## NixKit + T3 improvement
Look at the week's evidence: failed or blocked cards, retried runs, red CI on agent PRs, review findings that keep recurring. Pick at most **one** recurring failure pattern. Write a Kanban card with:
- the observed evidence
- the proposed change to one skill or workflow
- the metric that would show improvement, fixed *before* the change

Delegate the change to T3 (`t3-delegate`) as a PR with tests. The independent review and CI decide; you never edit the evaluator, its tests or the metric that judges the change. Record before/after on the card when the window closes, and say "inconclusive" when it is.
