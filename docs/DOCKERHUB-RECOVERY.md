# Daily private OS recovery

The current container-native backup uses a private Docker Hub repository named `nixos-recovery` under the authenticated operator's personal account. It captures the complete persistent `/srv/nix-clean` tree: Hermes profiles, accumulated memories, sessions, learned skills, credentials, Bot Groups and workspaces; Paperclip's embedded PostgreSQL database, files and matching keys; company KB/Kit snapshots and deployment metadata. Nothing is imported from the retired VPS setup.

Snapshots are unencrypted by request. They contain working credentials and private company context. Keep the repository private and use the native Docker credential store; never copy payloads into Git, Actions artifacts or logs. The builder refuses publication unless the Hub API explicitly reports `is_private: true`, before and after pushing. Dated UTC tags are retained; no moving `latest` tag or registry deletion is used.

## Capture and verification

The host takes a cold snapshot under the CI/CD deployment lock. Both applications briefly stop cleanly, the complete state is archived with per-file hashes and native ownership, and both restart before image creation or upload. Failure paths restart both services. Active conversations can be interrupted during this short daily window.

The recovery image extends the exact pinned official Hermes runtime. A new stopped holder container receives the archive explicitly in its unmounted writable layer; committing a live application's mounted volumes would omit their data. No private BuildKit cache is created. Temporary payloads and this job's local image tag are removed after the run.

Every run restores into separate storage, validates coverage, hashes and SQLite integrity, and starts the restored Paperclip PostgreSQL in single-user mode with networking disabled. It checks that company and agent rows are present. No application, bot, group or agent task runs during verification. After upload, the remote manifest digest is pulled and the restore/database checks repeat against its payload before `last-success.json` is written. This proves database readability and file preservation; it does not replace post-disaster model/connector acceptance.

Only state inside the declared root and the pinned runtime are covered. Unexpected external mounts/databases/links cause refusal. Host SSH keys, Docker Hub login, CI bootstrap and external Todoist/GitHub/Drive services are not backed up. Recreate the host access boundary separately. A restored OAuth login may still require provider reauthorization after token rotation or revocation.

## Operator installation

Sign in from the `nixos` account on the VPS. Use a Docker Hub personal access token at the native password prompt if device-login credentials cannot access repository-management APIs. Do not paste it into chat or a shell argument.

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
docker compose -f /srv/nixos-restored/compose.json pull
docker compose -f /srv/nixos-restored/compose.json up -d --wait
```

Verify both native health checks, profile authentication, Bot Group/controller identity, a representative model conversation and connector access before resuming work. The restored source inputs and receipts are under `state/`; re-establish reviewed GitHub CI/SSH bootstrap against the restored root deliberately before automatic deployments. The timer assumes the original `/srv/nix-clean` root until that host configuration is restored.

The older [Hermes-only GHCR workflow](HERMES-RECOVERY.md) remains historical and disabled. It is not the daily full-system path described here.
