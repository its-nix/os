# Authority and change governance

Root policy, owned by the founder. [CODEOWNERS](../.github/CODEOWNERS) lists the protected paths, including workflows, existing tests (new test files are free), the repository verifier, the reviewer, and root-executed deployment and recovery code.

## Durable changes (code, skills, workflows, deployment config)

Path: branch → PR with the original acceptance criteria → required checks → auto-merge → deploy → monitor/revert.

The active `main-protection` ruleset requires a PR, blocks force-pushes and deletion, and has an empty bypass list. It requires `verify` from GitHub Actions and `independent-review` from the Nix reviewer GitHub App, with the PR branch up to date with `main`. Repository auto-merge is enabled. The ruleset requires zero approving reviews and does not require CODEOWNERS approval. Agents open PRs with the founder's GitHub identity, which cannot approve its own PRs. A GitHub approval, label, or comment provides no founder authorization to the App.

`verify` runs the PR's own suite on GitHub-hosted runners with read-only contents access, no persisted checkout credentials and no deployment secrets. It deliberately does not also run the base suite: a legitimate behaviour change that an existing test asserts would then fail forever with no override. Instead, weakening the evaluation is a protected-path change. Modifying, renaming or deleting an existing file under `tests/`, or touching `scripts/verify.py`, `.github/` (including the workflow itself) or the reviewer, requires founder approval below. Adding a new test file does not.

`independent-review` runs from deployed main on the VPS. It uses the deployed CODEOWNERS list to inspect changed paths in the exact base...head comparison, including both names of a rename. If any path is protected, the App publishes a failure until it sees a root-owned approval file for that exact head SHA. Normal deployment creates the directory and mounts it read-only in the reviewer container. Neither T3 nor Hermes mounts it. The gate depends on the ruleset, App signing key and VPS root access remaining under trusted control.

After reviewing the exact diff, the founder or a founder-authorized root operator runs this command on the VPS, substituting the full head SHA:

```bash
sha='<full head SHA>'; [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && install -o root -g root -m 0400 /dev/null "/srv/nix-clean/reviewer/approvals/$sha"
```

The reviewer retries on its next poll. A new commit needs its own approval. The file authorizes protected-path changes and the model review must still pass. The App excludes the declared builder, chooses a reviewer per head SHA, and rechecks the head before posting the verdict. It leaves drafts, forks and non-main PRs without an approving check, and fails incomplete, binary or oversized diffs. It reads the diff through the GitHub API and runs the review CLI with tools disabled.

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
