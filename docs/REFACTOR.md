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
| D6 | 2026-11-23 | PRs in this refactor are built with the founder's admin token and reviewed by an independent model; this is **not** a forge-proof gate | The forge-proof gate (separate builder identity, ruleset requiring Actions check runs) is itself a deliverable of Phase 3. Still true on 2026-11-25: agents open PRs with the founder's identity ([AUTHORITY](AUTHORITY.md)) |
| D9 | 2026-11-25 | Daily recovery captures the whole `/srv/nix-clean` root cold (reviewer logins and App key, Twenty database, T3, Hermes, archived Paperclip); reviewer and Twenty credentials are included in the private image | Same class of native credential as the Hermes ones already held; excluding them would make a restore unusable and not lower the risk. Stated in `docs/DOCKERHUB-RECOVERY.md` |
| D10 | 2026-11-25 | Personal profile is built by the deploy under a staging name through native Hermes commands and renamed to `personal` only when complete, and only when absent (ownership proven by a root-only record, not by files in the profile); contract in `deploy/profiles/personal.json`, outside `native_inputs` | Same container means logical separation only; never overwrite an existing profile, never clone credentials ([contract](PERSONAL-PROFILE.md)) |
| D11 | 2026-11-23 | Merges after #73 that touched founder-owned paths, before the ruleset existed (from `git log fcd4150..main` matched against CODEOWNERS): **#76** (`.github/workflows/ci.yml`, `automation/recovery/*`, `deploy/compose.yaml`, `scripts/deploy-os.py`), **#77** and **#78** (`scripts/deploy-os.py`, adding to the distributed-skill list). Each had independent review and green CI. #80 (Paperclip retirement) was left for founder review and merged 2026-11-23 20:41 UTC | Bootstrap exception, stated openly; the founder may review those diffs after the fact |
| D12 | 2026-11-23 | The Venture Factory requires a non-empty approved PRD/GTM (#79) | Observed: a lead without them had no source-of-truth metric and blocked (improvement loop, before/after below) |
| D13 | 2026-11-23 | Unattended agent execution is effectively on (venture cron loops, Kanban dispatch, LNX loops), although `deploy/pilot.json` still says `unattended_execution: false` (checked on `main` 2026-11-25). That file is a hashed native input describing the retired pilot and is not edited | Safeguards: Hermes holds no outbound, payment or publishing credentials; the approval tool never executes; T3 is isolated from Hermes state and the Docker socket; pause/retire stop routines; health checks report by exception |
| D14 | 2026-11-25 | Hindsight runs as its own digest-pinned server container (`hindsight-api` 1.2.0) with the catalog plugin in `local_external` mode. Extraction uses `openai-codex` through a separate ChatGPT device login; embeddings are local (bge-small); the reranker is `rrf`; there is one bank per profile. Banks are an organizational separation, not a confidentiality boundary between Hermes profiles (one container, one uid); an API key keeps non-Hermes host processes out. Secrets are scrubbed by Hermes's native memory egress redaction, which the deploy proves against each new Hermes image. Supersedes the embedded-mode part of D8 | The founder chose the ChatGPT subscription: Hermes already runs on OpenAI, so no new third party sees text; what is new is durable storage (local Postgres and the recovery image). Per-profile keys would add no boundary, because every profile can read every key. Embedded mode cannot be pinned without forking the Hermes image. With no `hindsight-api` beside the plugin, the plugin falls back to `uvx hindsight-api@<embed version>` = `hindsight-api-slim[all]` (torch). That resolves at runtime, its cache lands in the Hermes home, and it runs inside Hermes's 2 GB cgroup. The plugin's own README says the server cannot be installed in Hermes's venv (protobuf/otel conflicts). A separate container gets its own memory and CPU caps and a digest pin |
| D15 | 2026-11-26 | Use the founder’s personal My Drive/Nix. A host-only token runs folder-rooted bisync for Workspace. Project the Git-reviewed KB one way | Founder chose one account. A personal OAuth token grants account-wide access. Host placement isolates it from the runtimes. [Setup](KNOWLEDGE-VAULT.md) |
| D16 | 2026-11-26 | Use the founder's own Todoist account for the `Nix` approval queue. Hermes submits through a local spool, and a host timer holds the account-wide credential. | The spool avoids an HTTP proxy and keeps the token outside Hermes and recovery images while capturing queue state. Todoist completion only records queue status. [Runbook and acceptance H](FOUNDER-APPROVAL-QUEUE.md) |
| D17 | 2026-11-26 | Reverse PR #109 before activation. Hermes uses the pinned Google Workspace CLI with the founder’s own Google account. Agents work in My Drive/Nix and reviewed policy stays in Git | Direct access gives the founder and Hermes one working copy with no sync job or second account. The account-wide grant is readable by every Hermes profile. [Setup](KNOWLEDGE-VAULT.md) |

## 4. Evidence ledger

| Date | Claim | Evidence |
| --- | --- | --- |
| 2026-11-23 | Recovery failure root cause | Read-only re-run of snapshot path checks on host: exactly one refusal, a Hermes-managed venv interpreter link into `/opt/hermes/`. Fixed in PR #69 |
| 2026-11-23 | Paperclip holds no live work | Native API readback: 0 issues/projects/routines; 3 paused agents |
| 2026-11-23 | Recovery restored | #69 deployed (`4079b26`); live walker 0 problems / 52,883 entries; verified publish `sha256:5f71ff69…` (52,866 entries, remote readback + isolated restore checks) at 18:38 UTC; bounded unit installed (2h/3 starts) and timer re-enabled |
| 2026-11-23 | T3-inclusive backup verified | Production receipt published at 20:12 UTC with 53,582 entries after remote readback and restore verification. T3, Hermes and Paperclip returned healthy. Recovery accepts T3’s graceful exit (#82) and preserves its empty ownership lock (#84). The deployed backup records failed Docker subcommands without private output (#85). Superseded as the newest recovery point by the 2026-11-24 03:26 UTC capture (row of 2026-11-25 below) |
| 2026-11-23 | T3 container isolation | Pinned v0.1.11 image: as uid 10010 no Docker socket, no Hermes/Paperclip state, CapEff 0; Hermes 18642 / dashboard 19119 / Paperclip 13100 closed on loopback, bridge gateway and public IP |
| 2026-11-23 | Hermes -> T3 full access | CLI-minted `mcp-client` tokens capped (`runtime_mode_escalation_denied`); OAuth+PKCE pairing-approved token launched a `full-access` thread |
| 2026-11-23 | Venture scaffold on real Hermes | Throwaway home, pinned 0.23.0: dry run writes nothing; create/rerun idempotent (1 mission, 2 loops active after commit); pause/resume |
| 2026-11-23 | T3 not recreated by unrelated deploys | Container `104c8d8b…` unchanged across the #74, #70 and #78 deploys |
| 2026-11-23 | Generic venture on production | `demo` created by the deployed Factory without source edits; gateway hot-added the profile; Kanban dispatched Mission 1 to the lead |
| 2026-11-23 | Independent progress without LNX | Run #1 blocked (missing PRD/GTM, 154 s). After the documents were supplied, run #2 completed (88 s): guide, 5 questions, charter metric/baseline/target/window, and a review card delegated to and completed by `reviewer` |
| 2026-11-23 | Improvement loop (F) | Observed failure → #79 (Factory requires PRD/GTM), independent review REQUEST_CHANGES → fixed → CI → merged → deployed. Metric: missions blocked for a missing decision package; expected 0. Revert = revert #79. On 2026-11-23 auto-merge was pending the reviewer key; on 2026-11-25 the `main-protection` ruleset is active and `allow_auto_merge` is true (GitHub API readback) |
| 2026-11-23 | Review gate, first run (historical) | First `pull_request_target` run on #77: "Reviewer credential is not configured; failing closed". That workflow was later removed; the reviewer now runs from `deploy/reviewer/`. On 2026-11-25 `independent-review` passed on #105 |
| 2026-11-23 | Company health loop | `scripts/nix-os/health.py` run in LNX returned silent (healthy); 3 cron jobs active; `nix/health.json` published by deploy |
| 2026-11-25 | Hindsight sizing on this VPS | Pinned `hindsight-api` 1.2.0 in throwaway containers with a 1-CPU cap and a mock LLM. Default `hs-rerank-1` reranker: killed while loading under a 1.5 GB limit. With bge-small and `rrf`: healthy in ~72 s; after a retain and a recall ~710 MiB anonymous memory, cgroup peak 1 GiB including page cache. Service capped at 1536 MiB / 1 CPU; the host had ~4.5 GB available |
| 2026-11-25 | Hindsight server without a login | `openai-codex` with no `auth.json` still starts healthy and logs the LLM as unavailable until a restart. The founder's login script therefore restarts it |
| 2026-11-25 | Plugin behaviour on the pinned Hermes | Throwaway Hermes 0.23.0 home. Plugins are per profile: a named profile needs its own `-p <profile> plugins install`. `install hindsight --ref f14cc1eb… --yes-deps --no-enable` installs that commit and its client dependencies (`hindsight_client`) with the plugin disabled and `memory.provider` empty; `config set memory.provider hindsight` then enables it. `--enable` gives the same end state. `memory off` writes an empty provider. Plugin auto-update is off. The plugin's only inputs are the memory manager's hand-offs (`sync_turn` takes user and assistant text only) |
| 2026-11-25 | Hermes scrubs memory hand-offs | Pinned Hermes 0.23.0 `agent/memory_manager.py`: every provider hand-off goes through `_redact_for_provider` → `redact_for_egress` (forced, fail-closed). `deploy/hindsight/redaction_probe.py` in a no-network throwaway of the pinned image prints `redaction-probe: ok`. In the live container, OpenAI/GitHub/AWS keys, a Bearer header, a JWT, a PEM key and `.env` password/API-key lines were masked; a password in plain prose was not (documented residual) |
| 2026-11-25 | Hindsight API key | Throwaway `hindsight-api` 1.2.0, no network, `ApiKeyTenantExtension`: `/health` 200 without a key; bank list 401 without or with a wrong key, 200 with it; `/health/llm` 404 unless `HINDSIGHT_API_ENABLE_BANK_LLM_HEALTH=true`, then 401 without the key and a per-operation status with it |
| 2026-11-25 | Memory port unreachable from T3 and reviewer | From `nixos-t3` and `nixos-reviewer`: host-loopback ports 18642/19119 and a loopback-published container port on another bridge network (T3's 3773, published like Hindsight's 18888) time out via the bridge gateway and the container address; `127.0.0.1` inside each container is refused. Only `nixos-hermes` uses the host network |
| 2026-11-23 | `hermes profile create` is safe for dynamic leads | Throwaway container with empty home on the pinned image: only a lock file is touched outside the new profile |
| 2026-11-25 | Last good recovery point and why 2026-11-25 failed | `last-success.json`: 2026-11-24 03:26 UTC, `sha256:2ba5b79e…`, 53,594 entries (predates the reviewer and Twenty). The 03:17 run and both retries failed at the first `docker inspect` with the `nixos-*` containers missing (the host auto-pruner incident); fixed by #93 (recreate missing containers, disk preflight), deployed. Timer enabled, next run 2026-11-26 03:17 UTC |
| 2026-11-25 | Capture coverage of every stateful service (read-only dry walk on the host, same path rules and link/special-file checks as `snapshot()`, nothing archived) | 62,772 durable paths, 0 refusals. Captured: reviewer 5,999 (home 5,992, `secrets/` incl. `app.pem` 0400, `reviewer.env`, `app/`), Twenty 2,356 (`db/` 2,345 incl. `PG_VERSION`, `redis/`, `storage/`, `db.env`, `server.env`, `ENABLED`), T3 home 912, Hermes 49,466, archived Paperclip 3,569. Skipped by design: T3 `work/` and worktrees/caches, reviewer `.npm/`, `.cache/`, `.codex/tmp/`, PostgreSQL `postmaster.pid`. The only `.sqlite` files that are not SQLite databases are the two already-exempt zero-byte locks |
| 2026-11-25 | DB consistency | Cold stop of all seven containers under the deploy lock, applications before databases (Twenty worker, server, db, redis; Hermes, T3, reviewer); databases start first afterwards. No hot copy or dump of PostgreSQL/Redis |
| 2026-11-25 | New code validated against the live host, read-only | `deployment_model` now cross-checks the `twenty-*` containers (image digest string-equals Compose, binds exactly the declared ones, no anonymous volumes): passes with the CRM enabled. The strengthened Twenty probe (opens in single-user mode with network off and finds the migrated `core` schema) passed on a throwaway copy of the live database files in a temp dir, deleted afterwards. twenty-server took about 4 minutes to first health on its first start, so the post-capture wait is now 10 minutes when the CRM is enabled (5 otherwise) |
| 2026-11-25 | Isolated restore of the latest published image (`sha256:2ba5b79e…`, 2026-11-24 capture), 40 GB free, 5 GB RAM available | `verify` and `restore --destination <fresh 0700 dir under /var/tmp>`, no network, no services: both passed, 53,594 entries, `compose.json` lists `hermes`, `t3`. Present: Hermes `config.yaml`, memories and the `engineer`/`reviewer` profiles, T3 `userdata`, `.codex`, `.claude`, archived Paperclip `PG_VERSION` and `secrets/`. Absent, as expected for a pre-reviewer, pre-CRM capture: `reviewer/`, `twenty/`. Throwaway directory and local image reference removed; disk back at 58% |
| 2026-11-25 | Found after #99 deployed: two defects that would have failed the 2026-11-26 run | (1) #100 layers a generated `packs.compose.json` over `compose.yaml` (extra read-only skill mounts on Hermes and T3); the capture's mount cross-check, container recreation and stop hook ignored it, so `deployment_model` refused ("native mounts differ") inside the cold window. Fixed in the next PR: all three now layer the override like the deploy. (2) `nixos-twenty-worker` crash-looped (Node `heap out of memory` at 768m, 128 restarts in about an hour), which makes the "all services running" precondition a coin flip; limit raised to 1536m like the server |
| 2026-11-26 | Daily capture failed all three attempts; no new recovery point (read-only host inspection, no live changes or cleanup) | Journal starts at 03:17:12, 03:35:24 and 03:52:42 UTC; all three failed. Last-attempt receipt: "Native services did not stop cleanly". No 2026-11-26 publication or digest, so the restore proof was skipped. 39.6 GB (decimal) free at inspection. Last successful capture is still 2026-11-24 03:26 UTC, 53,594 entries, `sha256:2ba5b79e86e666aef21e56a823f030ca319f70e9056858062c192ea9d59c4e48`. The Docker daemon journal records a Twenty server exit (`exitCode=2`) and restart during the first attempt; this does not prove which service failed the final clean-stop check. Proposed: include the service name and exit status in the refusal, and reproduce graceful shutdown on disposable data before changing any accepted exit codes |
| 2026-11-26 | Shutdown diagnosis from the recovery and Docker journals and per-container logs | Both Twenty apps logged queue shutdown completion before the PostgreSQL and Redis shutdowns on all three attempts. PostgreSQL logged a completed shutdown and Redis flushed its append-only file. In the first attempt, Twenty server hit “the database system is starting up” as PostgreSQL restarted and the daemon recorded the server’s exit 2 at 03:17:29 UTC. All seven containers had already restarted when inspected, with current exit codes of 0. The journal and retained Docker events could not establish the exit codes from the original cold stops. Hindsight was absent from these attempts. |
| 2026-11-26 | Reproduced and corrected the Twenty worker shutdown on fresh disposable state | Using the deployed image pins, the worker launched via `yarn worker:prod` processed jobs, logged queue shutdown completion, and exited 129 under `docker stop`. Running that script’s Node command directly under Docker init kept the queue shutdown completion and returned 143 when Nest re-raised SIGTERM. Twenty server exited 0 after draining and closing. PostgreSQL and Redis stopped after the apps and both exited 0. Isolated T3 completed shutdown reconciliation and exited 130. Isolated Hermes and the idle reviewer exited 0. Recovery now checks each exit before proceeding, names the container and exit code in refusals, and accepts non-zero exits only for T3 (130) and the Twenty worker (143). The next supervised capture will verify this on the deployed services. |
| open | Prove reviewer and Twenty in an actual image | Still outstanding: the 2026-11-26 run did not produce an image (row above). Needs a later daily run to succeed (first capture with both). Then run the "Prove a restore without touching live state" block in `docs/DOCKERHUB-RECOVERY.md`; expect `reviewer/home`, `reviewer/secrets/app.pem`, `twenty/db/PG_VERSION`, `twenty/ENABLED` present and `reviewer`/`twenty-*` in `compose.json` |

## 5. Blocked on founder action

One consolidated Todoist request (project `Nix`, label `nix-approval`), raised 2026-11-23. Status is as of 2026-11-25 where it was checked; otherwise it is as requested and not re-verified.
1. ~~`REVIEW_ANTHROPIC_API_KEY` Actions secret~~ → resolved by 2026-11-25: the `independent-review` check passed on #105, and the `main-protection` ruleset and auto-merge are active. The App key itself was not re-read.
2. Builder GitHub identity for T3 (contents + PR write; no workflows/admin/bypass). **Open:** agents still open PRs with the founder's identity ([AUTHORITY](AUTHORITY.md)).
3. Coding-harness logins inside T3. Open; not re-verified.
4. The founder chose ChatGPT for Hindsight extraction (D14). After #102 deploys, complete the [one-time device sign-in](../deploy/hindsight/README.md).
5. Direct Hermes Drive access awaits the founder’s one-time sign-in after deployment (D17).
6. Todoist API token for the agents' approval queue (`TODOIST_API_TOKEN` in Hermes). Open; not re-verified.
7. Twenty first admin + per-venture workspace and role-scoped API key, when a venture needs CRM. Open (CRM is off).
8. Personal profile email and calendar connectors. Open.

Removed as obsolete on 2026-11-25: the request to allow `pull_request_target` for `independent-review.yml` (that workflow was removed from `main`), and the review of #80 (merged 2026-11-23 20:41 UTC).

## 6. Next independently executable steps

- **Ruleset and auto-merge: done (2026-11-25).** Still open: run one agent PR end to end through T3, after the builder identity (§5 item 2) exists.
- **After harness logins and the builder identity:** a venture lead launches a T3 task through `t3-delegate`; two concurrent tasks on separate worktrees; a red CI proves merge blocking (acceptance D).
- **After a Twenty key and a Todoist token:** run `crm-lead-ops` live for the `demo` venture with a fictitious lead; the approval appears in Todoist; nothing is sent (acceptance E/H).
- **Hindsight:** After #102 deploys and the founder signs in, verify extraction and recall with synthetic text in a test bank on the pinned server (D14).
- **Drive (D17):** after founder sign-in, verify a Markdown round trip inside Nix and open a native Google document from an index note (D15).
- **NixKit packs and Skillshare:** core and engineering packs are deployed through Skillshare (#100, merged 2026-11-25). Remaining: a native discovery readback on production after a healthy `main` deployment (see the pack migration evidence below).

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
