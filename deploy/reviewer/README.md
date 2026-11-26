# Rotating independent reviewer

`reviewer.mjs` produces the required `independent-review` check for every PR into `main`.

- **Selection.** One reviewer per head SHA, chosen pseudo-randomly from the eligible subscription pool, keyed by the head SHA and a secret seed, so reruns keep the same reviewer and a builder cannot predict it. The PR's builders are excluded: `Builder: <provider>` lines, which `skills/t3-delegate` requires, or co-author trailers. Several builders are all excluded while at least two reviewers remain; if naming them would leave fewer, nobody is excluded, so a builder cannot pick its reviewer. An unavailable provider falls through to the next. If none answers, the check fails.
- **Re-review.** A new head carries forward blocking findings from the App's earlier checks on the same PR. The next reviewer must explain how each finding was resolved or disproved before approving. Findings are carried newest first within 8,000 characters; older ones beyond that are omitted, since each later review already had to resolve them. A failed review also pauses automatic branch updates, so moving `main` cannot hand a blocked PR to a new reviewer.
- **Isolation.** It runs in `nixos-reviewer`: the pinned T3 image, but its own container, network (`nixos-review`) and login home (`/srv/nix-clean/reviewer/home`). Builders in `nixos-t3` can't read its logins or its App key. No ports.
- **Untrusted input.** It never checks out PR code. The diff comes from the compare API and goes to the CLI in an empty directory, with tools disabled or plan-only.
- **Key isolation.** The orchestrator alone (root in the container, only SETUID/SETGID/CHOWN/KILL) reads the App key and the secret selection seed (`secrets/`, root 0400). Every reviewer CLI runs as uid 10010 and cannot read them. Published summaries are also redacted for anything token-shaped.
- **Protected paths.** The deployed [CODEOWNERS](../../.github/CODEOWNERS) list requires a founder approval file on the VPS for the exact head SHA. The reviewer checks both names of a rename and fails before invoking a model if approval is missing. It retries when the file appears. See the [approval command](../../docs/AUTHORITY.md#durable-changes-code-skills-workflows-deployment-config).
- **Known limits.** Reviewer CLIs can still read their own login home (Codex read-only, Antigravity plan mode) but never the App key or seed; published text is redacted. Don't configure MCP servers in the reviewer home. PRs above about 100 KB of diff are refused for review and must be split. The builder is self-declared: a PR declaring several builders excludes none, and a false declaration lets the builder's own provider review in a fresh, separate session. A failure caused by no provider being available is retried after 30 minutes. Model rejections need a new commit.
- **Unforgeable check.** It publishes as a dedicated GitHub App. The `main` ruleset requires the check from that App's id; builder tokens can't create check runs as the App.

## One-time setup (founder)

1. **GitHub App** (Settings → Developer settings → GitHub Apps → New, owner `its-nix`): name it e.g. `nix-reviewer`. Disable the webhook. Permissions: **Checks: read & write**, **Pull requests: read**, **Contents: read**, Metadata: read. Install it on `its-nix/os` only, then generate a private key.
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
4. **Ruleset** on `main` (empty bypass list):
   - require a PR
   - block force pushes and deletion
   - require status checks `verify` (GitHub Actions) and `independent-review` (the App), up to date
   - leave CODEOWNERS approval and required approving reviews disabled

   Then enable repository auto-merge.

## Operate

`docker logs nixos-reviewer` shows one line per failure (never PR contents). Each check run's summary names the reviewer, the excluded builder and any providers skipped. To take a provider out of rotation, edit `REVIEW_POOL` in `reviewer.env` and recreate the container with the same `docker compose … up -d reviewer` command as in step 2 (a plain restart keeps the old environment).

## Always-merge

Each poll, the App enables squash auto-merge on every eligible PR (same repository, base `main`, not a draft, no `hold` label) and updates auto-merge PRs that are behind `main` if the head's review has passed or has yet to be decided. It needs the App's Contents and Pull requests permissions set to read and write. The ruleset remains the only gate: auto-merge waits for `verify` and `independent-review` on an up-to-date head. To guarantee a PR stays open, convert it to a draft: GitHub never merges drafts. The `hold` label turns auto-merge off on the next poll (about a minute), so an already-armed PR whose checks finish within that window can still merge.
