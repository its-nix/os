# Nix OS

Nix OS is a lean, declarative operating repository for the Nix portfolio. It describes seven isolated Hermes profiles, three project boards, role boundaries, and the safe bootstrap path. It is an organizational operating system, not a Linux distribution.

## Current topology

- **LNX (`default`)** owns company OS/infrastructure, cross-project coordination, portfolio priorities, and independent monitoring. It is not the routine project request router or required delivery messenger.
- **Leads:** Pulse, Algua, and MeaBeclick own their project objectives, execution, recovery, and verified outcomes; Lior communicates with each directly in its dedicated channel.
- **MeaBeclick specialists:** UX, Engineering, and Growth, routed through the MeaBeclick Lead.
- **Boards/projects:** `pulse`, `algua`, and `meabeclick`.

The shared Discord bot routes each named project channel directly to its matching Lead profile and the portfolio channel to LNX. There are no extra bot identities. Leads use Hermes Kanban for durable execution state and handoffs; specialists report to their Lead. A Lead proactively contacts Lior directly when a human-only decision is required.

## Authority by plane

| Plane | Authority |
| --- | --- |
| Discord | Direct human-to-Lead project conversation; company/portfolio matters to LNX |
| Todoist (`td`) | Tasks personally assigned to Lior; never delegated-agent work |
| Hermes Kanban | Durable delegated execution, ownership, status, and handoffs |
| GitHub and project docs | Durable technical artifacts, decisions, reviews, and evidence |

The manifest in `nix-os.yaml` is JSON, which is a strict YAML subset. This allows validation with the Python standard library and no package installation. `scripts/reconcile-discord-profile-routes.py` previews the manifest's non-secret routes and applies them through Hermes' supported config CLI; it refuses to overwrite unexpected existing routes.

## Safe start

```bash
python3 scripts/verify.py
python3 -m unittest discover -s tests -v
bash scripts/bootstrap.sh          # preview only
bash scripts/bootstrap.sh --apply  # explicit local mutation
python3 scripts/install-skills.py --profile lnx --nixkit /path/to/nixkit
python3 scripts/setup-skill-scouts.py  # preview only
scripts/reconcile-hermes-default.sh    # preview config/runtime reconciliation
```

The apply mode creates only missing named profile directories with reviewed role contracts, then uses Hermes CLI for missing boards and projects. It deliberately avoids `hermes profile create`, because that command initializes unrelated active-profile state. Existing profile identity and configuration are preserved; only the requested missing profile directories, boards, and project records are added. The script never reads or writes credentials, memories, sessions, transcripts, or copied runtime data.

After apply, configure Discord manually for the default profile as printed by the script. Keep the bot credential in Hermes' interactive secret setup, never in this repository.

The shared LNX bot's three project-channel routes are declared in `nix-os.yaml`. Preview and apply them with:

```bash
python3 scripts/reconcile-discord-profile-routes.py
python3 scripts/reconcile-discord-profile-routes.py --apply
```

After an apply, restart the active Hermes gateway and verify the three routes with `hermes config get gateway.profile_routes`; other Discord channels and DMs remain on LNX/default. The script refuses to replace any unexpected pre-existing route set.

## GitHub webhook route preview

`automation/monitoring/github-webhook-routes.json` binds each project to its governed repository from `automation/monitoring/policy.json`, its Lead profile from `nix-os.yaml`, and a project-specific bounded stdin adapter. Preview only the native Hermes agent-mode subscription argv with `python3 scripts/preview-github-webhook-routes.py --preview`; the command never executes subscriptions. The wrappers in `scripts/` are repository sources, not directly runnable Hermes script paths. A future, separately authorized staging step must copy these exact sources under `$HERMES_HOME/scripts` (Hermes resolves relative `--script` paths from that directory):

| Source | Target beneath `$HERMES_HOME/scripts/` |
| --- | --- |
| `scripts/github-webhook-pulse.py` | `nix-github-webhooks/github-webhook-pulse.py` |
| `scripts/github-webhook-algua.py` | `nix-github-webhooks/github-webhook-algua.py` |
| `scripts/github-webhook-meabeclick.py` | `nix-github-webhooks/github-webhook-meabeclick.py` |
| `automation/monitoring/github_webhook.py` | `github_webhook.py` |

