# Rotating independent reviewer

`reviewer.mjs` refreshes the GitHub App installation's repository list each poll. It publishes the
`independent-review` check for open, non-draft PRs whose head is in the same repository and whose base
is that repository's default branch. New check runs are bound to the repository, PR and base.
Existing decisions in `its-nix/os` with a matching policy and base remain valid.

- **Selection.** One reviewer per head SHA, chosen pseudo-randomly from the eligible subscription pool, keyed by the head SHA and a secret seed, so reruns keep the same reviewer and a builder cannot predict it. The PR's builders are excluded: `Builder: <provider>` lines, which `skills/t3-delegate` requires, or co-author trailers. Several builders are all excluded while at least two reviewers remain; if naming them would leave fewer, nobody is excluded, so a builder cannot pick its reviewer. An unavailable provider falls through to the next. If none answers, the check fails.
- **Re-review.** A new head carries forward blocking findings from the App's earlier checks on the same PR. The next reviewer must explain how each finding was resolved or disproved before approving. The newest blocking verdict is always carried in full (the check fails closed if it alone exceeds 8,000 characters). Reviewers must restate every prior finding they did not see resolved, so older verdicts are added only while they fit. A failed review also pauses automatic branch updates.
- **Isolation.** It runs in `nixos-reviewer`: the pinned T3 image, but its own container, network (`nixos-review`) and login home (`/srv/nix-clean/reviewer/home`). Builders in `nixos-t3` can't read its logins or its App key. No ports.
- **Untrusted input.** It never checks out PR code. The diff comes from the compare API and goes to the CLI in an empty directory, with tools disabled or plan-only.
- **Key isolation.** The orchestrator alone (root in the container, only SETUID/SETGID/CHOWN/KILL) reads the App key and the secret selection seed (`secrets/`, root 0400). Every reviewer CLI runs as uid 10010 and cannot read them. Published summaries are also redacted for anything token-shaped.
- **Protected paths.** `its-nix/os` uses its deployed [CODEOWNERS](../../.github/CODEOWNERS). In other repos, the reviewer reads the first available `.github/CODEOWNERS`, `CODEOWNERS` or `docs/CODEOWNERS` from the default-branch commit being reviewed. If no file exists, there are no protected paths. Matching paths (including both names of a rename) require a founder approval file at `/run/reviewer-approvals/<sha>` in the container. The exact commit SHA is the shared key across repositories. The reviewer waits for the file before invoking a model. See the [approval command](../../docs/AUTHORITY.md#durable-changes-code-skills-workflows-deployment-config).
- **Repository review policy.** An optional `.github/nix-review.md` adds repository-specific criteria to the prompt. The reviewer reads and escapes this trusted text from the exact default-branch commit. The core review and tool restrictions still apply. Files over 8 KiB are ignored.
- **Known limits.** Reviewer CLIs can still read their own login home (Codex read-only, Antigravity plan mode) but never the App key or seed; published text is redacted. Don't configure MCP servers in the reviewer home. PRs above about 100 KB of diff are refused for review and must be split. The builder is self-declared: a PR declaring several builders excludes none, and a false declaration lets the builder's own provider review in a fresh, separate session. Model rejections need a new commit.
- **Check identity.** It publishes as a dedicated GitHub App. Public repository rulesets can require the check from that App's id, which builder tokens cannot impersonate.

## One-time setup (founder)

1. Create the `nix-reviewer` GitHub App under `its-nix` and disable its webhook. Grant **Checks**, **Pull requests** and **Contents** read and write access, plus **Metadata** and **Commit statuses** read access. Generate a private key. For an existing installation, approve any new permissions.
2. **Key and id** on the VPS as root:
   ```bash
   install -m 0400 -o root -g root ~/nix-reviewer.private-key.pem /srv/nix-clean/reviewer/secrets/app.pem
   sed -i 's/^# REVIEW_APP_ID=.*/REVIEW_APP_ID=<app id>/' /srv/nix-clean/reviewer/reviewer.env
   # recreate (not restart) so the edited env file is read:
   docker compose -p nixos --env-file /srv/nix-clean/deployment.env -f /opt/nix-clean-source/deploy/compose.yaml up -d reviewer
   ```
3. **Subscription logins inside the reviewer**. These are separate from T3's, so a builder can never reuse a reviewer session.
   ```bash
   docker exec -it -u t3 nixos-reviewer claude                      # /login (Claude Pro)
   docker exec -it -u t3 nixos-reviewer codex login --device-auth   # ChatGPT Plus
   docker exec -it -u t3 nixos-reviewer opencode auth login         # provider "OpenCode", paste the OpenCode Go key
   docker exec -it -u t3 nixos-reviewer grok                        # SuperGrok sign-in
   docker exec -it -u t3 nixos-reviewer agy                         # Google AI Pro: open the printed URL, paste the code back
   ```
   A provider without a working login simply drops out of rotation. OpenCode reviews always go to the OpenCode Go endpoint (`https://opencode.ai/zen/go/v1`), never pay-as-you-go; the default model is `opencode/kimi-k3.1-pro`. To change it, set `OPENCODE_REVIEW_MODEL` in `reviewer.env` to another id from the Go model list.
4. Configure a ruleset on each public repository's default branch with an empty bypass list.
   - require a PR
   - block force pushes and deletion
   - require status checks `verify` (GitHub Actions) and `independent-review` (the App), up to date
   - leave CODEOWNERS approval and required approving reviews disabled

   Then enable repository auto-merge.
5. After this change merges, install the App on all `its-nix` repos. In GitHub, go to **Settings → GitHub Apps → nix-reviewer → Configure** and set **Repository access** to **All repositories**.

## Operate

`docker logs nixos-reviewer` shows one line per failure (never PR contents). Each check run's summary names the reviewer, the excluded builder and any providers skipped. To take a provider out of rotation, edit `REVIEW_POOL` in `reviewer.env` and recreate the container with the same `docker compose … up -d reviewer` command as in step 2 (a plain restart keeps the old environment).

### Usage-limit cooldowns

When a provider CLI reports a usage limit, the reviewer skips that provider across PRs until its reported reset. A missing or unreadable reset time defaults to one hour. Cooldowns range from one minute to seven days. The reviewer keeps the existing seeded order and builder exclusions, and check summaries list limit skips with their UTC reset times.

The deploy creates a private `reviewer/state` directory and mounts it at `/var/lib/reviewer`. The reviewer writes `cooldowns.json` there atomically, so cooldowns survive restarts and container recreation. `REVIEW_STATE_DIR` can point to another private, writable directory.

If every eligible provider is cooling, `No reviewer available` is retried on the first poll at or after the earliest reset, with at least one minute between attempts. Other `No reviewer available` checks keep the 30-minute retry interval.

## Merge behavior

- **Public repositories** (`os`, `algua`, `meabeclick`, `dsio`). The App arms GitHub squash auto-merge on eligible PRs without a `hold` label. The ruleset requires `verify` and the App's `independent-review` on an up-to-date head. The App updates an armed branch that falls behind if its review passed or is still pending. A draft stays open. A `hold` label disarms auto-merge on the next poll, so a PR whose checks finish before that poll can still merge.
- **Private repositories** (`kit`, `nixvert`, `knowledgebase`, `gaitid`, `pulse`, `token-optimizer`, `stt`). GitHub Free does not enforce the reviewer check on private repos. This is a soft gate for the App's own merges. The founder or a token with sufficient permissions can still merge manually. The App disarms GitHub auto-merge it previously enabled and squash-merges directly only after its own check passes for the exact PR head and current base, any required founder approval is present, and CI is green. At least one non-reviewer app check run or bot commit status must exist on the head. Every app check run must be completed with `success`, `neutral` or `skipped`, and the latest commit status from each non-reviewer bot and context must be `success`. If a rerun leaves a failed check run on the head, the PR needs a new head. The App re-reads the live PR immediately before merging and requires unchanged head and base, an open and mergeable PR, no draft and no `hold` label. GitHub's merge endpoint pins the head SHA. A base change after that final read can still race the request.
- **No CI.** Private PRs without CI (currently typical in `gaitid` and `stt`) are review-only by default. After a passing review, the App comments once on the PR that CI is required for automatic merging. Set `REVIEW_ONLY_REPOS` in `reviewer.env` to a comma-separated list of full repository names to keep those repos review-only even when CI is present. Recreate the container after an env change.
