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

`scripts/deploy-os.py` calls `ensure_personal_profile` after Hermes is healthy:

- If `profiles/personal` exists (ours, the operator's, or half-made), it does nothing: no config, SOUL, memory or credential is rewritten.
- Otherwise it runs the native `hermes profile create personal --no-alias`, sets the model, Discord, approvals and workspace keys with `hermes -p personal config set`, reads every key back, then writes `SOUL.md` and a `nix-profile.json` marker owned by the Hermes user. Readback failure leaves the profile without the marker and fails the step.
- It never clones another profile (cloning copies `.env` and credentials) and writes no `nix-venture.json`, so the venture T3-credential and skill syncs skip it.
- A failure of this additive step is reported on stderr and does not fail the deployment; every later deployment retries.

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
