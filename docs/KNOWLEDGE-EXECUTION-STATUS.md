# Knowledge execution continuation — governance published, live inactive

This status supplements, and does not amend, the immutable `0ba0131f0da473d12bcb1fc3cadd8258bd66e15e` proposal and historical authorization records.

## Authority boundary

- `scripts/knowledge_execution.py` pins the exact contextual voice decision verified out of band by the parent. See [the decision record](KNOWLEDGE-ACTIVATION-DECISION.md) for actual voice/bot provenance and separate written release authorization. No canonical human-written approval quote is invented.
- The exact v2 approval receipt, single-skill approval receipt and ancestor-derived five-tree preservation manifest are published. These represent approved governance, **not an already installed skill or a completed live transaction**. Existing historical pack/text-approval rules remain unchanged.
- Production still requires clean committed governance, exact ancestor/source/Kit bindings, all twelve destination closures and five preserved trees, canonical receipts, native Skills Guard, and fresh live preflight. No bypass flag or general voice protocol is added.
- Production has no alternate receipt/home/test-authority CLI. The separate opt-in test harness substitutes synthetic authority only in memory and writes solely to disposable scratch homes and Kit copies. Negative unpublished/fake-evidence fixtures remain explicit.

## External gates and remaining work

The parent supplied verified merge state for OS #45 and #46; this branch integrates their main base without changing immutable proposal/source bytes. The parent reports green hosted source/proposal and pinned Kit checks, the authorized temporary Kit public CI window completed and private visibility restored. Kit #6 and knowledgebase #1 remain open/private in the supplied context. This preparation does not independently query or change those remote states.

The inactive execution code at `165885b61f11a099a0809007f9f8b72effd21a97` passed independent specification and quality review. This publication delta still requires narrow independent parent review and exact-head CI before parent-owned release. Current read-only production preview may refuse on host drift; such drift must be reported, not repaired here. No live apply, cleanup or rollback is performed by this preparation. Actual parent rollout, fresh post-install Discord acceptance and representative retrieval/render verification remain outstanding.

## Verification commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 scripts/verify.py
bash -n scripts/bootstrap.sh scripts/reconcile-hermes-default.sh
git diff --check
PYTHONDONTWRITEBYTECODE=1 python3 tests/exercise_knowledge_kit.py \
  --synthetic-test-mode --kit-snapshot "$HOME/.hermes/cache/scratch/activation-full-set-kit"
```

Receipt consumption and repository verification require a clean committed checkout. The real Kit harness requires an already acquired clean exact-pin snapshot, already cached exact Node runtime and native Skills Guard. It performs offline installation of 12 targets, preserves five synthetic native trees, verifies pure preview and repeat no-op, injects a nonzero Kit process result to test exact cache rollback, and recovers the disposable installation's cache archive. Archives survive subsequent transactions; no automatic archive cleanup is permitted. Synthetic results are not live-profile evidence.
