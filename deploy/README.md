# Nix OS deployment

The Compose project is `nixos`, with containers `nixos-hermes` and `nixos-paperclip`. Official image repositories and digests are pinned in `versions.json`.

The target architecture is [Clean company OS](../docs/CLEAN-COMPANY-OS.md). This directory deploys fresh official images and native state. The previous OS manifest/installer is historical and is not used by this stack. Shared Codex login and Discord connectivity are verified. All controller agents remain paused and heartbeat-disabled until full delegated-work acceptance.

Ongoing delivery, the dedicated `nixos` operator and provider-login commands are documented in [Tracked VPS delivery](../docs/OS-DELIVERY.md). Main-only CI/CD preserves these native homes and records every successful deployment revision.

## Inputs and native ownership

`versions.json` pins the official images, their source revisions, company KB, Kit and Agency role sources. `pilot.json` maps each controller identity to its exact Hermes profile and selected skills, declares the shared model defaults, and assigns Discord to the main profile only. Fresh preparation writes these native non-secret settings; credentials are configured separately. Shared workflow skills include the upstream prerequisites; role-specific selections add focused expertise. The Agency references are Project Shepherd, Minimal Change Engineer and Code Reviewer. Role bodies are hash-checked and combined with the small Nix boundary files; their MIT notice is installed into the native home.

| Role | Hermes home inside container | Adapter base URL |
| --- | --- | --- |
| Delivery Lead | `/opt/data` | `http://127.0.0.1:18642/p/default` |
| Engineer | `/opt/data/profiles/engineer` | `http://127.0.0.1:18642/p/engineer` |
| Independent Reviewer | `/opt/data/profiles/reviewer` | `http://127.0.0.1:18642/p/reviewer` |

The native Bot Group `nix-delivery-pilot` contains these same profiles. Paperclip agents use `hermes_gateway`, issue-scoped sessions and native secret references to their distinct profile API keys. Do not copy keys into this repository or shell arguments. The controller owns work state; group sessions are separate conversations and must retain links to the same work item.

## Prepare and start

Run the preparation script on the VPS from a reviewed source copy:

```bash
python3 scripts/prepare-clean-os.py --root /srv/nix-clean
python3 scripts/prepare-clean-os.py --root /srv/nix-clean --apply
```

Apply refuses an existing destination, including an empty one. It creates fresh native homes, role instructions, distinct API keys and a private deployment env file. It downloads only the pinned public Agency source and checks its hashes. It does not start a service, authenticate a provider, install a bot token or import previous state. Never rerun apply to update an existing installation; preserve native state and review upgrades separately.

Before starting:

1. Place an immutable archive of the pinned company KB at `/srv/nix-clean/knowledge/company`. An operator can transfer `git archive <pinned-KB-commit>` over SSH. No GitHub credential or repository history is needed by the workers. The mount is read-only; future refreshes are operator-reviewed snapshot replacements.
2. Provide the pinned Kit checkout at `/srv/nix-clean/inputs/kit`. A private Git bundle from the authorized operator checkout works without copying its login: create the bundle from `main`, transfer it, clone the bundle on the VPS and check out the exact pinned commit. Verify `git rev-parse HEAD` against `versions.json`.
3. Before installing selected skills, run the native `hermes -p <profile> skills opt-out --remove --yes` for each of `default`, `engineer`, `reviewer`, with the fresh home mounted at `/opt/data`. This disables optional bundled-skill seeding and removes pristine bundled copies; the declared role sets replace them. Hermes still restores its core `hermes-agent` skill in the default profile on startup. It avoids a same-tier collision between the bundled and Kit `requesting-code-review` packages. Do this before Kit installation, since native removal can also remove an identical selected package.
4. Use the existing Kit installer in a one-off official Hermes container, before first startup. Mount the dedicated VPS Kit checkout at `/kit` writable (the installer takes a source lock) and the fresh Hermes home at `/opt/data`; invoke `node /kit/install.mjs --apply --skills --agent hermes --prefix <profile-home>` with one repeatable `--skill` for the union of `shared_kit_skills` and that role's `kit_skills`. Native Hermes installation copies the selected packages. Preserve licenses and the installer's integrity checks. Never mount the operator's login or SSH agent.
5. Create the KB's isolated dependency environment using the bundled Python: `/opt/hermes/.venv/bin/python -m venv /opt/data/nix/kb/.venv`, then its Python `-m pip install -r /knowledge/company/requirements.txt`. These are the KB's existing three pinned dependencies; the official image environment is unchanged. Recreate this environment after an image/interpreter upgrade.

