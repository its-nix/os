# Authority and change governance

Root policy, owned by the founder. [CODEOWNERS](../.github/CODEOWNERS) lists the protected paths, including workflows, tests, scripts, the reviewer, and root-executed deployment and recovery code.

## Durable changes (code, skills, workflows, deployment config)

Path: branch → PR with the original acceptance criteria → required checks → auto-merge → deploy → monitor/revert.

The active `main-protection` ruleset requires a PR, blocks force-pushes and deletion, and has an empty bypass list. It requires `verify` from GitHub Actions and `independent-review` from the Nix reviewer GitHub App, with the PR branch up to date with `main`. Repository auto-merge is enabled, and the reviewer App turns on squash auto-merge for every eligible PR (same-repo, base `main`, not draft) and updates auto-merge PRs that fall behind `main` (a PR that was already green before it could be armed, e.g. after a removed `hold` label, gets a fresh independent review requested and is armed while that review is pending; the App never merges directly or copies an old verdict). It disarms only auto-merge it armed itself when a PR leaves `main`; each update gets its own CI run and review. To keep a PR open for certain, convert it to a draft (GitHub never merges drafts). A `hold` label also turns auto-merge off, but only on the reviewer's next poll (about a minute). The ruleset requires zero approving reviews and does not require CODEOWNERS approval. Agents open PRs with the founder's GitHub identity, which cannot approve its own PRs. A GitHub approval, label, or comment provides no founder authorization to the App.

`verify` runs the PR's own suite on GitHub-hosted runners with read-only contents access, no persisted checkout credentials and no deployment secrets. Running the base suite as well would block legitimate behaviour changes that need corresponding test updates. The check starts Python in isolated mode and loads `unittest` before appending the repository to the import path for application imports. Changes under `tests/`, `scripts/`, `.github/` or `deploy/reviewer/`, and to the root files `conftest.py`, `pyproject.toml`, `unittest.py`, `sitecustomize.py` and `usercustomize.py`, require founder approval. This includes added files.

`independent-review` runs from deployed main on the VPS. It uses the deployed CODEOWNERS list to inspect changed paths in the exact base...head comparison, including both names of a rename. If any path is protected, the App publishes a failure until it sees a root-owned approval file for that exact head SHA. Normal deployment creates the directory and mounts it read-only in the reviewer container. Neither T3 nor Hermes mounts it. The gate depends on the ruleset, App signing key and VPS root access remaining under trusted control.

After reviewing the exact diff, the founder or a founder-authorized root operator runs this command on the VPS, substituting the full head SHA:

```bash
sha='<full head SHA>'; [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && install -o root -g root -m 0400 /dev/null "/srv/nix-clean/reviewer/approvals/$sha"
```

**Founder-accepted risk.** When the founder has deliberately accepted a policy risk for a change (for example a credential exposure traded for speed), the approval file for that exact head can carry it as one or more `accepted-risk: <decision id and what is accepted>` lines:

```bash
sha='<full head SHA>'; [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && printf 'accepted-risk: %s\n' '<D-number: risk the founder accepted>' | install -o root -g root -m 0400 /dev/stdin "/srv/nix-clean/reviewer/approvals/$sha"
```

The reviewer is told the accepted risk, marked as trusted founder input separate from the untrusted PR text. It must not block solely because the change implements that risk, and it still blocks implementation defects and any risk beyond what is stated. The check result prints the accepted risk. Use it only for a decision the founder actually took and recorded in docs/REFACTOR.md.

The reviewer retries on its next poll after approval. If approval is removed while the PR is open, the next poll replaces any passing protected verdict with a failure. A new commit needs its own approval. The file authorizes protected-path changes and the model review must still pass. The App excludes the declared builder, chooses a reviewer per head SHA and rechecks both the head and base before publishing the model verdict. It considers only the latest completed check for that head and reviews again if the policy version or base has changed. Legacy checks without a policy and base binding are reviewed again. A missing reviewer triggers a 30-minute retry for the same policy and base. The App leaves drafts, forks and non-main PRs without an approving check, fails incomplete, binary or oversized diffs, reads the diff through the GitHub API and runs the review CLI with tools disabled.

Ordinary business events (CRM updates, analytics reads, approved procedure runs) are not PRs.

## Founder-gated external actions

Agents prepare these and never perform them:
- spending, paid campaigns, payments, pricing or contract commitments
- public posts and campaigns, bulk or unsolicited outbound
- DNS, credentials, consequential account or permission changes
- changes to these rules, GitHub rulesets or reviewer/check enforcement

**Queue protocol (Todoist, the founder's private personal project `Nix approvals`, label `nix-approval`).** The shared `Nix` workspace project is not used: it is visible to other workspace members and requests carry exact previews. One task per action. The title is `[approval] <venture>: <action>`. The description carries:
- action id (unique; reused on retries)
- venture
- target system
- the exact effect and preview
- estimated cost
- expiry
- executor

The founder executes the action, or approves a narrowly bounded method, and the receipt is recorded on the originating Kanban card. A completed Todoist task is **not** an authenticated approval and never triggers an effect by itself. A missing reply is not approval; an expired request is closed. Agents check for an existing receipt (by action id) before queueing or executing anything, and never blindly retry an ambiguous irreversible action.

The founder's own Todoist account holds the queue. Hermes submits a bounded request to a local spool and reads a status file for that request. The VPS host worker holds the account token outside container mounts and confines Todoist reads and writes to the configured private `Nix approvals` project. Expiry is the end of the stated UTC date. Preview and effect are rendered as literal text in the description. See the [setup, recovery and acceptance H runbook](FOUNDER-APPROVAL-QUEUE.md).

Credentials that could perform these effects are not given to Hermes or T3 runtimes. A prompt rule does not enforce anything that the runtime credential allows.