Inspect this map without writing files using `python3 scripts/preview-github-webhook-routes.py --staging-plan`. Staged wrappers import the sibling `github_webhook.py`; when run from this repository they import the package module instead. This repository change performs no staging or activation. The wrapper infers only check-run, review, or pull-request event families from payload shape because Hermes supplies JSON stdin, not GitHub headers. Native durable admission suppresses delivery retries, but this is not exactly-once Lead action; each Lead must reread canonical Kanban and live exact-head PR state. No subscriptions, secrets, public ingress/listener, or runtime changes are part of this repository contract. A separate public-ingress/listener/secret setup and live readback gate is required.

## Hermes default-profile maintenance

`scripts/reconcile-hermes-default.sh` declares the non-secret settings that
must survive Hermes config migration: manual approvals, denied cron approvals,
delegation concurrency 3, the existing 50-iteration child budget, and the
existing full tool/background progress display. It removes the retired
`display.tool_progress_overrides` and `delegation.max_async_children` keys by
supported Hermes CLI calls rather than copying a live config file.

The command is preview-first. Apply mode asks Hermes to create its built-in
pre-update backup outside this repository, runs the supported updater, reads
back the post-update version, reconciles and verifies every required default-
profile setting through typed JSON reads, and then runs `config check`,
`doctor`, and the real gateway status command:

```bash
scripts/reconcile-hermes-default.sh
scripts/reconcile-hermes-default.sh --apply
```

Apply fails closed if `hermes --version` reports carried local commits or any
unrecognized carried-state marker. Review and merge that fork deliberately
first; the maintenance script never replaces local history or commits runtime
state to this repository. If apply fails, the update or configuration may be
partial: correct the error and rerun, or restore the backup path printed by
Hermes only after stopping the gateway:

```bash
hermes gateway stop
hermes import <backup.zip>
```

Successful command exit does not promise warning-free diagnostics; review the
config-check, doctor, and gateway status output for warnings or degraded state.

## Governed role skills

`skills/packs.json` pins NixKit checkout `f5377ca3219daf4f33f6e8525f9438318dabcd2f` and audited `sources.json` SHA-256 `110ecc3872b4fe45ede667afd4ca757d59aef850672045a639eae5ef78415a83`, whose seven LNX skills retain source snapshot `da5f36fc4c470fa28ffdcab1a6c6aaa3750bded3`. It classifies all 57 source skills into explicit persistent role packs, task-only tools, or exclusions. There is no universal pack. The baseline and all named role packs are unchanged. `web-design-guidelines` and `find-docs` remain task-only because their pinned skill instructions fetch mutable latest dependencies at runtime. Caveman and Watch retain their receipt-backed LNX-only single-skill approvals.

The exact `product-development` pack remains approved for LNX as proposal `lnx-product-development-92338765`, with the seven LNX adaptations plus `writing-plans` and `subagent-driven-development` in a fixed order. Its historical receipt remains bound to proposal commit `083d008d3a83cd178e1f79bf962967ae7054fc0e`, proposal-file SHA-256 `06db117e1d84486a50f7beaae32f83fc3540ca8fd01eb5dc7fcf0f9e62100288`, and Lior's exact Discord approval message ID and timestamp; the NixKit pin advance does not rewrite that evidence.

The manifest retains 11 least-privilege, receipt-backed profile-skill assignments: LNX gets `todoist-cli`; Pulse gets `technical-project-management`; UX gets `rtl-design` and `web-accessibility`; Engineering gets `performance`, `web-accessibility`, `systematic-debugging`, and `test-driven-development`; Growth gets `analytics`, `customer-research`, and `social-media-content-calendar`. Algua and MeaBeclick Lead receive no additions. Each assignment keeps its immutable single-skill proposal and approval receipt.

