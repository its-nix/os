# Knowledgebase integration — inert proposal

**Not approved, not installed, and not deployed.** This OS change contains original first-party skill candidates, role-template pointers, and static contract tests only. It changes no live identity, privileges, skills, config, memory, session, cron, credential, installer, or approval receipt. KB setup lives in a separate repository/branch; nothing here promotes its unmerged contents.

Canonical entry: <https://github.com/its-nix/knowledgebase/blob/main/START-HERE.md>.
Contribution contract: <https://github.com/its-nix/knowledgebase/blob/main/AGENTS.md>, followed by relevant `_meta/` policy. The new repository README is still bootstrap while setup is unmerged: do not interpret unavailable START-HERE/AGENTS as a working deployed KB, fall back to historical experiments, or invent company facts.

## Ownership and supported discovery

GitHub Markdown is company knowledge; Obsidian views the same files. LNX curates portfolio context and coordinates escalation. Leads own project technical truth and propose cross-venture learning; specialists receive minimum relevant authorized context from their Lead and return company proposals through that Lead. Existing authority and project-specific privacy/financial gates remain unchanged. Tasks stay in Hermes Kanban, with Todoist only for Lior's commitments. Native Hermes memory/session continuity is not an approved knowledge store; compaction is not validated ingestion.

Project truth stays in [Pulse vision](https://github.com/its-nix/pulse/blob/main/docs/VISION.md), [Algua KB](https://github.com/its-nix/algua/blob/main/kb/README.md), and [MeaBeclick product](https://github.com/its-nix/meabeclick/blob/main/PRODUCT.md). These are pointers, not permission to mirror content. The exact Pulse-specific KB URL remains unresolved; do not invent a `pulse-kb` location.

The preflight inspected Hermes v0.22.5+1714.g9c36150, source `9c361506de2d642874c0b8e00bf34a2da0f676a3`; its checkout had unrelated dirty gateway files, so running-process equivalence was not established. Re-audit before deployment. Official references: [context files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files), [skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [security](https://hermes-agent.nousresearch.com/docs/user-guide/security).

Verified preflight behavior: project AGENTS follows git-root-to-cwd, unless a higher-priority `.hermes.md`/`HERMES.md` context type wins; it is not universal out-of-repository discovery. Skills use compact metadata and on-demand bodies/references. An inert source tree in OS is not in a live skill list. `ROLE.md` edits are reviewable templates, not live SOUL edits; bootstrap preserves existing profiles. Do not rerun bootstrap to activate these pointers.

Before activation, a bounded lead/Discord mandate may explicitly carry the entry URL and authorized task scope. This is supplied task context and does **not** prove persistent fresh Discord discovery. Prefer future separately approved `nix-knowledge` skill metadata for discovery. No SOUL reconciler, USER/MEMORY edit, external skill directory, project-root trust change, plugin, cwd change, or global installation is proposed here. Any genuinely needed identity pointer mutator requires a separate approved design preserving unrelated bytes, conflict checks, secure backup and rollback.

## Profile-specific proposal packets (non-executable)

These are review packets, **not valid installer JSON or approval receipts**. There is no fabricated passing score, timestamp, message ID, catalog revision, or installation authority.

| Candidate | Target and tier | Need and scope | Gate status |
| --- | --- | --- | --- |
| `skills/nix-knowledge/` | Governed `lnx`, live `default`; persistent candidate only | Out-of-checkout portfolio retrieval, evidence-backed capture/reconcile/curation | Source commit/inventory available after local commit; immutable catalog admission, effective-set clearance, authenticated exact approval and receipt pending |
| `skills/nix-document-design/` | LNX/default bounded document task; task-only initially | Portable editorial hierarchy, accessible diagrams and render checks | No persistent assignment requested; task authorization and representative rendering/safety tests pending |

Leads and specialists receive bounded context initially, not blanket persistent skills. Approval for default does not authorize any other profile. Both packages are Markdown-only original writing with MIT frontmatter; no executable, fetch hook, runtime dependency, service, new credential, or automatic network request. Reading authorized GitHub sources and editing an approved branch use existing tools only under the task's authority; no publication or promotion follows from loading a skill. Existing handoff/verification/design skills may already satisfy some needs: non-duplication remains an admission gate, not assumed PASS.

The current installer catalogs neither candidate. Admission requires a separately reviewed immutable catalog revision (prefer the normal NixKit catalog path), source/license evidence, inventory and package digest, then coordinated pin/catalog/verifier/test changes. Do not add these names to `packs.json` alone, copy them into live trees, or bypass governance via `skill_manage`, a hub installer, symlink, or `skills.external_dirs`. Original candidate files below `skills/` are not a deployment source configured for Hermes.

Use the actual seven hard gates in [Skill Governance](SKILL-GOVERNANCE.md): relevance, provenance, reliability, safety/privacy, license, Hermes compatibility, non-duplication. Passing evidence and the exact rubric sum must reach **80/100**: role value 30, evidence quality 20, security/provenance 20, Hermes compatibility 15, maintenance 10, documentation/license 5. Do not reuse a generic scoring rubric or score an untested candidate as admitted.

After admission, the single-skill process is proposal/manifest commit → genuine human-authenticated exact-revision/profile approval → later receipt binding that prior commit → complete preview → separately authorized apply. Single-skill receipt field `approved_by: Lior` does not independently authenticate a human message; verify actual approval out of band. Pack-specific authenticated approval rules do not make a new knowledge pack preapproved. Existing host-native exceptions are limited to their exact three artifacts, not a general workaround.

## Pinned upstream review and limitations

Assessed [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills/tree/3ccff5338ea700537839b21900aa5358a0402c98) at exact `3ccff5338ea700537839b21900aa5358a0402c98`: selected Markdown/Bases closures are six documentation files, with no packaged executable. MIT permits reuse but requires full notice inclusion for copied substantial portions; upstream notice is `Copyright (c) 2026 Steph Ango (@kepano)`. These candidates use original text, not upstream copying. See [portable design provenance](../skills/nix-document-design/references/portable-markdown.md) for notice obligations and source links.

Static format/security review is not maintainer-signature authentication, native scanner acceptance, installed Hermes compatibility, transitive acquisition review, or Obsidian visual proof. The signature key was unavailable. Root notice propagation through the acquisition/copy chain was not exercised; a URL alone is insufficient if text is later vendored. Bases remains optional; adjacent CLI/fetch/generation tools are excluded. `obsidian-markdown` and `qmd` remain task-only, unchanged.

## Actual full effective-set preview evidence

Read-only previews were executed from this OS branch using an independent clean NixKit checkout at the canonical pin `f5377ca3219daf4f33f6e8525f9438318dabcd2f`, not the drifted working Kit checkout. For each profile, commands supplied **all** `approved_additions` as repeatable `--skill` and all matching canonical single-skill `--approval` receipts; the wrapper also includes baseline/role and receipt-backed pack additions. No `--apply` was passed. The wrapper prints a downstream `--apply` command but does not execute it during preview.

| Profile | Result | Meaning |
| --- | --- | --- |
| lnx/default | **exit 2** | `host-native skill tree digest mismatch: writing-plans at software-development/writing-plans` |
| pulse | exit 0 | Complete current governed selection preview accepted |
| algua | exit 0 | Complete current governed selection preview accepted |
| meabeclick | exit 0 | Complete current governed selection preview accepted |
| meabeclickux | exit 0 | Complete current governed selection preview accepted |
| meabeclickeng | exit 0 | Complete current governed selection preview accepted |
| meabeclickgrowth | exit 0 | Complete current governed selection preview accepted |

This exposes a deeper blocker than the earlier Kit HEAD mismatch. LNX's mismatch was not repaired, rehashed into policy, or overridden. Later validations/destination collisions may still be blocked behind that failure. Six successful current-set previews do not include uncataloged candidates, fetch their bytes, verify downstream no-overwrite behavior, or authorize activation. The sole persistent proposal target, default, remains blocked. Rerun complete previews after reviewed resolution and immutable candidate admission; never force only the easy new addition.

Portable reproduction from OS root, with `NIXKIT_CHECKOUT` explicitly set to a separate clean checkout at the pin (do not reset a live checkout):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import json, os, pathlib, subprocess
root = pathlib.Path.cwd()
manifest = json.loads((root / 'skills/packs.json').read_text())
for profile in manifest['profiles']:
    command = ['python3', 'scripts/install-skills.py', '--profile', profile,
               '--nixkit', os.environ['NIXKIT_CHECKOUT']]
    for skill in manifest['approved_additions'][profile]:
        command += ['--skill', skill]
    for path in sorted((root / 'skills/approvals').glob('*.json')):
        if json.loads(path.read_text()).get('profile') == profile:
            command += ['--approval', str(path.relative_to(root))]
    result = subprocess.run(command, check=False)
    print(profile, 'exit', result.returncode)
PY
```

## Access findings: procedural boundaries are not security

Preflight measured all seven profile homes owned by the **same UID**, with mode 0700. The process could traverse other profiles and read their DB paths by metadata permission checks; no private content was needed for that finding. `session_search` also explicitly accepts another profile and opens its DB read-only: profile-name validation is not role authorization. Manual approvals and separate state routing do not revoke filesystem, browser, MCP, network, index, or credential access.

Organization readback showed **base permission write**, plan **free**. Private visibility does not prevent inherited organization-member write/read. A lower repository grant cannot subtract that floor; all repo readers see committed files and history. Folder names, frontmatter, CODEOWNERS, CI, PR instructions, and local permissions under the same owner do not implement per-note ACLs. Private required-review protection was not established; GitHub Free organizations generally need Team/Enterprise for that enforcement. Existing OS governance was also partly procedural, not proof of server enforcement. Do not make the KB public to get free protection.

Keep restricted data out. Required access/security acceptance remains blocked pending separate permission/deployment decisions, not fixed by this docs patch. Choices requiring approval include org-wide base-permission changes with scoped grants (affects other repos), paid enforcement evaluation (spending), repo rules/bypass restrictions, and a real runtime isolation pilot. An all-org-readable/writable non-sensitive pilot with procedural review must be explicitly accepted as residual risk; it does not pass enforced approval or negative-access criteria.

### Staged real isolation pilot — not implemented

1. Obtain deployment/access approval. Provision a distinct unprivileged principal with no sudo/docker membership, host SSH agent, shared admin GitHub session, Docker socket, or host-home access.
2. Run the whole agent and **all tools**, MCP/search/index processes under that principal, not just terminal. Use an unprivileged container or filesystem namespace with narrow approved read-only company mounts and a separate writable task workspace; inspect all mount and credential fallbacks.
3. Supply isolated Hermes state and minimal provider credentials; repo-scoped read access for retrieval, separately scoped proposal rights if approved, never approval/merge authority. No shared session DB, cache/index of restricted material, or inherited host credentials.
4. From the actual isolated worker, test allowed company retrieval and denial of synthetic unrelated-project sentinels, profile memory/DB paths, symlink escapes, direct Python/shell/file-tool reads, explicit cross-profile `session_search`, browser/MCP routes, and unauthorized GitHub repo requests. Capture exact service identity/scopes and denial evidence without opening protected real records.
5. Route one separately approved task only after those tests. No estate-wide isolation claim: same-UID existing workers remain unenforced. Synthetic fixtures alone do not prove deployed access controls.

## Finite acceptance and rollout gates

| Criterion | Evidence or disposition in this OS proposal |
| --- | --- |
| Canonical amended coordinate, original bounded candidates, role routing | Static contract tests and review; docs-only payload |
| Native/live state and governance preserved | Activation inputs unchanged against base; no live mutator invoked |
| Full current desired-set previews | Actual table above; LNX blocked, others accepted at wrapper preview only |
| Provenance, contradiction, validity, concurrency behavior | Contribution instructions delivered; operational tests belong to separate KB implementation, not claimed here |
| Fresh LNX company-note retrieval and citations outside checkout | **Pending** immutable admission, genuine approval, deployment and new Discord-session test |
| Authorized lead retrieval with unrelated data denied | **Blocked** actual principal/tool/credential isolation and GitHub access decisions |
| Cross-venture contribution PR | **Pending** KB review and separate publication authority; no external write here |
| Obsidian/GitHub visual quality and baseline retrieval questions | **Pending** representative viewer inspection and KB question results |
| Skill scan, install byte/reference inventory, idempotence, non-target preservation | **Pending** separately approved isolated smoke/install and rollout |

Local source verification: run `python3 -m unittest discover -s tests -v`, `python3 scripts/verify.py`, shell syntax checks and `git diff --check`. These prove repository contracts, not deployed discovery/security. Local commit SHA plus Git-tree/archive inventory/digests can bind these source candidates for review; they are not catalog admission or a NixKit install receipt. Keep machine paths and runtime evidence outside tracked files.

Before promotion: resolve LNX host-native drift through its own review, admit immutable sources, verify full previews, obtain authenticated exact approval, create only genuine later receipts, perform isolated smoke/scanner/render tests, then separately authorize profile-local apply. Read back installed bytes and references, verify non-target profiles, idempotence and rollback, and run a new Discord session outside the checkout without injecting the entry URL to prove persistent discovery. Do not reset existing sessions to manufacture the result.

Rollback now is a Git revert of inert sources/templates; there is no runtime rollback because none was changed. A future deployment needs an exact-owned-copy removal/reversion plan, preserved prior artifacts and independent readback; no broad recursive cleanup. OS/KB merge, push, access changes and persistent activation remain separate authorities.
