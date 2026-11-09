# Tracked VPS delivery

The `ci` workflow checks every push and PR. Passing `main` pushes deploy automatically; manual main runs and a daily 05:17 UTC run reconcile the same stack and verify health. GitHub's `vps` environment records the commit and deployment result. Native receipts record the last healthy revision and latest attempt on the VPS.

The dedicated SSH operator is `nixos`. It has the human operator's existing public key and Docker administration access. Docker membership grants host-level administrative power; this account separates routine access from root login, rather than providing a sandbox. Hermes and Paperclip retain their native container users and state ownership. Existing root access and unrelated services are unchanged.

## Deployment boundary

The GitHub-hosted deployment job needs a successful verification job and the main ref. The environment permits only the `main` branch. Its separate SSH key has `restrict` and a forced command; it cannot open a shell, forward ports or request a PTY. The root-owned launcher validates an exact SHA, fetches the fixed public OS repository and refuses a SHA that is no longer current main. A host lock serializes deployments. No state-holding self-hosted runner is installed.

Each revision is checked out into `/opt/nix-os/releases/<sha>`. Compose reads that revision's definitions and official digest pins, pulls images and waits for native health. Deployment then reads back the actual image references, exact loopback listeners and the KB's tests at native UID 10000. Native output stays out of CI logs. The source pointer `/opt/nix-clean-source` and successful receipt move only after these checks pass.

Runtime image and Compose changes can deploy normally. KB/Kit/Agency pins, profiles and generated role instructions have an explicit migration boundary: a changed installed-input contract causes refusal. Refresh the affected snapshot/packages/instructions with their documented native tools, verify them, then record the actual installed input contract. Never restamp metadata to bypass an unapplied change. Credentials, learning, sessions and controller databases are preserved; deployment never calls preparation, `down`, volume removal or recursive ownership repair.

Failed rollouts leave the previous successful receipt and a failed latest attempt. Some containers may already have changed before a later health failure; this is not an automatic rollback. Inspect the native logs and deploy a reviewed correction. Database-affecting downgrades require a compatible recovery procedure. Daily private recovery publication remains a separate, inactive acceptance gate in [Hermes recovery](HERMES-RECOVERY.md).

## One-time operator setup

The initial setup is performed over the existing trusted administrator connection:

1. Create `nixos` with a home and locked password; add its human public key and Docker group membership.
2. Install the reviewed `scripts/deploy-os.sh` as root-owned mode 0755 `/usr/local/sbin/nix-os-deploy`. Grant `nixos` passwordless sudo only for that command. Keep the launcher root-owned; changes to the launcher require this explicit installation step again.
3. Add a distinct CI public key to that account with `restrict,command="sudo -n /usr/local/sbin/nix-os-deploy \"$SSH_ORIGINAL_COMMAND\""`. Verify an attempted `id` command is refused. Store its private key only in the GitHub environment secret `VPS_SSH_KEY`; remove the temporary operator copy after the first successful workflow.
4. Configure environment variables `VPS_HOST`, `VPS_USER=nixos` and `VPS_KNOWN_HOSTS`, using the already trusted host-key record. Restrict environment deployment branches to `main`. Do not use opportunistic key scanning in CI.
5. Verify the current native install against its reviewed source, then record `native_inputs(source)` from `scripts/deploy-os.py` in owner-only `/srv/nix-clean/installed-inputs.json`. This captures dependency pins and instruction/profile source hashes, not private state.
6. Move the existing source-only directory to `/opt/nix-os/bootstrap-source` and make `/opt/nix-clean-source` a symlink to it. Leave `/srv/nix-clean` and native ownership untouched. The first successful deployment replaces only the source symlink.
7. Merge the pipeline and observe its first main deployment. Confirm both receipt SHAs match GitHub and all pilot identities remain paused before provider setup.

The host launcher, account authorization, sudo policy and environment credentials form the one-time bootstrap boundary. Subsequent deployment logic comes from the verified main revision. Rotate the CI key by adding its replacement public key, updating the environment secret, testing a run and removing the old authorization.

## Operator access and provider setup

```bash
ssh -l nixos vps
sudo -n /usr/local/sbin/nix-os-deploy status
docker ps --filter name=nixos-
```

Access the native interfaces through SSH:

```bash
ssh -N -l nixos -L 13100:127.0.0.1:13100 -L 19119:127.0.0.1:19119 vps
```

Open Paperclip at `http://127.0.0.1:13100` and Hermes at `http://127.0.0.1:19119`.

When ready, sign in once through the main profile and choose OpenAI Codex:

```bash
ssh -t -l nixos vps 'docker exec -it -u 10000 nixos-hermes hermes model'
```

Complete the native device authorization once. Named profiles use the global-root provider credentials when they have no profile-specific override; this native fallback was verified for both specialists on the installed image. Configure each specialist's provider/model separately after sign-in, using that shared account. Do not copy refresh-token files between profiles. Keep agents paused until the bounded three-agent acceptance task, group consultation, real Todoist/GitHub/Drive connector boundaries and knowledge-impact handoff are verified. The existing HTTP skill-listing limitation is recorded in [the pilot guide](../deploy/README.md).
