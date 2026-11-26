# Nix Company OS

An always-on company operating system: one multi-profile Hermes runtime runs ventures through accountable leads, delegates coding to a Dockerized T3 Code, and improves itself through reviewed GitHub PRs. All self-hosted runtime services run under the `nixos` Docker Compose project on the VPS. The target design is [PRD v1.1](docs/REFACTOR.md#1-observed-baseline--2026-11-23); this page describes **what is actually running**.

## Implementation status (observed 2026-11-23 to 2026-11-25)

Each row is as of its evidence date. Founder items were requested on 2026-11-23 and are marked as not re-verified unless checked later.

| Capability | Status | Evidence / where |
| --- | --- | --- |
| Hermes runtime (one container, multi-profile, native Kanban/Cron) | **Running** | `nixos-hermes`, official pinned 0.23.0; profiles hot-added without restart |
| Venture Factory + dynamic venture leads | **Running** | `skills/venture-factory`; live `demo` venture: lead completed Mission 1 autonomously and delegated review to the `reviewer` profile; the Factory now requires an approved PRD/GTM (#79) |
| T3 Code (Dockerized, 5 harnesses) | **Running** | `nixos-t3` (v0.1.11 + Claude Code, Codex, OpenCode, Antigravity CLI, Grok); isolated network, no Docker socket or Hermes state |
| Hermes → T3 delegation (full access, worktree per task) | **Running** | T3 MCP registered in LNX (59 tools); credential renewed by deploy; `skills/t3-delegate` |
| Harness logins inside T3 | Blocked: founder | Interactive provider logins (Todoist request, 2026-11-23; not re-verified) |
| Daily private recovery (Hermes, T3, reviewer, Twenty; archived Paperclip) | **Running** | Daily 03:17 UTC; last verified publish 2026-11-24 03:26 UTC. The 2026-11-25 run failed at `docker inspect` (missing containers); fixed by #93, next run 2026-11-26 ([runbook](docs/DOCKERHUB-RECOVERY.md)) |
| Main-branch CI → VPS deploy | **Running** | `ci.yml` → restricted `nixos` SSH forced command |
| Independent AI review gate | **Running** | Reviewer GitHub App publishes the required `independent-review` check on `main`; passed on #105 (2026-11-25) ([policy](docs/AUTHORITY.md)) |
| `main` ruleset + auto-merge | **Active** (checked 2026-11-25) | Ruleset `main-protection` active; repository auto-merge enabled. Agents still open PRs with the founder's GitHub identity ([policy](docs/AUTHORITY.md)) |
| Twenty CRM (optional profile) | Ready, off | Merged (#76); starts when `twenty/ENABLED` exists; first admin + workspace key are founder-created |
| Lead → CRM → approval-gated action procedure | Deployed, not live-proven | `skills/crm-lead-ops` (#77); needs a Twenty workspace key and a Todoist queue token (founder request, 2026-11-23) |
| Company loops (health, digest, improvement) | **Running** | LNX cron: no-agent health check every 6 h (silent when healthy), daily digest, weekly improvement (#78) |
| Hindsight memory | Open PR #102 (unmerged at 2026-11-25) | PR #102 configures a pinned loopback server, per-profile banks and local embeddings. The founder chose ChatGPT for extraction and must complete a [one-time device sign-in](deploy/hindsight/README.md) after deployment. |
| Google Drive | Requires founder sign-in | [Direct Hermes access and setup](docs/KNOWLEDGE-VAULT.md) · Markdown in My Drive/Nix, reviewed policy in Git |
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
