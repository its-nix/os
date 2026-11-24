# Authority and change governance

Root policy, owned by the founder. Changes to this file, `.github/` (CI, reviewer, CODEOWNERS), the repository verifier, the root-executed deploy implementation and its inputs (`scripts/deploy-os.{sh,py}`, `deploy/compose.yaml`, `deploy/t3/hermes_token.py`), recovery code, or the gate's own tests need founder review through CODEOWNERS. Agents may propose such changes but cannot merge them.

## Durable changes (code, skills, workflows, deployment config)

Path: branch → PR with the original acceptance criteria → required checks → auto-merge → deploy → monitor/revert.

The `main` ruleset requires:
1. A pull request. No direct pushes and no force-pushes.
2. Required check runs:
   - `verify` **from the GitHub Actions app**: unit tests, Compose contract, repository verifier, reviewer tests.
   - `independent-review` **from the Nix reviewer GitHub App**. It is produced by `deploy/reviewer/reviewer.mjs` on the VPS, in its own container with its own subscription logins, separate from T3 where builders run. For each PR head SHA it picks one reviewer, pseudo-randomly from the eligible pool (Claude, Codex, OpenCode, Grok, Antigravity/Gemini) and seeded by the SHA so reruns are stable. It excludes the builder, named by the PR's `Builder:` line or co-author trailers. Unavailable providers fall through to the next; if none answers, the check fails. The PR head is never checked out or executed; the diff comes from the API and goes to a reviewer CLI with tools disabled.
   Builder tokens can't create check runs as that App, so they can't forge the check.
3. Code-owner review for the root-policy paths above (including `deploy/reviewer/`), with stale approvals dismissed and an empty bypass list. Checks must be up to date with `main`.

The reviewer reviews exactly the PR's base...head range, re-checks that the head did not move before publishing, and fails on drafts, forks, non-`main` bases, and incomplete, binary or oversized diffs. File modes are not visible in the reviewed diff, so mode-only effects rely on CI. The description is advisory and the diff is authoritative. The model verdict judges code quality; enforcement comes from the ruleset and token permissions, not from the model.

Builders (T3 coding threads, venture leads) use a GitHub identity **without** admin, ruleset bypass or workflow-file permission. A red check blocks the merge. A builder may not edit the tests or metric that judge its own change; PRs that touch evaluation files fall under CODEOWNERS.

Ordinary business events (CRM updates, analytics reads, approved procedure runs) are not PRs.

## Founder-gated external actions

Agents prepare these and never perform them:
- spending, paid campaigns, payments, pricing or contract commitments
- public posts and campaigns, bulk or unsolicited outbound
- DNS, credentials, consequential account or permission changes
- changes to these rules, GitHub rulesets or reviewer/check enforcement

**Queue protocol (Todoist, project `Nix`, label `nix-approval`).** One task per action. The title is `[approval] <venture>: <action>`. The description carries:
- action id (unique; reused on retries)
- venture
- target system
- the exact effect and preview
- estimated cost
- expiry
- executor

The founder executes the action, or approves a narrowly bounded method, and the receipt is recorded on the originating Kanban card. A completed Todoist task is **not** an authenticated approval and never triggers an effect by itself. A missing reply is not approval; an expired request is closed. Agents check for an existing receipt (by action id) before queueing or executing anything, and never blindly retry an ambiguous irreversible action.

Credentials that could perform these effects are not given to Hermes or T3 runtimes. A prompt rule does not enforce anything that the runtime credential allows.
