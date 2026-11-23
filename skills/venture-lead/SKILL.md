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

Report to the founder only for exceptions, decisions they own, and the outcomes in the LNX digest.
