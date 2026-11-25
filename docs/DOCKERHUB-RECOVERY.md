# Daily private OS recovery

The deployed container-native backup uses [liornis/nixos-recovery](https://hub.docker.com/r/liornis/nixos-recovery), a private Docker Hub repository under the operator’s personal account. It captures the complete persistent `/srv/nix-clean` tree, so every stateful service is covered by construction and nothing is selected by allow-list:

| State under `/srv/nix-clean` | Service | Captured |
| --- | --- | --- |
| `hermes/` | Hermes: profiles, memories, sessions, learned skills, credentials, Bot Groups, workspaces, Kanban | yes |
| `t3/home/` | T3 database, settings and provider logins | yes |
| `t3/work/`, `t3/home/.t3/worktrees/`, caches | T3 clones and worktrees | **no**: rebuildable from GitHub (the directories are kept, empty). Unpushed work in a worktree is not backed up |
| `reviewer/home/` | Reviewer subscription logins (Claude, Codex, OpenCode, Antigravity, Grok) | yes, except `.cache/`, `.npm/`, `.codex/tmp/` |
| `reviewer/secrets/`, `reviewer/reviewer.env`, `reviewer/app/` | GitHub App private key (`app.pem`, root 0400), review-selection seed, App id, reviewer code | yes |
| `twenty/` (when `ENABLED` exists) | Twenty CRM: PostgreSQL `db/`, Redis AOF `redis/`, file `storage/`, `db.env`, `server.env`, the flag | yes, cold (see below) |
| `paperclip/` | Retired Paperclip (archived, not running): PostgreSQL, files, encryption keys | yes; stays restorable |
| `knowledge/`, `inputs/`, `versions.json`, `deployment.*`, `source-revision` | Company KB/Kit snapshots and deployment metadata | yes |

Nothing is imported from the retired VPS setup.

Snapshots are unencrypted by request. They contain working credentials and private company context. Keep the repository private and use the native Docker credential store; never copy payloads into Git, Actions artifacts or logs. The builder refuses publication unless the Hub API explicitly reports `is_private: true`, before and after pushing. Dated UTC tags are retained; no moving `latest` tag or registry deletion is used.

## Observed status — 2026-11-10

Daily backups are active. The personal repository is explicitly private, and anonymous metadata access returns 404. The first snapshot, `20261110T174037864615Z`, completed at 17:45 UTC with 5,771 preserved entries. Its remote manifest digest was pulled back and passed file/SQLite restoration, isolated native Paperclip PostgreSQL checks and recovery-image verification. Both production services returned healthy.

The verified manifest is `liornis/nixos-recovery@sha256:d15ce5a2ac023b6019683955754fe3dd0e6c17420ddfc59b84a1112c5f038338`. The root-owned service exited successfully, and the enabled persistent timer is active and waiting for **2026-11-11 03:17 UTC**. Success and attempt receipts are stored privately on the VPS under `/var/lib/nixos-recovery`. Later scheduled runs must continue to advance the success receipt; timer activation alone is not proof that a future backup completed.

## Capture and verification

The host takes a cold snapshot under the CI/CD deployment lock. Before stopping anything it refuses to start when `/` is at or above 85% full (the host's own auto-pruner deletes stopped containers above 90%), when CRM containers run while the CRM is disabled, or when any OS container is not running. It then stops, one at a time with a 90-second grace and applications before the databases they use: `nixos-hermes`, `nixos-t3`, `nixos-reviewer`, and, when `twenty/ENABLED` exists, `nixos-twenty-worker`, `-server`, `-db`, `-redis`. Every container must exit cleanly. PostgreSQL and Redis are therefore **never** copied while running; there is no hot file copy and no dump. The complete state is archived with per-file hashes and native ownership, and the containers start again in the reverse order (databases first) before image creation or upload. Retired Paperclip has no container and is archived as frozen files.

Failure paths restart everything: the Python `finally` block, and the service's `ExecStopPost` hook if the process is killed. A container deleted while stopped is recreated from the deployed Compose file (`up --detach --no-recreate`, with `--profile crm` when the CRM is enabled); state is all bind-mounted. After the restart the job waits up to 5 minutes for the core services and up to 10 minutes when the CRM is enabled (Twenty's own healthcheck allows 330 seconds). Active conversations, review jobs and CRM requests can be interrupted during this short daily window.

Before the cold stop is released the job also compares each running OS container (including every CRM container) with the deployed Compose model: image pin, and exactly the declared bind mounts inside the captured root, with no anonymous volumes.

The recovery image extends the exact pinned official Hermes runtime. A new stopped holder container receives the archive explicitly in its unmounted writable layer; committing a live application's mounted volumes would omit their data. No private BuildKit cache is created. Temporary payloads and this job's local image tag are removed after the run.

Every run restores into separate storage and validates coverage, hashes and SQLite integrity. When present, the restored Twenty PostgreSQL is opened in single-user mode with networking disabled and must expose Twenty's migrated `core` schema; the manifest must carry the CRM flag, database and both credential files together or the snapshot is refused. The frozen archived Paperclip PostgreSQL is opened the same way (company and agent rows present), but only when its restored bytes differ from the last validated copy. No application, bot, group or agent task runs during verification. Paperclip’s documented permanent empty `.lock.sqlite` advisory files are preserved byte-for-byte; they are not application databases ([pinned upstream contract](https://github.com/paperclipai/paperclip/blob/d69631b01b9c658f80bf8bed389863dce82643e1/doc/workspace-restore-locks.md)). After upload, the remote manifest digest is pulled and the restore/database checks repeat against its payload before `last-success.json` is written. This proves database readability and file preservation; it does not replace post-disaster model/connector acceptance.

## Credentials in the private image

The image holds working credentials, by decision (2026-11-25, per the founder's brief): Hermes credentials were already included, and the reviewer's logins and GitHub App key and Twenty's secrets are the same class of already-native credential. Excluding them would make a restore unusable without re-issuing every login and would not lower the risk, because the image is private, unencrypted by request and equally sensitive without them. Specifically it contains:

- reviewer: provider subscription logins under `reviewer/home/`, the GitHub App private key `reviewer/secrets/app.pem`, the selection seed and `reviewer.env`;
- T3: provider logins under `t3/home/`;
- Twenty: `db.env` (database password), `server.env` (`APP_SECRET`, `ENCRYPTION_KEY`) and the database itself. Because `docker compose config` inlines `env_file` values, these also land in the captured `deployment.json` and in the mode-600 `compose.json` produced by a restore;
- Hermes credentials as before.

The controls are unchanged: the repository must be explicitly private (checked before and after every push), tags are dated and never deleted by the job, and payloads never go to Git, Actions artifacts or logs. A restored host holds live credentials; treat the restore destination like production. If the image or the Docker Hub token is ever exposed, rotate the App key, revoke the provider logins and rotate Twenty's secrets.

Only state inside the declared root and the pinned runtime are covered. Unexpected external mounts/databases/links cause refusal. Host SSH keys, Docker Hub login, CI bootstrap and external Todoist/GitHub/Drive services are not backed up. Recreate the host access boundary separately. A restored OAuth login may still require provider reauthorization after token rotation or revocation.

## Operator installation

Sign in from the `nixos` account on the VPS using a Docker Hub personal access token at the native password prompt. Creating the private repository requires **Repo Read, Write, Delete** permission; **Repo Read & Write** is sufficient for daily uploads after the repository exists. After creating the repository, reduce the same token to **Repo Read & Write**. Docker Hub allows changing an existing token’s permissions without generating a new token or repeating login ([permission reference](https://docs.docker.com/security/access-tokens/reference/)). Do not paste the token into chat or a shell argument.

```bash
ssh -l nixos vps
docker login --username YOUR_DOCKER_HUB_ACCOUNT
```

A trusted host administrator then creates the repository and performs the first verified publication:

```bash
export DOCKER_CONFIG="$(getent passwd nixos | cut -d: -f6)/.docker"
python3 /opt/nix-clean-source/automation/recovery/full_create.py \
  --apply --publish --create-repository \
  --image docker.io/YOUR_DOCKER_HUB_ACCOUNT/nixos-recovery
```

The authenticated account must equal the repository owner. Creation explicitly requests private visibility and refuses any existing public repository. Authentication, quota or API errors stop the operation before state publication; the code never falls back to automatic public creation.

After successful remote restore verification, install the reviewed units and configuration as root:

```bash
install -d -m 700 /etc/nixos
# Write the non-secret destination to mode-600 /etc/nixos/recovery.env:
# NIXOS_RECOVERY_IMAGE=docker.io/YOUR_DOCKER_HUB_ACCOUNT/nixos-recovery
# DOCKER_CONFIG=<the nixos account home>/.docker
install -m 644 /opt/nix-clean-source/automation/recovery/nixos-recovery.service /etc/systemd/system/
install -m 644 /opt/nix-clean-source/automation/recovery/nixos-recovery.timer /etc/systemd/system/
install -m 755 /opt/nix-clean-source/scripts/deploy-os.sh /usr/local/sbin/nix-os-deploy
systemctl daemon-reload
systemctl enable --now nixos-recovery.timer
```

The timer runs daily at **03:17 UTC**, with a persistent catch-up after downtime. Failed runs retry every 15 minutes. The root service reads only the selected native `nixos` account’s `.docker` login. Main-only CI/CD updates the reviewed backup implementation through the existing source pointer; no state-holding public GitHub runner is installed.

```bash
systemctl list-timers nixos-recovery.timer
systemctl status nixos-recovery.service
journalctl -u nixos-recovery.service --since yesterday
cat /var/lib/nixos-recovery/last-success.json
cat /var/lib/nixos-recovery/last-attempt.json
```

A failed attempt leaves the previous success receipt intact. Check completion time and manifest digest, not just timer activation. If a job is killed, the service's stop hook also starts both applications. A manual local-only canary is available with `full_create.py --apply`; it performs the cold capture and isolated restore without any registry upload.

## Restore on a replacement VPS

Install Docker/Compose and sign in to the private repository. Select a successful **manifest digest** from the receipt. Use a new empty host destination and keep any old working system intact. These commands extract only; they do not start bots:

```bash
image=docker.io/YOUR_DOCKER_HUB_ACCOUNT/nixos-recovery@sha256:YOUR_VERIFIED_DIGEST
docker pull "$image"
docker run --rm --network none --read-only --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  "$image" verify
install -d -m 700 /srv/nixos-restored
docker run --rm --network none --read-only --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  --mount type=bind,source=/srv/nixos-restored,target=/restore \
  "$image" restore --destination /restore
```

The destination contains `state/`, mode-600 `compose.json` with the actual captured native environment and portable bind paths, and `manifest.json` with immutable image/input revisions. Empty-target checks refuse replacement of existing state; a failed extraction leaves partial scratch data that must be discarded before retrying.

Pull the pinned Paperclip runtime, inspect Compose locally without logging its private environment, and start only after disabling the previous host's bots to avoid duplicate sessions:

```bash
# T3 is a locally built image: rebuild it from the OS repository at the manifest's os_revision.
rev=$(python3 -c 'import json;print(json.load(open("/srv/nixos-restored/manifest.json"))["os_revision"])')
git clone https://github.com/its-nix/os /tmp/nix-os && git -C /tmp/nix-os checkout "$rev"
tag=$(python3 -c 'import json;print(json.load(open("/srv/nixos-restored/compose.json"))["services"]["t3"]["image"])')
docker build -t "$tag" /tmp/nix-os/deploy/t3
docker compose -f /srv/nixos-restored/compose.json pull --ignore-buildable
docker compose -f /srv/nixos-restored/compose.json up -d --wait
# Only if the source host ran the CRM (state/twenty/ENABLED exists):
[ -e /srv/nixos-restored/state/twenty/ENABLED ] && docker compose -f /srv/nixos-restored/compose.json --profile crm up -d --wait
```

Disable the previous host's bots **and its reviewer** before starting anything: the restored reviewer (GitHub App key and logins) would otherwise answer the same pull requests as the old one, and the old Twenty must not keep syncing. Providers may require a fresh login after a restore.

Verify both native health checks, profile authentication, Bot Group/controller identity, a representative model conversation and connector access before resuming work. The restored source inputs and receipts are under `state/`; re-establish reviewed GitHub CI/SSH bootstrap against the restored root deliberately before automatic deployments. The timer assumes the original `/srv/nix-clean` root until that host configuration is restored.

The older [Hermes-only GHCR workflow](HERMES-RECOVERY.md) remains historical and disabled. It is not the daily full-system path described here.

## Prove a restore without touching live state

Run this on the host (or any Docker host) to restore the **latest published** image into a throwaway directory and check what is present. It extracts only, starts nothing, never writes under `/srv/nix-clean`, and refuses below 15 GB free disk:

```bash
. /etc/nixos/recovery.env; export DOCKER_CONFIG
[ "$(df --output=avail -k / | tail -1)" -gt $((15*1024*1024)) ] || exit 1
image=docker.io/$(python3 -c 'import json;print(json.load(open("/var/lib/nixos-recovery/last-success.json"))["manifest_digest"])')
docker pull --quiet "$image"
SEC="--network none --read-only --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,mode=1777"
docker run --rm $SEC "$image" verify
R=$(mktemp -d /var/tmp/nixos-restore-proof.XXXXXX); chmod 700 "$R"
docker run --rm $SEC --mount type=bind,source="$R",target=/restore "$image" restore --destination /restore
# Presence and mode/size only; never print file contents:
ls -ld "$R"/state/{hermes/config.yaml,t3/home/.t3/userdata,reviewer/home,reviewer/secrets/app.pem,twenty/db/PG_VERSION,twenty/ENABLED}
rm -rf "$R"; docker image rm "$image"
```

Expected for a capture taken with the reviewer and CRM running: every listed path exists, and `compose.json` lists the `reviewer` and `twenty-*` services. The Twenty and Paperclip databases themselves are opened in single-user mode by every capture (and its remote readback), not by this extraction.
