# Rotating independent reviewer

`reviewer.mjs` produces the required `independent-review` check for every PR into `main`.

- **Selection.** One reviewer per head SHA, chosen pseudo-randomly from the eligible subscription pool and seeded by the SHA, so reruns keep the same reviewer. The PR's builder is excluded: a `Builder: <provider>` line, which `skills/t3-delegate` requires, or a co-author trailer. An unavailable provider falls through to the next. If none answers, the check fails.
- **Isolation.** It runs in `nixos-reviewer`: the pinned T3 image, but its own container, network (`nixos-review`) and login home (`/srv/nix-clean/reviewer/home`). Builders in `nixos-t3` can't read its logins or its App key. No ports.
- **Untrusted input.** It never checks out PR code. The diff comes from the compare API and goes to the CLI in an empty directory, with tools disabled or plan-only.
- **Unforgeable check.** It publishes as a dedicated GitHub App. The `main` ruleset requires the check from that App's id; builder tokens can't create check runs as the App.

## One-time setup (founder)

1. **GitHub App** (Settings → Developer settings → GitHub Apps → New, owner `its-nix`): name it e.g. `nix-reviewer`. Disable the webhook. Permissions: **Checks: read & write**, **Pull requests: read**, **Contents: read**, Metadata: read. Install it on `its-nix/os` only, then generate a private key.
2. **Key and id** on the VPS as root:
   ```bash
   install -m 0440 -o root -g 10010 ~/nix-reviewer.private-key.pem /srv/nix-clean/reviewer/secrets/app.pem
   sed -i 's/^# REVIEW_APP_ID=.*/REVIEW_APP_ID=<app id>/' /srv/nix-clean/reviewer/reviewer.env
   docker restart nixos-reviewer
   ```
3. **Subscription logins inside the reviewer**. These are separate from T3's, so a builder can never reuse a reviewer session.
   ```bash
   docker exec -it nixos-reviewer claude                      # /login (Claude Pro)
   docker exec -it nixos-reviewer codex login --device-auth   # ChatGPT Plus
   docker exec -it nixos-reviewer opencode auth login         # OpenCode Go key, Extra Usage off
   docker exec -it nixos-reviewer grok                        # SuperGrok sign-in
   docker exec -it nixos-reviewer gemini                      # Google AI Pro sign-in (if supported headless)
   ```
   A provider without a working login simply drops out of rotation. Set `OPENCODE_REVIEW_MODEL` in `reviewer.env` to pin the OpenCode model.
4. **Ruleset** on `main` (empty bypass list):
   - require a PR
   - block force pushes and deletion
   - require status checks `verify` (GitHub Actions) and `independent-review` (the App), up to date
   - require CODEOWNERS review with stale approvals dismissed

   Then enable repository auto-merge.

## Operate

`docker logs nixos-reviewer` shows one line per failure (never PR contents). Each check run's summary names the reviewer, the excluded builder and any providers skipped. To take a provider out of rotation, edit `REVIEW_POOL` in `reviewer.env` and restart the container.
