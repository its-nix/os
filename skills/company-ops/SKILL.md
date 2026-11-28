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
| NixKit + T3 improvement | weekly, Monday 05:00 | agent, bounded, **idle-gated** | at most one improvement PR, via T3 |

## Evidence and the idle gate
`scripts/collect_evidence.py` builds a bounded, deterministic evidence bundle from local Hermes state: cron outcomes and failure streaks, `usage_audit.jsonl` token burn, `skills/.usage.json`, Kanban card outcomes (repeated card patterns, `changes_requested` corrections), undelivered approvals and, for this loop only, two public GitHub calls (merged/unmerged PRs, CI failures) for `its-nix/os`. Unreachable sources read `unavailable`; card text is reduced to short lowercase signatures.

The deployment installs it as `gate-outcome.py`, `gate-distill.py` and `gate-improvement.py` in each profile's `scripts/nix-os/`, used as the loop's pre-run `--script`. When nothing needs judgment it prints `{"wakeAgent": false}` and Hermes skips the agent, so idle weeks cost no tokens. It wakes the agent when there is open work, a pattern or correction that recurs at least twice, a cron failing twice in a row, two CI failures, or an unreadable source (fail open). Hermes refuses a job whose script file does not exist, so `venture.py` places the gate files in a new venture's profile itself (ungated if it cannot) and every deploy refreshes all copies. `setup_loops.py`, run after a deploy, adds the gate to the LNX improvement job and reconciles only that `script` field; before the gate file exists it leaves the job ungated. To undo, `hermes cron edit <job-id> --script ""`.

## Loop contract
Every loop states all eleven fields. Defaults are bounds, not targets; tighten per venture, never loosen without a founder decision.

| Field | Venture outcome | Workflow distillation | NixKit + T3 improvement | Company health |
| --- | --- | --- | --- | --- |
| Owner | venture lead | venture lead | LNX | LNX (no agent) |
| Scope | one venture: board, charter, repos, CRM workspace | one venture's repeated work | NixKit skills, OS scripts, CI checks | deployment, backup, disk, T3, approvals, blocked cards |
| Trigger | daily 07:00, only with open cards, undelivered approvals, or a charter review overdue (7 days since the last real run) | Monday 06:00, only with a repeated pattern or correction | Monday 05:00, only with evidence from the gate | every 6 h |
| Objective | highest-value next task toward the charter metric | turn a repeated success or correction into a deterministic check | fix one recurring failure | stay silent unless something needs attention |
| Source-of-truth metric | charter metric, read from a source system | recurrences of the correction, tokens per run (evidence bundle) | evidence-bundle numbers fixed on the card before the change | `nix/health.json` and the frozen health eval |
| Allowed change surface | venture board, CRM, repos via T3 PR; external effects queued | one script, check or skill via T3 PR | one skill, script or check via T3 PR | none (read-only) |
| Budget and retry | one pass; at most 1 card started; about 150k tokens; cards with `--max-runtime 2h --max-retries 2`; no blind retry of an ambiguous effect; spend 0 | one PR; about 200k tokens; one retry after red CI | one PR per week; about 250k tokens; one retry after red CI | 0 tokens; T3 probe 5 s |
| Evaluator | source-system evidence, never the agent's own claim | CI, independent review and existing tests | CI, independent review and the frozen evaluators | `tests/health_eval.py` |
| Decision window | charter date; review weekly | 4 weekly runs | 2 weekly runs (14 days) | alert persists until the condition clears |
| Promote or rollback | continue, pivot or pause per charter; revert by PR | merge after independent review and green CI; revert by PR | same; revert by PR if the metric worsens or is inconclusive at the window end | n/a; fix through the improvement loop |
| Reporting | one line on the card; founder only for exceptions | card with before and after | card with before and after, one digest line; say "inconclusive" when it is | Discord on exception only |

## Deterministic-first distillation
When the same correction or rejection recurs (at least 2 sanitized examples, e.g. `repeated_corrections` in the bundle), first propose a script, CI check, lint or API constraint that makes the mistake impossible. Fall back to skill prose only when a deterministic check is not possible, and say why on the card. Every change goes through a T3 PR; nothing is auto-applied. A PR may add a new check but must not edit or delete the existing tests, evaluator, metric, CI workflow or CODEOWNERS entries that judge it. Those changes go in a separate PR with founder review.

## Founder digest
Read the venture registry (`venture.py status`) and each venture board. Report:
- progress against each charter's metric, from source systems only
- verified PRs merged or deployed
- blocked cards older than 24 h
- approval requests waiting on the founder
- incidents and unexpected cost

No narration of activity without outcomes. If nothing changed, say so in one line.

## NixKit + T3 improvement
Start from the evidence bundle above. Look at the week's evidence: failed or blocked cards, retried runs, red CI on agent PRs, review findings that keep recurring. Pick at most **one** recurring failure pattern. Write a Kanban card with:
- the observed evidence
- the proposed change to one skill or workflow
- the metric that would show improvement, fixed *before* the change

Delegate the change to T3 (`t3-delegate`) as a PR with tests. The independent review and CI decide; you never edit the evaluator, its tests or the metric that judges the change. Record before/after on the card when the window closes, and say "inconclusive" when it is.