Start the native entrypoints:

```bash
docker compose --env-file /srv/nix-clean/deployment.env -f deploy/compose.yaml config --quiet
docker compose --env-file /srv/nix-clean/deployment.env -f deploy/compose.yaml up -d --wait
```

The official image performs native ownership/setup and supervision. No custom entrypoint, upstream patch, messaging bridge, Docker socket or old deployment bind is used. This Compose file targets a Linux VPS: host networking is intentional. Listeners must be exactly loopback on ports 18642, 19119 and 13100. Shared host networking is not a network sandbox; the pilot assumes one trusted operator and non-sensitive company content.

Operator access:

```bash
ssh -N -l nixos -L 13100:127.0.0.1:13100 -L 19119:127.0.0.1:19119 vps
```

Paperclip is at `http://127.0.0.1:13100`; the native Hermes UI/backend is at `http://127.0.0.1:19119`. Local-trusted controller access is intended only through this SSH/loopback boundary. Do not forward either listener publicly. Keep its native state and signing keys on restart.

Use native Paperclip onboarding/API to create the pilot company and three agents. Store each profile gateway key in the native controller secret store, set its adapter to the URL above, disable heartbeat/wake-on-demand and pause it. Do not create a second agent runtime. Use native Hermes Bot Groups to create `Delivery pilot` with profile members `default`, `engineer`, `reviewer`; the server's group methods support explicit `member_id`, `profile` and `handle`. Do not start a discussion before model login. For reproducibility, native configuration is declared in `pilot.json`; it is not a custom scheduler.

## Knowledge in everyday work

The KB entry is <https://github.com/its-nix/knowledgebase/blob/main/START-HERE.md>. Follow its actual admission/schema/lifecycle rules. Retrieve using its own environment, with the pinned published ref:

```bash
/opt/data/nix/kb/.venv/bin/python /knowledge/company/scripts/retrieve.py \
  'organizational knowledge' --ref YOUR_PINNED_KB_COMMIT
/opt/data/nix/kb/.venv/bin/python /knowledge/company/scripts/retrieve.py --curate
```

Preparation embeds the exact local paths, retrieval/curation commands and pinned citation ref in every worker's SOUL, with non-secret pins at `/opt/data/nix/versions.json`. These commands run inside the Hermes container. Results are private company context: do not print note bodies into public CI or operator reports. Current results must carry source/authority links. Zero matches means no approved evidence found. The current lexical engine does not infer semantic matches or truth.

Every significant outcome reports its knowledge-impact disposition on the originating Paperclip work item. The lead routes a focused company proposal through the curator and applicable human review. Proposal writes use a separately scoped working copy/credential later; the worker's read-only snapshot cannot publish or merge. Drive artifacts and project technical truth remain linked, not mirrored. The [companion KB PR](https://github.com/its-nix/knowledgebase/pull/5) updating task-plane references is merged. The deployed snapshot remains the separately pinned revision; refresh it explicitly after review.

## Verification and activation

For each installation, verify:

- Official versions, native health, exact loopback listeners and absence of old data mounts.
- All three profile endpoints accept their own key and reject another profile's key.
- Native group driver readiness and exact member readback; same room and paused controller identities survive full container recreation.
- Native `commands.catalog` discovery and `skill_view` loads include every declared role skill, with only the native core `hermes-agent` extra in the default profile, the KB mount is read-only, and the KB's own tests/retrieval/curation run under the actual agent UID.

