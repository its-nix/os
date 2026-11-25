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

`scripts/deploy-os.py` calls `ensure_personal_profile` after Hermes is healthy. The Hermes user can write anywhere in its home, so root treats every path there as attacker-influenced and never trusts, writes or chowns through it.

1. **Validate the contract first.** The manifest, the role file and the pilot model are checked up front (types, required keys, files inside the release, Discord disabled, `approvals.mode: manual`, the workspace path). Any fault is a handled provisioning failure: it is reported on stderr and the company deployment continues. Only this step's own messages are printed, never native command output or argv (which can hold role text).
2. **Build under a staging name.** The profile is created as `personalsetup` (`hermes profile create personalsetup --no-alias`, no description so the kanban orchestrator cannot route to it). Discord is disabled first, then approvals, the model and the workspace key are set with `hermes -p personalsetup config set`, and every key is read back. The role instructions are written to `SOUL.md` **by the Hermes user** (`docker exec -u 10000 ... sh -c 'printf ... > file.next && mv'`) and read back. Root writes nothing inside the Hermes home. The multiplexed gateway serves every profile directory, but a fresh non-cloned profile has no platform credentials and nothing routes to the staging name.
3. **Workspace.** `workspaces/` is root-owned, so root creates `workspaces/personal`: each path component is opened relative to its parent descriptor with `O_NOFOLLOW|O_DIRECTORY`, and `fchown`/`fchmod` are applied to the descriptor. A symlink anywhere on that path is refused and its target is never touched.
4. **Publish last.** Only when everything read back does `hermes profile rename personalsetup personal` run (native: it also migrates routing identity and unroutes/routes the multiplexer). `personal` therefore never exists half-configured. Then the description is set (`hermes profile describe personal --text ...`).

### Ownership proof

Ownership is **not** inferred from anything inside the profile (the Hermes user can forge any file there). The deploy keeps `/srv/nix-clean/provisioning/personal.json` (root, `0600`, directory `0700`, outside the Hermes mount). It records `creating` before `profile create`, then `staged` with the staging directory's device and inode right after, and `complete` after the rename and description. The deployment adopts only a directory whose device and inode match the record:

- `personal` exists and there is no record or no matching inode (the operator's profile, an agent-made one, or a restore onto another filesystem): nothing is touched, no setting, SOUL, memory or credential.
- `personalsetup` exists without a matching record (made by someone else, or a crash between `profile create` and the record): provisioning stops with an error and changes nothing. Inspect it and remove it with `hermes profile delete personalsetup`, then the next deployment retries.
- `staged` and the inode matches: the next deployment resumes (never re-creates), re-applying and re-reading every setting, then renames. A crash after the rename but before `complete` finishes the description and records `complete`.
- `complete`: never touched again, even if the profile is later removed. To re-provision, remove the record by hand.

Limit: inode numbers can be reused after the Hermes user deletes and recreates a directory; the record is defence in depth against adopting a foreign directory, not a sandbox (see below).

A failure of this additive step never fails the deployment; every later deployment retries. It never clones another profile (cloning copies `.env` and credentials) and writes no `nix-venture.json`, so the venture T3-credential and skill syncs skip it.

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
