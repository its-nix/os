# Company OS refactor — baseline, migration map, decisions and evidence

Running record for the Docker-first Company OS refactor (PRD v1.1). It records what is **observed**, not intended. Host identifiers, IPs, chat/agent IDs and secrets are deliberately omitted: this repository is public.

## 1. Observed baseline — 2026-11-23

| Area | Observed state |
| --- | --- |
| Deployed revision | `main` `d5ca55e`, healthy receipt; deploy via restricted `nixos` SSH forced command |
| Hermes | 0.23.0 official digest-pinned image, one container, host network, loopback API/dashboard. Profiles `default`, `engineer`, `reviewer`; one multiplexed gateway serves all three; Discord (LNX bot) on `default` only |
| Hermes Kanban / Cron | Native, healthy scheduler heartbeat. One board `default`, **0 tasks**; **0 cron jobs** in any profile |
| Paperclip | Official pinned image, loopback. **1 company, 3 agents (all paused), 0 issues, 0 projects, 0 routines.** No runtime dependency from Hermes |
| Native state | `/srv/nix-clean` (Hermes home ~0.7 GB, Paperclip embedded Postgres, KB snapshot, Kit input) |
| Recovery | Private Docker Hub daily image. **Last success 2026-11-16.** Failing since 2026-11-17 (see §4, D1); timer stopped manually 2026-11-18 |
| GitHub | Public repo; `main` **unprotected**, no rulesets, `allow_auto_merge=false`; free org plan. CI: unit tests + compose contract + main-only deploy |
| Host | Small shared VPS (2 vCPU, 7.8 GB RAM, no swap, ~21 GB free disk). Unrelated workloads run alongside and are out of scope |
| Open work | PR #68 (Paperclip-era workflow proposal, draft) and issues #49–51 (Lead routing/continuity) — superseded by this refactor once dynamic venture leads exist |

## 2. Migration map

| Path | Class | Decision |
| --- | --- | --- |
| `deploy/compose.yaml`, `deploy/versions.json`, `scripts/deploy-os.{sh,py}`, `.github/workflows/ci.yml` | Active | **Reuse/refactor.** Add services (T3, optional Twenty) and adapt readbacks; keep one Hermes service and digest pins |
| `automation/recovery/full_*.py`, `nixos-recovery.*` | Active | **Refactor.** Fix current failure; later extend coverage to T3/Twenty/Hindsight and make Paperclip optional |
| `deploy/pilot.json`, `deploy/roles/*`, `scripts/prepare-clean-os.py` | Active (pilot) | **Refactor.** `unattended_execution: false` stays until the review gate exists; three pilot roles become generic specialist skills |
| Paperclip service | Active, idle | **Retire** after Hermes Kanban owns work: archive state, keep it in recovery history, then remove from active Compose |
| `nix-os.yaml`, `profiles/*`, `scripts/bootstrap.sh`, `scripts/github-webhook-*.py`, `scripts/reconcile-*`, `automation/monitoring/*`, `automation/skill-scout/*` | Historical | **Keep for audit; never an input.** Venture names in them must not be activated by generic code |
| `skills/packs.json`, `skills/{proposals,approvals,...}`, `scripts/install-skills.py` | Historical governance | **Refactor** into NixKit packs (core…data-ai); keep provenance/pinning/integrity; drop manual-approval assumption via reviewed migration |
| `skills/nix-knowledge`, `skills/nix-document-design` | Active skills | **Reuse** |
| `docs/NIX-OS-SPEC.md` (untracked local draft) | Founder draft | Not committed by the refactor |

## 3. Decision log

