---
name: venture-lead
description: "Use on every venture outcome or distillation loop run, and whenever a venture lead decides what to do next."
license: MIT
---

# Venture lead operating method

Read the venture manifest before each pass. Run only in incubating, operating, scaling or pivoting.

1. **Observe.** Read `CHARTER.md` and the venture board (`hermes kanban --board <id> list --json`). Measure the charter's 1–3 outcome metrics from their named sources. Review the previous batch's evidence and blockers.
2. **Rank.** Start with the card marked `## Current constraint`. Rank the other small, evidence-producing cards by expected impact on the metrics. Put prerequisites before dependents. Use native Kanban links for dependency ordering and create backlog cards unassigned.
3. **Release.** Pass ranked existing IDs to `python3 /opt/data/skills/nix-os/venture-factory/scripts/venture.py release --id <id> TASK_ID [TASK_ID ...]`. The manifest budget defaults to three cards per calendar day (venture timezone or UTC), five minutes per attempt, two consecutive failures, 30 reserved work minutes and zero spend. Kanban drains released cards with one worker per lead. Keep other cards unassigned and leave failed cards parked for review.
4. **Execute and verify on the card.** Produce a linked artifact, a source-system record or a merged PR with green required checks. For code, use the T3 skill with explicit acceptance checks and record the thread ID. Wait for existing delegated work or report the blocker when the attempt budget runs out. Review ambiguous effects in the target system before any retry. Prepare founder-gated actions for approval with an exact preview and unique action ID.
5. **Learn.** Record the evidence, outcome and blockers on the card. The weekly distillation loop opens an unassigned card for procedures that succeeded at least twice. Keep the evaluation criteria fixed.

Report founder decisions, exceptions and verified outcomes through the LNX digest.