`scripts/install-skills.py` verifies the exact clean NixKit revision, audited `sources.json`, and the working `install.mjs` bytes against both the pinned Git object and approved SHA-256 before preview or execution, including strict byte-, type-, mode-, symlink-, and file-set verification of the local `skills/watch` Git tree. It rejects symlinked profile homes, recursively inventories validated skill frontmatter, resolves the executable `npx` path once, prints the exact Node 24.22.1 Hermes subset command, revalidates immediately before execution, and mutates only with `--apply`. Proposed packs never enter the effective set; only baseline profile packs plus exact receipt-backed approved pack additions do. The exact `host_native_satisfaction` field preserves the existing categorized Hermes-native `nix-idea-to-mvp`, `writing-plans`, and `subagent-driven-development` trees only after their paths, entry types, permission modes, bytes, SHA-256 tree digests, unique names, and frontmatter names validate. The narrow corrective record at `skills/host-native-approvals/lnx-preserve-host-native-v1.json` binds Lior's exact Telegram quote `preserve and amend`, chat `6926015052`, profile `lnx`, and those exact three artifact records; it contains no fabricated message ID or timestamp and does not rewrite the historical product-pack receipt or create a general approval path. Those three remain effective product-development capabilities but are omitted from the NixKit command; every other effective selection, including `todoist-cli`, remains a normal install target. Product-development additionally fails closed unless the approval evidence matches the canonical Discord message ID `1563556252829622396` and timestamp `2026-10-24T14:14:20.145000+00:00`. Each declared single-skill addition must be covered exactly once by repeatable `--approval` receipts. See [Skill Governance](docs/SKILL-GOVERNANCE.md) for the digest encoding, candidate gates, promotion, migration, rollback, and private-checkout authentication.

## Knowledgebase integration proposal

[Knowledgebase integration](docs/KNOWLEDGEBASE-INTEGRATION.md) contains inert, original `nix-knowledge` and `nix-document-design` candidates and access/rollout gates. Nothing is installed or deployed; role-file pointers are templates only. The canonical company entry is <https://github.com/its-nix/knowledgebase/blob/main/START-HERE.md>, pending its separate setup review. Project truth stays with project owners.

## Weekly scouting

Seven role scouts run locally on staggered Saturday `Asia/Jerusalem` schedules. A default-profile aggregator validates all seven current-week packets, deterministically deduplicates qualifying candidates, and returns `[SILENT]` only for a complete valid week with nothing to report. Provisioning is preview-first:

```bash
python3 scripts/setup-skill-scouts.py
python3 scripts/setup-skill-scouts.py --apply                  # local canary delivery
python3 scripts/setup-skill-scouts.py --apply --enable-delivery # aggregator only, after canaries
```

Only the aggregator's second-stage delivery target is `discord`, which resolves to the configured Discord Home channel; Telegram gateway messaging is disabled. Runtime packets stay under `${HOME}/.local/share/hermes-scouts/` and never enter Git.

## Migration note

The previous Paperclip-based design remains preserved remotely at `archive/paperclip-os-2026-10-23`. This repository does not use Paperclip, n8n, Slack, a custom agent runtime, or one Discord bot per internal agent. The archive branch is historical evidence only and must not be modified by this bootstrap.

## Repository map

- `nix-os.yaml` — safe topology and authority manifest
- `profiles/*/ROLE.md` — reviewable role contracts installed locally as `SOUL.md` only for newly created profiles
- `config/hermes.example.yaml` — non-secret baseline settings
- `scripts/bootstrap.sh` — preview-first local bootstrap
- `scripts/install-skills.py` — pinned, exact-subset NixKit role installation
- `scripts/setup-skill-scouts.py` — preview-first per-profile cron reconciliation
- `scripts/reconcile-hermes-default.sh` — preview-first default-profile config/runtime reconciliation
- `scripts/reconcile-discord-profile-routes.py` — preview-first reconciliation for the three shared-bot project routes
- `skills/` — role packs and proposal/approval schemas
- `automation/skill-scout/` — declarative jobs, prompts, report schema, and collector
- `scripts/verify.py` — dependency-free repository safety verifier
- `docs/` — architecture, state, secret, and skill-governance boundaries
