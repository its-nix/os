# Hermes recovery images

Each recovery image embeds a native **full** Hermes backup in the matching Linux Hermes runtime. It includes portable memories, sessions, learned skills, all named profiles, configuration, credentials and SQLite databases. The archive is unencrypted by design and must stay in a **private** GHCR package. Neither the archive nor its contents belongs in Git, Actions artifacts or CI caches.

The source host makes the backup. A hosted runner cannot reconstruct learning from this repository. Docker image commits also omit mounted volumes, so this workflow explicitly copies the native backup archive into the image.

## Create a candidate

Requirements: Python 3.11+, Git, Docker/BuildKit, the installed Hermes CLI and a clean Hermes source checkout. Publishing additionally needs GitHub CLI package access. Use the default Hermes root containing every named profile, rather than a single named profile home.

```bash
python3 automation/recovery/create.py \
  --home "${HOME}/.hermes" \
  --hermes-source "${HOME}/.hermes/hermes-agent" \
  --runtime-image 'nousresearch/hermes-agent@sha256:<64-character-digest>' \
  --image ghcr.io/its-nix/hermes-recovery
```

This is a preview. Add `--apply` to make a **local** image; add `--apply --publish` to publish after verification. `--hermes-bin` selects an explicit executable when needed. Normal output contains aggregate status and the dated image tag; subprocess output is suppressed because native tools may print private values or paths.

Pull the pinned runtime image before applying. Its OCI revision label and `/opt/hermes/.hermes_build_sha` must both equal the clean installed Hermes Git revision, and the CLI must report that revision. Matching release names alone are insufficient. Use the corresponding official runtime digest when available. Otherwise build the upstream Dockerfile from a clean `git archive` of that exact source revision, with `HERMES_GIT_SHA` and the `org.opencontainers.image.revision` label set to that commit, publish the code-only runtime, and use its immutable digest here. The runtime architecture must match both the source Docker host and recovery VPS; an arm64 Mac image cannot run natively on an amd64 VPS.

Temporary backup/build files live outside the checkout in an owner-only directory and are removed on exit. The Docker daemon still holds image layers and build cache containing the unencrypted archive; use a trusted daemon. Local candidates that fail verification must not be used. Successful builds have unique UTC tags; there is no moving `latest` tag and this workflow never deletes older versions.

## What verification proves

Before publishing, the builder checks archive CRCs, paths, SQLite integrity and coverage of durable root configuration/credentials/databases, memories, session files and learned skills across profiles. Empty or truncated source database headers are refused before native backup can convert them into valid but empty SQLite files. Linked context is refused because native backup skips symlinks; materialize those installations deliberately before retrying. If context files change while the backup runs, coverage may refuse and a later retry is appropriate.

A container then restores into temporary storage with no network, a read-only root, dropped capabilities and no gateway entrypoint. Every restored file is hashed and every database checked again. Stale process markers and database sidecars are excluded. A successful canary proves archive extraction and database readability, not live Discord/API authentication or successful agent replies. It never starts bots or scheduled jobs.

Native backup takes consistent snapshots of individual SQLite databases. It is not one transaction across every file/database. Stop writers first when an application-wide point-in-time cut is required. External memory provider files are included only when supported by native backup; declared paths outside the source user's home are refused when native backup reports them. External services, project checkouts, native login stores, and arbitrary files outside the backup are not migrated. Host-specific absolute paths, keychain-backed credentials and custom system tools may need adjustment on the VPS.

## Private package and daily execution

1. Create an empty, harmless seed image in `ghcr.io/its-nix/hermes-recovery` before sending any state. For example, build a scratch image with a non-secret label and push a `bootstrap` tag. GitHub creates new packages privately by default; explicitly inspect its package settings and leave visibility **private**. Do not infer privacy from the source repository. The builder checks the package API before and after pushing and refuses missing, inaccessible, internal or public packages.
2. Give the private control repository Actions access to that package. The daily job uses its short-lived `GITHUB_TOKEN` with `packages: write`. Manual publishing uses `gh` authentication with package read/write permission; tokens stay in native CLI storage or the selected secret manager, never command arguments or Git. The builder creates a temporary Docker push login and preserves the host's existing Docker login.
3. Copy [.github/workflows/hermes-recovery.yml](../.github/workflows/hermes-recovery.yml) into a **private** control repository with a `main` branch. Register the source host's runner there with the `hermes-recovery` label, under the Hermes owner's account. Do not register the state-holding host as a runner for this public repository. The workflow deliberately skips public repositories, PR events and non-main branches.
4. Set the following repository variables, then enable the job. Install Python, Git, Docker, `gh` and Hermes on that host; Docker must be running without interactive prompts. Pre-pull the matching runtime digest. The checked-out source must never overlap the Hermes home.

| Variable | Value |
| --- | --- |
| `HERMES_RECOVERY_HOME` | Absolute default Hermes home on the source host |
| `HERMES_RECOVERY_SOURCE` | Absolute clean installed Hermes Git checkout |
| `HERMES_RECOVERY_BIN` | Optional explicit Hermes executable |
| `HERMES_RECOVERY_RUNTIME_IMAGE` | Matching runtime pinned with `@sha256:...` |
| `HERMES_RECOVERY_IMAGE` | `ghcr.io/its-nix/hermes-recovery` |
| `HERMES_RECOVERY_OS_REF` | Reviewed 40-character OS commit containing these scripts |
| `HERMES_RECOVERY_ENABLED` | `true` to enable manual and scheduled runs |

Enable the variable before the manual run, then keep it enabled for daily runs at 02:17 UTC. Only one run executes at a time. GitHub schedules may be delayed and the source host must be awake/online; inspect Actions failures and confirm the newest dated tag regularly. After upgrading Hermes, update the matching runtime digest. After changing these scripts, review and update the OS commit pin. Existing Kit installation and policy pins are captured as-is; backup does not synchronize dependencies.

Package setup references: [GHCR authentication and permissions](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), [self-hosted runner security](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners).

## Restore onto a VPS

Choose an explicit known-good dated tag and authenticate to the private registry using native Docker login storage. Use a **fresh** volume:

```bash
image=ghcr.io/its-nix/hermes-recovery:YOUR_DATED_TAG
docker pull "$image"
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777 "$image" verify
docker volume create hermes-recovered
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  --mount type=volume,src=hermes-recovered,dst=/restore \
  "$image" restore
```

The volume now contains `hermes/` (the complete Hermes root including named profiles) and `user/` (home-relative external provider files). Existing contents are refused. A failed restore leaves partial data; discard that new volume and retry with a fresh one. Retain the old working instance until recovery is accepted.

After inspecting machine-specific settings and ensuring the original instance is stopped, explicitly activate the restored gateway with the bundled executable:

```bash
docker run -d --name hermes-recovered \
  --mount type=volume,src=hermes-recovered,dst=/restore \
  -e HOME=/restore/user -e HERMES_HOME=/restore/hermes \
  --entrypoint /opt/hermes/.venv/bin/hermes "$image" gateway run
```

This direct foreground gateway runs as root in the container, matching the owner-only restored files; it does not use the base image's supervision/privilege-drop shim. Configure network policy and restart policy for the VPS deliberately. The full runtime executable is available for manual CLI/profile work. Never invoke the recovery image's default command expecting it to start a bot: the default is verification only. Test a representative conversation and profile on the intended VPS before declaring the migration complete.
