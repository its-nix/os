# Personal agent profile (PRD §3.2)

A native Hermes profile named `personal` for daily assistance, reminders, personal tasks and private notes. It runs in the same `nixos-hermes` container as `default` (LNX), `engineer`, `reviewer` and the dynamic venture leads.

## Contract

The declarative, non-secret contract is [`deploy/profiles/personal.json`](../deploy/profiles/personal.json); its instructions are [`deploy/roles/personal.md`](../deploy/roles/personal.md).

| Aspect | Contract |
| --- | --- |
| Scope | Personal tasks, reminders, notes, daily help. No company repositories, venture CRM, company Kanban, company KB writes or T3 delegation. It has no T3 credential and receives no `nix-os` company skills. |
| Memory | Hermes profile-native (`profiles/personal/memories`, sessions, `state.db`), separate from every company profile. A separate Hindsight bank (`personal`) is planned; it must never share the company bank. |
| Discord | `platforms.discord.enabled: false`. Only `default` owns the LNX bot. |
| Model | Same provider and model as the other profiles, from `deploy/pilot.json`. Auth is the shared Hermes provider login. No refresh-token file is copied or cloned into the profile. |
| Approvals | `approvals.mode: manual`, `cron_mode: deny`. Spending, public posts, outbound messages and account changes stay founder-gated ([authority](AUTHORITY.md)). |
| Connectors | Email, calendar and private documents are **not connected**; see below. |

## Honest limitation: logical separation only

Personal and company scopes are separated by profile, memory, configuration and instructions. They share one container, one Hermes data volume, one OS user and the same recovery backup. Another profile, a company worker with shell access, or an operator of the container could read personal files. This is **not** confidentiality against other profiles in the same container, and the profile's instructions say so. Minimizing company access to personal Google sessions, tokens and private documents is done by not giving company profiles those credentials and by keeping personal credentials out of this repository; it is not enforced by a sandbox. Material that needs real confidentiality needs a separate container or host first.

The recovery image includes `/srv/nix-clean/hermes`, so personal memory is backed up with company state to the same private repository.

## Deployment

`scripts/deploy-os.py` calls `ensure_personal_profile` after Hermes is healthy. The live Hermes home is writable by the Hermes user, including its profile names.

1. **Validate the contract.** The only manifest config keys are `platforms.discord.enabled`, `approvals.mode`, `approvals.cron_mode` and `terminal.cwd`. All four are required, with Discord disabled, approvals set to `manual`, cron approvals set to `deny` and the declared personal workspace as the working directory. The release supplies the model and role.
2. **Build in a private directory.** Disposable containers use the pinned Hermes image and run as the Hermes UID without network access. They mount only a scratch home under the root-private provisioning directory. Hermes creates `personalsetup` without cloning, applies and reads back the config, then writes and reads back `SOUL.md`. The native rename and description both finish in the scratch home. The live gateway discovers the finished profile on its next reconciliation.
3. **Publish the workspace and profile.** Deploy creates and sets ownership on a fresh workspace in the private directory. It opens the live parent directories without following symlinks and publishes both directories with descriptor-relative `renameat2(RENAME_NOREPLACE)`. An existing workspace with matching recorded identity can be reused as-is. An unrecorded workspace blocks provisioning and retains its ownership and mode. An existing destination also prevents a concurrent publish from overwriting it.
4. **Verify publication.** Deploy checks that the live paths still identify the published directories. Once published, the Hermes user can modify or remove them. Provisioning failures are reported on stderr and the company deployment continues. Native command output and arguments stay out of the deployment log.

### Ownership and recovery

The root-private `/srv/nix-clean/provisioning/personal.json` record (`0600` in a `0700` directory outside the Hermes mount) stores the profile and workspace device and inode as `prepared` before publication. A matching live profile and workspace let a retry finish recording `complete` without touching either one. If just the workspace was published, a retry reuses that directory and rebuilds the profile. Failed builds are cleaned up on normal exit. A process crash can leave a private scratch directory that a later deployment ignores.

An existing `personal` without a matching record is left alone. A pre-existing live `personalsetup` blocks a new build and can be inspected by the operator. A `complete` record makes later deployments skip the profile. Removing that record permits a fresh build only when the live profile and workspace names are free. Linux device and inode numbers can be reused after deletion, so the record is a retry mechanism within the logical separation described above.

The personal profile uses the shared provider login after publication. The build mounts no live credential files and writes no venture marker, so the venture T3 credential and skill syncs skip it.

The profile contract is deliberately not part of `native_inputs`: changing it does not trip the installed-input migration refusal, and it only takes effect for a profile that does not exist yet. To change an existing personal profile, use native Hermes commands (`hermes -p personal config set ...`) and record the change here.

## Use

```bash
ssh -l nixos vps 'docker exec -u 10000 nixos-hermes hermes -p personal chat -q "..." -Q'
```

## Blocked on the founder

- Email and calendar connectors: need a founder-authorized Google session scoped to this profile.
- Private-document store: needs a scoped Drive location.
- Personal Hindsight bank: waits on the memory-extraction provider decision (D8).
- A personal channel (for example a private Discord DM route): needs a decision on how the LNX bot may route to this profile.
