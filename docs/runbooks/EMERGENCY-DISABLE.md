# Emergency disable — stop agent activity fast

Operator runbook. Each layer is reversible and keeps native state. Work top to bottom (least to most disruptive) and stop as soon as the situation is contained. All container state is bind-mounted under `/srv/nix-clean` on the VPS; nothing in this runbook deletes it.

SSH access is the `nixos` operator alias used by the other guides. GitHub steps use the standard `gh` CLI or the web UI.

## At a glance

| # | Layer | Stops | Reversible by |
| --- | --- | --- | --- |
| 1 | [Hold PR auto-merge](#1-hold-pr-auto-merge) | merges of the held PRs (per-PR; a sustained freeze needs more) | remove label / mark ready |
| 2 | [Stop the approval worker timer](#2-stop-the-founder-approval-worker-timer) | new Todoist approval tasks | start the timer |
| 3 | [Pause each venture](#3-pause-each-venture) | venture lead cron loops | `venture.py resume` |
| 4 | [Pause remaining Hermes cron](#4-pause-remaining-hermes-cron-jobs) | LNX/ops loops per profile | `cron resume` |
| 5 | [Disable Twenty CRM](#5-disable-twenty-crm) | CRM intake and worker | restore marker, deploy |
| 6 | [Stop containers](#6-stop-containers) | everything, including the gateway | `docker start`, re-enable timers and the deploy gate |
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
- **Per-PR only.** This disarms exactly the PRs listed above: a PR opened or retargeted to `main` while the reviewer runs is armed on its next poll (about a minute), and a merge that slipped in just before the hold can still be deploying — the deploy pause is layer 6.
- For a sustained freeze, turn auto-merge off at the repository level and draft the already-armed PRs. This is the freeze; do not rely on a stopped reviewer instead, because a recovery capture (`ExecStopPost`) or any deploy starts `nixos-reviewer` again:
  - A repo settings change (Settings → General → Allow auto-merge): `gh api -X PATCH repos/its-nix/os -F allow_auto_merge=false` (restore with `-F allow_auto_merge=true`; `-F` sends a boolean, `-f` would send a string). Verify with `gh api repos/its-nix/os -q .allow_auto_merge`, which must print `false`.
  - It does not disarm PRs already armed; draft those (`gh pr ready <number> --undo`).
  - Stopping `nixos-reviewer` (`docker stop --time 90 nixos-reviewer`) is optional on top: it stops new reviews, but it is not a freeze on its own.
- A merge freeze never pauses the deploy pipeline itself: the scheduled 05:17 UTC reconcile deploys current `main` regardless of merges. Apply and verify the deploy pause in layer 6 independently.
- **Verify**: each open PR shows the `hold` label (and draft state where applied); `gh pr view <number> --json autoMergeRequest` prints `null` once a PR is disarmed.
- **Resume**: `gh pr edit <number> --remove-label hold` and `gh pr ready <number>`. A PR that was already green gets a fresh independent review before auto-merge re-arms; the ruleset gate is never skipped ([AUTHORITY](../AUTHORITY.md)).

## 2. Stop the founder approval worker timer

The host timer polls the Hermes approval spool and creates Todoist queue tasks. Stopping it freezes the queue; already-spooled requests stay pending, and completing a task never executes anything by itself ([FOUNDER-APPROVAL-QUEUE](../FOUNDER-APPROVAL-QUEUE.md)).

```bash
sudo systemctl stop nixos-approval.timer              # pause now
sudo systemctl disable --now nixos-approval.timer      # also keep it off across reboot
```

- Stopping the timer does not stop a worker run the timer already triggered. The service is a short oneshot (`TimeoutStartSec=75s`, `scripts/approval/nixos-approval.service`) that only reconciles the spool into Todoist tasks: a run in flight is safe to wait out, and `sudo systemctl stop nixos-approval.service` ends it — interrupted or not, the next run reconciles by action id and duplicates nothing.
- **Verify**: `systemctl is-active nixos-approval.timer nixos-approval.service` — both print `inactive`.
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
- **Never start the Twenty containers while `ENABLED` is absent.** The next deploy stops them again (`crm_enabled` in `scripts/deploy-os.py`), and the nightly capture refuses while CRM containers run with the CRM disabled (`stopped_services` in `automation/recovery/full_create.py`).
- **Resume**: restore the marker first — `sudo mv /srv/nix-clean/twenty/ENABLED.off /srv/nix-clean/twenty/ENABLED` — and verify it (`sudo test -f /srv/nix-clean/twenty/ENABLED && echo marker present`) before starting anything. Then deploy the current main revision (`sudo -n /usr/local/sbin/nix-os-deploy <main sha>`; the daily reconcile or the next merge does the same) — with the marker back, the deploy starts the CRM again. A manual start works too, databases first: `docker start nixos-twenty-db nixos-twenty-redis`, wait healthy, then `nixos-twenty-server`, then `nixos-twenty-worker`.

## 6. Stop containers

First the two things that would restart the stack on you, then the runtimes. Apps before datastores; the Twenty containers were handled in layer 5.

**The recovery timer.** The 03:17 UTC capture cold-stops and restarts everything, and a failed attempt retries every 15 minutes (up to three per 2h, `automation/recovery/nixos-recovery.service`).

```bash
sudo systemctl disable --now nixos-recovery.timer   # pause now, stay off across reboot
systemctl is-active nixos-recovery.service          # a capture the timer already triggered keeps running
```

- Stopping the timer does not stop a capture already in flight. An in-flight capture cold-stops the stack and restarts every container when it ends — on success, on failure, and even if you `systemctl stop nixos-recovery.service`, because the unit's `ExecStopPost` unconditionally starts the four runtimes (and the Twenty containers when `ENABLED` exists). A refused run does the same. Wait for `inactive` — a capture can take up to an hour (`TimeoutStartSec=1h`) — accept its one-time restart, then continue.

**The GitHub deploy.** Do not disable the whole `ci` workflow: that also stops PR CI, including the `verify` check the merge ruleset requires, so every open PR stalls. Pause the deploy job itself. It runs only for `main` (push, schedule, manual dispatch) inside the `vps` GitHub environment (`.github/workflows/ci.yml`), which gives two levers:

- First record the environment's current protection so resume can restore it exactly: `gh api repos/its-nix/os/environments/vps > vps-environment-before.json` (keep it off the repo). Then require an approval in that environment (repo Settings → Environments → `vps` → Required reviewers). The gate is only as strong as its approvers: any listed reviewer can release a deploy, and administrators can when admin bypass is allowed. For the incident, set the reviewers to the founder alone and turn off admin bypass (in the same settings page: "Allow administrators to bypass"), then verify both with `gh api repos/its-nix/os/environments/vps`. Agents open PRs with the founder's GitHub token, so if agent access is the suspected problem this gate is not containment: disable the workflow instead (below) or revoke the token (layer 7). Every deploy — merge-triggered, the scheduled 05:17 UTC reconcile, a manual dispatch — then waits for an approval you simply do not grant, and PR CI is untouched. The same gate can be set through the environments REST API (verify the exact call before use).
- Cancel deploy runs already queued or in progress — they serialize in the `nix-os-vps` concurrency group (`cancel-in-progress: false`), so cancel each:

```bash
# every run not yet completed: queued, in_progress, waiting (environment gate), pending, requested
gh run list --workflow ci --branch main --limit 50 --json databaseId,status,event \
  -q '.[] | select(.status != "completed")'
gh run cancel <run-id>
```

- On the VPS, confirm remote deploy work has drained before stopping containers. A deploy runs `compose up`, which restarts what you stop. `deployment-attempt.json` alone is not enough: a deploy that was dispatched but is still waiting for `/run/lock/nix-os-deploy.lock` leaves the previous attempt's `healthy` or `failed` visible. Check for any process holding or waiting on the deploy lock: `sudo fuser -v /run/lock/nix-os-deploy.lock` must list nothing (a waiter opens the lock file before it blocks, and the recovery capture and approval worker use the same lock; do not use `pgrep -f deploy-os`, which also matches reviewer prompts that mention the file), and `sudo cat /srv/nix-clean/deployment-attempt.json` must not read `"status": "deploying"`. A cancelled GitHub run may leave the remote deploy executing; trust these checks, not the run state. If one is running, let it finish, then re-check both.
- Deploys restart stopped containers; that is why this pause comes before the runtimes. Disabling the whole workflow (Actions → disable workflow) remains the blunt last resort — it stops PR CI too.

**The runtimes.**

```bash
docker stop --time 90 nixos-t3 nixos-reviewer
docker stop --time 90 nixos-hermes nixos-hindsight
```

- `--time 90` matches the deploy's own stop grace; Hermes and T3 shut down gracefully inside it. A forced kill after the timeout still loses no state.
- **Verify**: `docker ps --filter name=nixos-` lists nothing running.
- **Resume**: start the core runtimes — `docker start nixos-hindsight nixos-hermes nixos-t3 nixos-reviewer` once the repository-level merge freeze is verified (layer 1; leave `nixos-t3` stopped after a bearer compromise, layer 7), and the Twenty containers **only after the `ENABLED` marker is back** (layer 5 resume); started without it, the next deploy stops them again and the nightly capture refuses. Then `sudo systemctl enable --now nixos-recovery.timer` — `Persistent=true` makes a missed 03:17 window fire the capture immediately on activation, so enable it only with the stack running — and lift the GitHub deploy pause above. The reviewed alternative to all of it is one deploy of current main, which starts and health-checks the whole stack.
- Stopped containers and everything on disk survive. Unpushed commits in T3 worktrees are only at risk on host loss, not on a stop.

## 7. Revoke credentials at the provider

Last resort: this breaks the stack until each credential is re-issued. Do it when a credential itself is the incident (leak, compromise). Rotate, then use [PROVIDER-AUTH-RECOVERY](PROVIDER-AUTH-RECOVERY.md) to restore logins.

| Credential | Where it lives | Revoke at the provider | Resume |
| --- | --- | --- | --- |
| Reviewer GitHub App key | `/srv/nix-clean/reviewer/secrets/app.pem` | GitHub App settings: **delete the compromised private key** — regenerating adds a new one and every old key stays valid until deleted. If the App itself is compromised, suspend or uninstall the installation instead. `independent-review` then fails closed, so nothing merges | install the new key per [deploy/reviewer](../../deploy/reviewer/README.md) and recreate the reviewer container |
| Todoist API token (founder account) | `/srv/nix-clean/todoist/token` (root-only) | revoke the token in Todoist settings; also remove the file on the VPS | provision a fresh token via operator-controlled transfer and run the worker check ([FOUNDER-APPROVAL-QUEUE](../FOUNDER-APPROVAL-QUEUE.md)) |
| Twenty API key (per venture lead, `TWENTY_API_KEY`) | lead profile environment | delete the workspace API key in Twenty's settings | issue a new least-privilege key and configure it for the lead |
| Google Drive vault grant | `/srv/nix-clean/drive/` (root-only, rclone token) | revoke the rclone grant in the founder's Google account; stop `nix-drive.timer` | repeat the SSH sign-in ([KNOWLEDGE-VAULT](../KNOWLEDGE-VAULT.md)) |
| Harness logins (Claude Code, Codex, OpenCode, Antigravity, Grok) | `t3/home` and `reviewer/home` | sign out / revoke the device at each provider; a dead login drops that provider from the reviewer rotation | [PROVIDER-AUTH-RECOVERY](PROVIDER-AUTH-RECOVERY.md) |

### Suspected compromise of the Hermes→T3 bearer

`MCP_T3_API_KEY` is a full-access credential and, once minted, stays valid on T3's side for up to 30 days — waiting out the expiry is not containment.

- T3 listens on host loopback only (`deploy/t3/README.md`), so the bearer is usable only from the VPS itself; the host access boundary is the real containment. Then rotate so Hermes stops presenting the old token:

```bash
sudo rm /srv/nix-clean/hermes/nix/t3-mcp.json                 # non-secret sidecar; without it the mint script must issue a fresh token
sudo python3 /opt/nix-clean-source/deploy/t3/hermes_token.py  # writes the new MCP_T3_API_KEY, never prints it
docker restart --timeout 60 nixos-hermes                     # the gateway loads the key only at start
```

- The mint script reuses a stored token only when the sidecar, the stored key and T3's probe of it all agree (`ensure()` in `deploy/t3/hermes_token.py`); with the sidecar gone it mints unconditionally.
- The old token remains valid on T3's side until its recorded expiry. This repository's tooling has no T3-side revoke — only the pairing flow is implemented (`deploy/t3/README.md`); check T3's own auth commands in the cockpit for a revoke (verify before use).
- **Containment until then:** keep `nixos-t3` stopped (`docker stop --time 90 nixos-t3`). With no listener the old bearer cannot be used from anywhere. Start T3 again only after a verified T3-side revoke of the old token, or after its recorded expiry has passed. Rotating the token above changes only what Hermes presents; it does not invalidate the stolen one.
- The next deploy shares the new token with every venture lead's private `.env` and completes the bookkeeping ([PROVIDER-AUTH-RECOVERY](PROVIDER-AUTH-RECOVERY.md)).

## What each layer leaves behind

Every layer above is a pause. The venture registry, cron stores, Kanban boards, CRM database, approval spool and all logins stay under `/srv/nix-clean` and keep being captured by the recovery image once its timer runs again.

Resume in this order, not in any order — the Twenty marker, the containers and the timers interact:

1. If any credential was revoked (layer 7), re-issue it first ([PROVIDER-AUTH-RECOVERY](PROVIDER-AUTH-RECOVERY.md)); nothing else resumes a dead login.
2. Restore the Twenty `ENABLED` marker (layer 5 resume) if the CRM was enabled before the incident, and verify it. This comes before any deploy or container start: with the marker absent, the deploy stops the CRM again (`crm_enabled` in `scripts/deploy-os.py`).
3. Before anything below can restart the reviewer, confirm the merge freeze is the repository-level one: `gh api repos/its-nix/os -q .allow_auto_merge` prints `false` (layer 1). Steps 3–5 can each start `nixos-reviewer` (a container start, a missed recovery capture, a deploy). Then start the containers (layer 6 resume): the core runtimes, then the Twenty containers — only after step 2. A single deploy of current main starts and health-checks the whole stack instead. Keep `nixos-t3` stopped if the Hermes→T3 bearer was compromised (layer 7).
4. Re-enable the host timers: the approval timer (layer 2), then the recovery timer — `Persistent=true` makes a missed 03:17 window fire the capture immediately on activation, so enable it only with the stack running.
5. Lift the GitHub deploy pause (layer 6): restore the `vps` environment's protection to what `vps-environment-before.json` recorded (if it already had required reviewers, keep them; remove only the reviewer you added), and verify with `gh api repos/its-nix/os/environments/vps`. Or re-enable the `ci` workflow if it was disabled outright.
6. Lift the merge freeze (layer 1): restore repo auto-merge (`-F allow_auto_merge=true`, verify `true`), then unhold and mark ready each PR.

Layers 3 and 4 (ventures, Hermes cron) can resume in either order once Hermes is running; their own commands are enough.