| # | Date | Decision | Reason |
| --- | --- | --- | --- |
| D1 | 2026-11-23 | Fix recovery before any topology change | No usable recent recovery point existed; PRD requires one before changing live state |
| D2 | 2026-11-23 | Venture registry lives in Hermes native state (`/opt/data/nix/ventures`), not Git | Ventures are runtime data (0..N); Git holds only schema, templates and the scaffolder |
| D3 | 2026-11-23 | T3 on a Compose bridge network, published on loopback only; never host network | Host loopback exposes the unauthenticated Paperclip API and the Hermes dashboard |
| D4 | 2026-11-23 | T3 pinned to stable v0.1.11 linux-x64 tarball verified against upstream SHA256SUMS | Reproducible; nightlies change several times per day |
| D5 | 2026-11-23 | Hermes→T3 via T3's native bearer-scoped API/MCP (`t3 auth session issue`), no proxy service | Native headless auth exists; avoids a custom T3 proxy |
| D7 | 2026-11-23 | Hermes→T3 credential minted by the root deploy via T3's own OAuth pairing approval, renewed when <7 days remain | T3 caps CLI-issued MCP sessions at `approval-required`; OAuth tokens last 30 days without refresh |
| D8 | 2026-11-23 | Hindsight via the native Hermes plugin (local embedded mode), deferred until the founder picks the extraction LLM | Embedded mode keeps storage local, but extraction sends text to whichever LLM is configured |
| D6 | 2026-11-23 | PRs in this refactor are built with the founder's admin token and reviewed by an independent model; this is **not** a forge-proof gate | The forge-proof gate (separate builder identity, ruleset requiring Actions check runs) is itself a deliverable of Phase 3 |
| D9 | 2026-11-25 | Personal profile is created by the deploy through native `hermes profile create` only when absent; contract in `deploy/profiles/personal.json`, outside `native_inputs` | Same container means logical separation only; never overwrite an existing profile, never clone credentials ([contract](PERSONAL-PROFILE.md)) |

## 4. Evidence ledger

| Date | Claim | Evidence |
| --- | --- | --- |
| 2026-11-23 | Recovery failure root cause | Read-only re-run of snapshot path checks on host: exactly one refusal, a Hermes-managed venv interpreter link into `/opt/hermes/`. Fixed in PR #69 |
| 2026-11-23 | Paperclip holds no live work | Native API readback: 0 issues/projects/routines; 3 paused agents |
| 2026-11-23 | Recovery restored | #69 deployed (`4079b26`); live walker 0 problems / 52,883 entries; verified publish `sha256:5f71ff69…` (52,866 entries, remote readback + isolated restore checks) at 18:38 UTC; bounded unit installed (2h/3 starts) and timer re-enabled |
| 2026-11-23 | T3 container isolation | Pinned v0.1.11 image: as uid 10010 no Docker socket, no Hermes/Paperclip state, CapEff 0; Hermes 18642 / dashboard 19119 / Paperclip 13100 closed on loopback, bridge gateway and public IP |
| 2026-11-23 | Hermes -> T3 full access | CLI-minted `mcp-client` tokens capped (`runtime_mode_escalation_denied`); OAuth+PKCE pairing-approved token launched a `full-access` thread |
| 2026-11-23 | Venture scaffold on real Hermes | Throwaway home, pinned 0.23.0: dry run writes nothing; create/rerun idempotent (1 mission, 2 loops active after commit); pause/resume |
| 2026-11-23 | `hermes profile create` is safe for dynamic leads | Throwaway container with empty home on the pinned image: only a lock file is touched outside the new profile |

## 5. Blocked on founder action

One consolidated Todoist request (project `Nix`, label `nix-approval`), raised 2026-11-23:
1. `REVIEW_ANTHROPIC_API_KEY` Actions secret → enables the independent-review gate, ruleset and auto-merge.
2. Builder GitHub identity for T3 (contents + PR write; no workflows/admin/bypass).
3. Coding-harness logins inside T3.
4. Hindsight memory-extraction provider (local GPU vs hosted API): privacy decision.
5. Drive vault folder and scoped access.
6. Personal profile connectors (email, calendar, private documents): founder-authorized Google session and a scoped Drive location.

## Pack migration evidence — 2026-11-25

Read-only inspection found the deployed Kit checkout at its declared revision.
Five default-profile Hermes core trees matched their installed Kit cache
copies. Grilling and handoff were absent. T3 contained Claude Code, Codex,
OpenCode and agy. The pinned OpenCode CLI exposes `debug skill` for native
discovery readback.

A local build from that Kit revision through Skillshare v0.31.0 verified nine
copied skill trees against the private Kit staging prefix. A fresh detached
engineering worktree received exactly the two engineering additions. These
are build and worktree results. Production discovery requires a healthy
main deployment and a separate native readback.

The deployment-style build also passed under Linux/amd64 as UID 65534 from
a Git archive of the installed Kit revision. In disposable local containers,
the pinned Hermes 0.23.0 image listed `grilling` in the `nixkit` category
with the skill bind-mounted read-only. OpenCode 1.19.6 returned `grilling`
from `opencode debug skill` using a fresh home and `.agents/skills`.
Neither discovery test required a model call or a harness login.
