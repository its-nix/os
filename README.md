# Nix Company OS

An always-on company operating system: one multi-profile Hermes runtime runs ventures through accountable leads, delegates coding to a Dockerized T3 Code, and improves itself through reviewed GitHub PRs. All self-hosted runtime services run under the `nixos` Docker Compose project on the VPS. The target design is [PRD v1.1](docs/REFACTOR.md#1-observed-baseline--2026-11-23); this page describes **what is actually running**.

## Implementation status (2026-11-23)

| Capability | Status | Evidence / where |
| --- | --- | --- |
| Hermes runtime (one container, multi-profile, native Kanban/Cron) | **Running** | `nixos-hermes`, official pinned 0.23.0; profiles hot-added without restart |
| Venture Factory + dynamic venture leads | **Running** | `skills/venture-factory`; live `demo` venture: lead profile, board, 2 loops, mission run by the lead autonomously |
| T3 Code (Dockerized, 5 harnesses) | **Running** | `nixos-t3` (v0.1.11 + Claude Code, Codex, OpenCode, Antigravity CLI, Grok); isolated network, no Docker socket or Hermes state |
| Hermes → T3 delegation (full access, worktree per task) | **Running** | T3 MCP registered in LNX (59 tools); credential renewed by deploy; `skills/t3-delegate` |
| Harness logins inside T3 | Blocked: founder | Interactive provider logins (Todoist request) |
| Daily private recovery (Hermes, Paperclip, T3) | **Running** | Daily 03:17 UTC; last verified publish 2026-11-23 ([runbook](docs/DOCKERHUB-RECOVERY.md)) |
| Main-branch CI → VPS deploy | **Running** | `ci.yml` → restricted `nixos` SSH forced command |
| Independent AI review gate + ruleset + auto-merge | Staged (#73) | Needs `REVIEW_ANTHROPIC_API_KEY`; then the ruleset is enabled ([policy](docs/AUTHORITY.md)) |
| Twenty CRM (optional profile) | Staged (#76) | Starts only when a venture needs it; workspace key is founder-created |
| Lead → CRM → approval-gated action procedure | Staged (#77) | Mock-tested; live proof needs a Twenty key and a Todoist queue token |
| Hindsight memory | Blocked: decision | Extraction LLM choice (local GPU vs hosted) is a privacy decision |
| Drive + Obsidian knowledge | Not started | Needs the vault folder and scoped access; the KB snapshot stays authoritative meanwhile |
| Personal agent profile (`personal`) | Contract + idempotent deploy step ([contract](docs/PERSONAL-PROFILE.md)) | Logical separation only; email/calendar blocked on the founder |
| Paperclip | Running, idle | 0 issues/projects, 3 paused agents; retire after cutover with state kept in recovery |
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
| `deploy/` | Compose, pins (`versions.json`), T3 image |
| `scripts/deploy-os.{sh,py}` | Root deploy launcher and release application |
| `automation/recovery/` | Cold capture, verified private publish, restore |
| `skills/` | First-party OS skills distributed to `skills/nix-os/` (Venture Factory, venture lead, T3 delegate, CRM lead ops) |
| `.github/` | CI/deploy and the independent review gate (founder-owned) |
| `nix-os.yaml`, `profiles/*`, `automation/monitoring`, `automation/skill-scout` | Historical. Not deployment inputs; never activated by generic code |

Credentials, memories, sessions and databases live in native stores on the VPS, never in Git.
