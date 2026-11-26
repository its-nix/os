# Nix Company OS

An always-on company operating system: one multi-profile Hermes runtime runs ventures through accountable leads, delegates coding to a Dockerized T3 Code, and improves itself through reviewed GitHub PRs. All self-hosted runtime services run under the `nixos` Docker Compose project on the VPS. The target design is [PRD v1.1](docs/REFACTOR.md#1-observed-baseline--2026-11-23); this page describes **what is actually running**.

## Implementation status (observed 2026-11-23 to 2026-11-26)

Each row is as of its evidence date. Founder items were requested on 2026-11-23 and are marked as not re-verified unless checked later.

| Capability | Status | Evidence / where |
| --- | --- | --- |
| Hermes runtime (one container, multi-profile, native Kanban/Cron) | **Running** | `nixos-hermes`, official pinned 0.23.0; profiles hot-added without restart |
| Venture Factory + dynamic venture leads (C) | **Verified** | On 2026-11-26 the Factory created, paused, resumed and retired a disposable venture. In a separate run, the `demo` outcome loop completed card `t_02b38370` and wrote the internal positioning note while all three LNX cron jobs were paused. [Live evidence](docs/REFACTOR.md#4-evidence-ledger) |
| T3 Code (Dockerized, 5 harnesses) | **Running** | `nixos-t3` (v0.1.11 + Claude Code, Codex, OpenCode, Antigravity CLI, Grok); isolated network, no Docker socket or Hermes state |
| Hermes → T3 delegation (D) | **Verified** for concurrent worktrees | On 2026-11-26 the `demo` lead launched two full-access Codex tasks through its registered T3 MCP. Scratch work overlapped for 54 seconds across two worktrees. [Live evidence](docs/REFACTOR.md#4-evidence-ledger) |
| Harness logins inside T3 | Codex verified on 2026-11-26 | The two delegated Codex tasks ran successfully. Other harness logins await verification. |
| Daily private recovery (Hermes, T3, reviewer, Twenty; archived Paperclip) | **Running** | Daily 03:17 UTC; last verified publish 2026-11-24 03:26 UTC. The 2026-11-25 run failed at `docker inspect` (missing containers); fixed by #93, next run 2026-11-26 ([runbook](docs/DOCKERHUB-RECOVERY.md)) |
| Main-branch CI → VPS deploy | **Running** | `ci.yml` → restricted `nixos` SSH forced command |
| Independent AI review gate | **Running** | Reviewer GitHub App publishes the required `independent-review` check on `main`; passed on #105 (2026-11-25) ([policy](docs/AUTHORITY.md)) |
| `main` ruleset + auto-merge | **Active** (checked 2026-11-25) | Ruleset `main-protection` active; repository auto-merge enabled. Agents still open PRs with the founder's GitHub identity ([policy](docs/AUTHORITY.md)) |
| Twenty CRM (optional profile) | **Running** since 2026-11-25 | `crm` profile enabled; workspace `Nix`; agent API key with a least-privilege `Nix agent` role (read/update records, no deletes, no settings; delete and settings calls refused live). Live `crm.py` intake/qualify/show smoke on 2026-11-25 with a test lead (#76) |
| Lead → CRM → approval-gated action procedure (E) | **Verified** on 2026-11-26 | The `demo` lead ran one continuous pass on a fictitious lead. It took the lead into Twenty, qualified it to SCREENING, added a note, drafted the intro email and queued the approval. The founder completed the Todoist task. The tick alone left `outcome: null`. The lead then recorded the outcome from the founder's confirmed decision. Nothing was sent: the target was a reserved test address and the profile holds no mail credentials. [Evidence](docs/REFACTOR.md#4-evidence-ledger) |
| Founder approval queue (PRD §7.5 / H) | **Running**; acceptance H **verified** on 2026-11-26 | Private Todoist project `Nix approvals` via a host worker and a local Hermes spool; root-only token (#110, #119, #121). Proven: one previewed task per request, idempotent re-request, no effect from the request or from task completion, and a founder-ticked run (acceptance E) whose completion reached the receipt as `completed-by-founder` without authorizing anything. [Setup and demo](docs/FOUNDER-APPROVAL-QUEUE.md) |
| Company loops (health, digest, improvement) | **Running** | LNX cron: no-agent health check every 6 h (silent when healthy), daily digest, weekly improvement (#78) |
| Reviewed improvement loop (F) | **Deployed and rollback-tested** | The deployed health check scored 15/15, up from 6/15 on main, with all six controls preserved. 1,175 of 1,176 files outside `company-ops` matched, with one Hermes usage telemetry file changed. The draft revert passed CI and closed unmerged. [Evidence](docs/REFACTOR.md#4-evidence-ledger) |
| Hindsight memory | **Running** for `demo` on 2026-11-26 | Hermes loaded the Hindsight plugin in `local_external` mode. Authenticated readback found `venture-demo` with one document and four observations. [Runtime setup](deploy/hindsight/README.md) |
| Drive + Obsidian knowledge | Staged pending founder sign-in | [Vault setup and operations](docs/KNOWLEDGE-VAULT.md) · two-way Workspace, one-way reviewed KB |
| Personal agent profile (`personal`) | Contract + idempotent deploy step ([contract](docs/PERSONAL-PROFILE.md)) | Logical separation only; email/calendar blocked on the founder |
| Paperclip | **Retired from active Compose** (#80, merged 2026-11-23) | State archived and captured in recovery (2026-11-25). At the 2026-11-23 baseline: 0 issues/projects, 3 paused agents |
| LNX Discord bot | **Running** | Default profile; unchanged |

Founder-only steps are one Todoist request (project `Nix`, label `nix-approval`). Completing that task authorizes nothing by itself.

## Operate

- [Deployment and inputs](deploy/README.md) · [T3 container](deploy/t3/README.md) · [VPS access and CI/CD](docs/OS-DELIVERY.md)
- [Authority and change governance](docs/AUTHORITY.md): what agents do autonomously vs what the founder executes
- [Recovery](docs/DOCKERHUB-RECOVERY.md): capture, verification, and restore on a new host
- [Refactor record](docs/REFACTOR.md): baseline, migration map, decisions, evidence ledger

### Common tasks

```bash
ssh -l nixos vps 'sudo -n /usr/local/sbin/nix-os-deploy status'                     # deployed revision
ssh -N -l nixos -L 3773:127.0.0.1:3773 -L 19119:127.0.0.1:19119 vps                  # T3 + Hermes UIs
docker exec -u 10000 nixos-hermes python3 /opt/data/skills/nix-os/venture-factory/scripts/venture.py status
docker exec -u 10000 nixos-hermes python3 /opt/data/skills/nix-os/venture-factory/scripts/venture.py pause --id <id> --reason "…"
```

**Emergency stop of all agent routines.** Pause every venture with `venture.py pause`, then `docker stop nixos-t3`. Native state is preserved. Never use `docker compose down -v`.

## Repository layout

| Path | Role |
| --- | --- |
| `deploy/` | Compose, pins (`versions.json`), T3 image, Hindsight memory (`deploy/hindsight`) |
| `scripts/deploy-os.{sh,py}` | Root deploy launcher and release application |
| `automation/recovery/` | Cold capture, verified private publish, restore |
| `skills/` | First-party OS skills distributed to `skills/nix-os/` (Venture Factory, venture lead, T3 delegate, CRM lead ops) |
| `.github/` | CI/deploy and the independent review gate (founder-owned) |
| `nix-os.yaml`, `profiles/*`, `automation/monitoring`, `automation/skill-scout` | Historical. Not deployment inputs; never activated by generic code |

Credentials, memories, sessions and databases live in native stores on the VPS, never in Git.
