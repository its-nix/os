# Emergency disable — stop agent activity fast

Operator runbook. Each layer is reversible and keeps native state. Work top to bottom (least to most disruptive) and stop as soon as the situation is contained. All container state is bind-mounted under `/srv/nix-clean` on the VPS; nothing in this runbook deletes it.

SSH access is the `nixos` operator alias used by the other guides. GitHub steps use the standard `gh` CLI or the web UI.

## At a glance

| # | Layer | Stops | Reversible by |
| --- | --- | --- | --- |
| 1 | [Hold PR auto-merge](#1-hold-pr-auto-merge) | merges to `main`, so CI deploys | remove label / mark ready |
| 2 | [Stop the approval worker timer](#2-stop-the-founder-approval-worker-timer) | new Todoist approval tasks | start the timer |
| 3 | [Pause each venture](#3-pause-each-venture) | venture lead cron loops | `venture.py resume` |
| 4 | [Pause remaining Hermes cron](#4-pause-remaining-hermes-cron-jobs) | LNX/ops loops per profile | `cron resume` |
| 5 | [Disable Twenty CRM](#5-disable-twenty-crm) | CRM intake and worker | restore marker, deploy |
| 6 | [Stop containers](#6-stop-containers) | everything, including the gateway | `docker start`, re-enable timers |
| 7 | [Revoke credentials](#7-revoke-credentials-at-the-provider) | the credentials themselves | re-issue each login |

## Hard rules

- Stop containers with `docker stop` only. State survives a stop.
- **Never** `docker compose down -v`. **Never** `docker system prune` or any image/volume prune. Images are built locally and pinned; the 2026-11-25 capture failed because the host auto-pruner removed the `nixos-*` containers ([REFACTOR](../REFACTOR.md)).
- Never delete or regenerate a state directory under `/srv/nix-clean` to "reset" anything.
- Credential revocation (layer 7) is the last resort: it breaks recovery until each login is re-issued.

## 1. Hold PR auto-merge

Holding takes effect on the reviewer's next poll (about a minute); an already-armed PR whose checks finish inside that window can still merge. **Converting to a draft is the guarantee**: GitHub never merges drafts.

```bash
gh pr list --state open --base main          # every PR to hold
gh pr edit <number> --add-label hold         # per PR
gh pr ready <number> --undo                  # per PR, when the guarantee matters
```

- Scope (`deploy/reviewer/reviewer.mjs`): every eligible PR — same repository, base `main`, not a draft. `HOLD_LABEL` is `hold`.
- This also stops main→VPS deploys: CI deploys only on merges to `main`.
- **Verify**: each open PR shows the `hold` label (and draft state where applied).
- **Resume**: `gh pr edit <number> --remove-label hold` and `gh pr ready <number>`. A PR that was already green gets a fresh independent review before auto-merge re-arms; the ruleset gate is never skipped ([AUTHORITY](../AUTHORITY.md)).

## 2. Stop the founder approval worker timer

The host timer polls the Hermes approval spool and creates Todoist queue tasks. Stopping it freezes the queue; already-spooled requests stay pending, and completing a task never executes anything by itself ([FOUNDER-APPROVAL-QUEUE](../FOUNDER-APPROVAL-QUEUE.md)).

```bash
sudo systemctl stop nixos-approval.timer              # pause now
sudo systemctl disable --now nixos-approval.timer      # also keep it off across reboot
```

- **Verify**: `systemctl is-active nixos-approval.timer` prints `inactive`.
- **Resume**: `sudo systemctl enable --now nixos-approval.timer` (or `start` if still enabled). The worker reconciles existing requests by action id, so nothing is duplicated.

## 3. Pause each venture

The Venture Factory pauses the venture's cron loops (daily outcome, weekly distillation) and records `paused` in the registry. Nothing is deleted; the lead profile, board and documents stay.

```bash
docker exec -u 10000 nixos-hermes python3 \
  /opt/data/skills/nix-os/venture-factory/scripts/venture.py status
docker exec -u 10000 nixos-hermes python3 \
  /opt/data/skills/nix-os/venture-factory/scripts/venture.py pause --id <id> --reason "emergency stop"
```

- **Verify**: `venture.py status` lists every venture as `paused`; `docker exec -u 10000 nixos-hermes hermes -p <id> cron list` shows the lead's loops paused.
- **Resume**: `venture.py resume --id <id>`. It refuses anything that is not paused, so re-running it is safe.

## 4. Pause remaining Hermes cron jobs

Venture loops are gone after step 3. What remains is per-profile cron, mostly the LNX company loops on `default` (`nix-company-health`, `nix-founder-digest`, `nix-improvement`). List each profile, pause what is listed:

```bash
docker exec -u 10000 nixos-hermes hermes -p <profile> cron list
docker exec -u 10000 nixos-hermes hermes -p <profile> cron pause <id>
```

- `<profile>`: `default` plus every venture lead and any other profile that lists jobs. Job ids are the 12-hex values printed by `cron list`.
- Pausing cron does not stop the gateway or the LNX Discord bot; mentions can still reach a profile until layer 6.
- **Verify**: re-run `cron list` per profile; every job shows as paused.
- **Resume**: `docker exec -u 10000 nixos-hermes hermes -p <profile> cron resume <id>`.

## 5. Disable Twenty CRM

Skip if `/srv/nix-clean/twenty/ENABLED` does not exist — the CRM is off. To disable a running CRM: stop the apps before their datastores, then move the marker aside.

```bash
docker stop --time 90 nixos-twenty-worker nixos-twenty-server
docker stop --time 90 nixos-twenty-db nixos-twenty-redis
sudo mv /srv/nix-clean/twenty/ENABLED /srv/nix-clean/twenty/ENABLED.off
```

- The deploy does the same thing from the marker alone: with `ENABLED` absent it stops (never removes) any running Twenty container (`crm_enabled` in `scripts/deploy-os.py`).
- Keep the marker move **temporary**: the nightly capture refuses while `twenty/` sits in the state root without its flag. Either restore the marker before the next 03:17 UTC run, or stop the recovery timer (layer 6). To retire the CRM for good instead, move the whole `twenty/` directory out of the root together with the flag ([DOCKERHUB-RECOVERY](../DOCKERHUB-RECOVERY.md)).
- **Verify**: `docker ps --filter name=nixos-twenty-` lists nothing.
- **Resume**: `sudo mv /srv/nix-clean/twenty/ENABLED.off /srv/nix-clean/twenty/ENABLED`, then deploy the current main revision (`sudo -n /usr/local/sbin/nix-os-deploy <main sha>`; the daily reconcile or the next merge does the same). A manual start works too, databases first: `docker start nixos-twenty-db nixos-twenty-redis`, wait healthy, then `nixos-twenty-server`, then `nixos-twenty-worker`.

## 6. Stop containers

First stop the two things that would restart the stack on you:

```bash
sudo systemctl stop nixos-recovery.timer       # the 03:17 UTC capture cold-stops and restarts everything
```

- In GitHub, disable the `ci` workflow's daily 05:17 UTC deploy run (Actions → disable workflow). Every main deploy runs `compose up` and restarts stopped containers; merges are already held by layer 1, but the scheduled reconcile is not.
- Then stop the runtimes. Apps before datastores; the Twenty containers were handled in layer 5.

```bash
docker stop --time 90 nixos-t3 nixos-reviewer
docker stop --time 90 nixos-hermes nixos-hindsight
```

- `--time 90` matches the deploy's own stop grace; Hermes and T3 shut down gracefully inside it. A forced kill after the timeout still loses no state.
- **Verify**: `docker ps --filter name=nixos-` lists nothing running.
- **Resume**: `docker start nixos-hindsight nixos-hermes nixos-t3 nixos-reviewer`, then the stopped Twenty containers if the CRM was enabled. `sudo systemctl enable --now nixos-recovery.timer`, and re-enable the GitHub workflow. The reviewed alternative to all of it is one deploy of current main, which starts and health-checks the whole stack.
- Stopped containers and everything on disk survive. Unpushed commits in T3 worktrees are only at risk on host loss, not on a stop.

## 7. Revoke credentials at the provider

Last resort: this breaks the stack until each credential is re-issued. Do it when a credential itself is the incident (leak, compromise). Rotate, then use [PROVIDER-AUTH-RECOVERY](PROVIDER-AUTH-RECOVERY.md) to restore logins.

| Credential | Where it lives | Revoke at the provider | Resume |
| --- | --- | --- | --- |
| Reviewer GitHub App key | `/srv/nix-clean/reviewer/secrets/app.pem` | GitHub App settings: regenerate the private key (or suspend/uninstall the App). `independent-review` then fails closed, so nothing merges | install the new key per [deploy/reviewer](../../deploy/reviewer/README.md) and recreate the reviewer container |
| Todoist API token (founder account) | `/srv/nix-clean/todoist/token` (root-only) | revoke the token in Todoist settings; also remove the file on the VPS | provision a fresh token via operator-controlled transfer and run the worker check ([FOUNDER-APPROVAL-QUEUE](../FOUNDER-APPROVAL-QUEUE.md)) |
| Twenty API key (per venture lead, `TWENTY_API_KEY`) | lead profile environment | delete the workspace API key in Twenty's settings | issue a new least-privilege key and configure it for the lead |
| Google Drive vault grant | `/srv/nix-clean/drive/` (root-only, rclone token) | revoke the rclone grant in the founder's Google account; stop `nix-drive.timer` | repeat the SSH sign-in ([KNOWLEDGE-VAULT](../KNOWLEDGE-VAULT.md)) |
| Harness logins (Claude Code, Codex, OpenCode, Antigravity, Grok) | `t3/home` and `reviewer/home` | sign out / revoke the device at each provider; a dead login drops that provider from the reviewer rotation | [PROVIDER-AUTH-RECOVERY](PROVIDER-AUTH-RECOVERY.md) |

The Hermes→T3 token is minted by the deploy and expires in 30 days on its own; it needs no emergency revocation.

## What each layer leaves behind

Every layer above is a pause. The venture registry, cron stores, Kanban boards, CRM database, approval spool and all logins stay under `/srv/nix-clean` and keep being captured by the recovery image once its timer runs again. Resume in any order; each layer's own resume command is enough.