The main profile has a working Codex login, and all three profiles have returned live model responses through the shared credential. Keep token files in the native root store. Next, unpause only the bounded acceptance task: specification → engineer → independent review, group consultation and knowledge-impact handoff. Test the actual Todoist, GitHub and Drive connector permissions before claiming external integration. Treat future KB changes as reviewed proposals; no metadata field authenticates approval.

Full native Hermes/Paperclip recovery, including learned skills, group state, database keys and pinned inputs, is implemented in [Daily private OS recovery](../docs/DOCKERHUB-RECOVERY.md). It requires the operator’s native Docker Hub login, an explicitly private personal repository and a successful isolated remote restore before scheduling. No additional backup encryption is requested.

Stop only this project with the same Compose file and env file. It preserves both native homes. Do not remove volumes/state or revive the previous VPS stack as a rollback.

## Observed staging evidence — 2026-11-08

Official Hermes 0.23.0 and Paperclip 2026.1106.0 started healthy on fresh native homes. All three profile capability routes returned 200 for their own key and 401 for another profile's key. The native Group driver reported ready and its exact three-member room survived full container recreation, as did all three paused controller identities and native secret references. After native bundled-skill opt-out and selected Kit installation, profile-scoped `commands.catalog` discovery returned all declared 15/14/15 skills without warnings. After another container recreation, the same selected packages remained loadable, with only the native core `hermes-agent` extra in the default profile. Every selected package loaded successfully through native `skill_view` under UID 10000; the duplicate review-skill collision was resolved without changing upstream code. KB tests passed 27/27 inside the runtime; UID 10000 retrieval returned a sourced current result with the pinned immutable citation. The KB mount was read-only and all three listeners were loopback-only. No model turn or external connector acceptance was performed.

Independent source review found a missing worker-local KB command/ref, which was reproduced and fixed with a preparation regression. The source-owned knowledge section was updated and read back in all three staged profiles. The old deployment remains stopped and no old state is mounted. Full Linux OS validation ran 258 tests with two optional checks skipped, plus the repository verifier and existing syntax/bootstrap/scout checks.

Known upstream limitation: the pinned official Hermes image returns HTTP 500 from `/p/<profile>/v1/skills`. Its API handler calls `find_all_skills(..., include_editorial=True)`, but the native finder does not accept that keyword. Native UI/RPC discovery and actual skill loading passed independently; the runtime is unpatched. Any client that depends on this HTTP enumeration route remains blocked until a compatible official release is pinned and retested. Model-backed Paperclip execution remains a separate acceptance gate; CLI connectivity probes do not establish the controller handoff.

## Current verification — 2026-11-09

- The Compose project is `nixos`; both official containers are healthy. SSH/CI uses the `nixos` operator, with verified main-only deployments and daily/manual reconciliation.
- `default`, `engineer` and `reviewer` use `openai-codex` / `gpt-6.1-terra`. One main-profile login supplies native fallback credentials. Each profile returned a live model response; no token files were copied.
- The existing LNX Discord bot has a valid credential and required intents, and the native gateway reports Discord and API connected. Mentions are required, allow-all access is off, and the allowlist/home channel are configured privately. Both specialists have Discord explicitly disabled.
- Paperclip retains the three profile-matched identities, native secret references, disabled heartbeat/wake-on-demand and paused status. The native Bot Group uses those same profiles.
- The running SOULs contain the current Nix role boundaries, Agency references and KB instructions. Old configuration, context, memories and sessions were not bulk imported. A full audit of reusable old company/project context is still pending.

Recovery update — 2026-11-10: the first private Docker Hub snapshot passed isolated Hermes-plus-Paperclip restore checks after remote digest readback. The daily 03:17 UTC timer is enabled; [Daily private OS recovery](../docs/DOCKERHUB-RECOVERY.md) records the destination, verified digest and restore procedure.

Remaining gates: an actual Paperclip-led delegated task with group consultation and knowledge-impact review, and live Todoist/GitHub/Drive permission checks. The pinned KB snapshot and historical image/skill evidence above retain their explicit revisions and dates.
