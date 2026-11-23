# Authority and change governance

Root policy, owned by the founder. Changes to this file, `.github/` (CI, reviewer, CODEOWNERS) or the deploy launcher need founder review through CODEOWNERS. Agents may propose such changes but cannot merge them.

## Durable changes (code, skills, workflows, deployment config)

Path: branch → PR with the original acceptance criteria → required checks → auto-merge → deploy → monitor/revert.

The `main` ruleset requires:
1. A pull request. No direct pushes and no force-pushes.
2. Required check runs **from the GitHub Actions app**:
   - `verify`: unit tests, Compose contract, repository verifier.
   - `independent-review`: a different model reviews the diff against the description. It runs from `main`'s definition (`pull_request_target`), never executes PR code, refuses forks and fails closed when unconfigured.
   Any write token can post a commit *status*, but only the Actions app produces these *check runs*, so a builder cannot forge them.
3. Code-owner review for the root-policy paths above.

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
