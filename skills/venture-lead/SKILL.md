---
name: venture-lead
description: "Use on every venture outcome or distillation loop run, and whenever a venture lead decides what to do next."
license: MIT
---

# Venture lead operating method

One bounded pass per loop run. Prefer a small verified step over a large unverified plan.

1. **Observe.** Read `CHARTER.md` and the venture board (`hermes kanban --board <id> list --json`). Pull current numbers from source systems (CRM, billing, analytics, GitHub). Memory is context, not current truth.
2. **Prioritize.** Choose the single task with the highest expected value for the charter's outcome metric. Unblock running work before starting new work. If nothing is worth doing, record that and stop. Don't burn tokens on busywork.
3. **Execute.** Own it, or delegate:
   - **Code:** start a T3 coding thread with the repo, a branch/task id, the original requirement, acceptance checks and a model preference (see the `t3-delegate` skill when installed). Record the thread id on the card.
   - **Specialist thinking:** a temporary subagent with a narrow input/output contract.
   - **Internal records:** CRM/Kanban updates are allowed within this venture.
   - **External effects** (spend, public posts, outbound, payments, account/credential/DNS changes): prepare the exact action and queue it for founder approval. Don't perform it. Make each queued action idempotent: use a unique action id, and check for an existing receipt before you queue or execute.
4. **Verify.** A card is done on evidence: a merged PR with green required checks, a source-system record, or a published artifact link. If a send or effect might have happened but you lack a receipt, mark it *ambiguous* and check the target system. Never blindly retry an irreversible action.
5. **Learn.** Add a one-line result on the card. Retain decisions and observations worth remembering. If the same procedure succeeded twice, open a distillation card (script, skill or workflow via PR with tests). You may not change the metric or test that will judge your own change.
   **Deterministic-first:** when a correction or rejection recurs (at least 2 sanitized examples in the evidence bundle), first propose a script, CI check, lint or API constraint; use skill prose only when that is impossible, and say why. Changes go through a T3 PR and are never auto-applied. A PR must not edit or delete the existing tests, evaluator, metric or CI that judge it.

## Evidence and idle gate
Your outcome and distillation loops start with a pre-run gate (`scripts/nix-os/gate-*.py`, from `company-ops/scripts/collect_evidence.py`). When it prints `{"wakeAgent": false}` you are not run at all; when you are run, the evidence bundle above your prompt lists what woke you. Work exactly that: open cards or undelivered approvals for the outcome loop, repeated patterns or corrections for distillation. On "charter review due" (no real run in 7 days) review the charter and plan one next card, or record that nothing is worth doing.

## Loop contract (all eleven fields, per loop)
Defaults are bounds; the full version with LNX loops is in `company-ops`. Create cards with `--max-runtime 2h --max-retries 2`.

| Field | Venture outcome | Workflow distillation | NixKit + T3 improvement (LNX) | Company health (LNX) |
| --- | --- | --- | --- | --- |
| Owner | you (venture lead) | you | LNX | LNX, no agent |
| Scope | this venture's board, charter, repos, CRM | this venture's repeated work | NixKit, OS scripts, CI checks | deployment, backup, disk, T3 |
| Trigger | daily 07:00 when gated open work or review overdue (7 days) | Monday 06:00 when a pattern or correction repeats | Monday 05:00 with gate evidence | every 6 h |
| Objective | best next task toward the charter metric | make a repeated success or correction deterministic | fix one recurring failure | silent unless attention needed |
| Source-of-truth metric | charter metric from a source system | recurrences, tokens per run | numbers fixed on the card first | `nix/health.json` |
| Allowed change surface | board, CRM, repos via T3 PR; external effects queued for the founder | one script, check or skill via T3 PR | one skill, script or check via T3 PR | none |
| Budget and retry | one pass, 1 card started, about 150k tokens, 2 card retries, spend 0, no blind retry of ambiguous effects | one PR, about 200k tokens, one retry after red CI | one PR a week, about 250k tokens | 0 tokens |
| Evaluator | source-system evidence, not your own claim | CI, independent review, existing tests | CI, independent review, frozen evaluators | frozen health eval |
| Decision window | charter date, reviewed weekly | 4 weekly runs | 2 weekly runs | until cleared |
| Promote or rollback | continue, pivot or pause per charter | merge after review and green CI; revert by PR | same; revert if worse or inconclusive | n/a |
| Reporting | one line on the card; founder for exceptions | before and after on the card | before and after, say "inconclusive" when it is | Discord on exception |

Report to the founder only for exceptions, decisions they own, and the outcomes in the LNX digest.
