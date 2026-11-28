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
| Founder digest | daily 08:00 | agent, bounded | one short Discord message |
| NixKit + T3 improvement | weekly, Monday 05:00 | agent, bounded | at most one improvement PR, via T3 |

## Company health

The existing host approval timer refreshes `nix/health.json` under the deploy lock. The check reports degraded Compose services, a stale approval worker (15 minutes), stale recovery or pack sync, missing T3 harness auth files, and exhaustion of the configured reviewer pool. Reviewer cooldown entries contain provider names and reset times. Auth presence is a filesystem signal. Verify a suspected login failure with the [provider recovery runbook](../../docs/runbooks/PROVIDER-AUTH-RECOVERY.md).

The default profile and active venture profiles contribute cron failures and usage data. The check reports blocked cards and running cards older than 24 hours on their boards. For usage, it compares the last seven days with the preceding seven days. It alerts when recorded cron tokens or recorded model cost at least double a positive baseline, with an increase of at least 100,000 tokens or $5 respectively. Cost uses actual spend when recorded and otherwise the estimate. Session usage rows that span windows are excluded.

To enable the builder PAT reminder, record its expiry in the root-owned host file `/etc/nixos/builder-pat-expiry` as a single UTC date in `YYYY-MM-DD` format. Update the date when rotating the PAT. The check alerts within 14 days of expiry.

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
