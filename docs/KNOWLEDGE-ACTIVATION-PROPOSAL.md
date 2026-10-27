# Default-only knowledge activation — pending immutable proposal

**Not approved, not installed, not an authorized production preview.** This proposal changes catalog pins and the declared single-skill proposal set, not live policy authority. The `approved_additions` field name is an existing lifecycle convention; without the later authenticated receipt it grants no apply authority.

## Exact decision scope

Review this proposal commit and its tracked `skills/activation-proposals/lnx-knowledge-v2.json` plus `skills/proposals/lnx-nix-knowledge-1182c51c.json`. Any approval must name the full immutable proposal commit, `lnx-knowledge-v2`, LNX/governed `lnx` → Hermes `default`, and these exact artifacts/actions. A later receipt must reference that earlier ancestor commit; it cannot refer to its own containing commit. The current request authorizes preparation, not activation. No receipt, approver identity claim, invented message ID, or approval timestamp has been created.

- Persistent addition: **only nix-knowledge**, original `its-nix/os@1182c51c33ccb8a6c3b095bf551864fc5cb061c6`, path `skills/nix-knowledge`, same-revision package-scoped MIT LICENSE.
- `nix-document-design` remains task-only and outside every persistent assignment.
- Kit pin: `10018867c2544948109ce46217a689e1db2a1495`; source manifest `a23b87ae26784ac9571da5e7047f054d01ab3435b295c77b6c289bf5e4a3ec49`; unchanged installer `9748b8338c88c766208fa54fa144c95f0cd282a72f95327f485641ce85c18041`. Exact commit API readback confirmed publication in Kit PR6. Source PR45 and Kit PR6 remain unmerged; no hosted Kit CI claim here.
- All 59 catalog names classified; both new names task-only catalog eligibility. Other six profile assignments and additions remain unchanged.
- No overwrite/downgrade of working host skills, no copying of local canon into Kit, no hooks, external directories, pointers, permission changes, SOUL/config/memory/session/credential writes, or bootstrap rerun.

## Proposed v2 preservation (not authorization)

The historical v1 authorization, proposal/pack receipts and their original source bindings remain byte-for-byte unchanged. The old Telegram quote does not approve changed bytes. V2 proposes these **non-reusable profile/name/lexical-path/tree** bindings only:

| Skill | Relative path under default skills | Canonical host-native-v1 tree SHA-256 |
| --- | --- | --- |
| `nix-idea-to-mvp` | `product-development/nix-idea-to-mvp` | `d0594a11fc1ae3e7b852e23f0ea8be40c3273feea903c25a8300e68a2b86db9e` |
| `writing-plans` | `software-development/writing-plans` | `28a30bef5c4d6539791929f4c0e66eb92e47af3253fac6e5e43d72bc0381d2cd` |
| `subagent-driven-development` | `software-development/subagent-driven-development` | `e6f083d602c635d8de51ccd5dd0d202cf41e385705cc7a61ead83d05fd17b54f` |
| `lnx-grilling` | `lnx-grilling` | `d332fd9af0fb60002b5c3a5f7da1b079ce12ac08a44c453cc7b2f08481fbfaed` |
| `todoist-cli` | `todoist-cli` | `3f2599f5c04661d017e70e4f155068c9a1bdf4b32361d6a0fe2b9a0993b035b4` |

The nix-idea-to-mvp and subagent-driven-development bindings are unchanged. Writing-plans retains current safety/workflow learning; lnx-grilling and todoist-cli preserve their current learned guidance rather than replacing it with pinned older source. These proposed bindings are used only by the separately labeled diagnostic, never by the production wrapper as authorization.

## Exact reversible watch cache plan — NOT executed

The durable proposal binds a complete semantic watch inventory (every lexical path, type, full permission mode and file hash), before-tree digest and acquired after-tree digest. No observed device/inode/uid/gid/timestamp or stat snapshot belongs in Git. Remove **only** these generated entries after durable verified archive, with invocation-local identity/hash/mode rechecks immediately before each descriptor-relative operation:

| Relative path under watch | SHA-256 | Mode |
| --- | --- | --- |
| `scripts/__pycache__/download.cpython-311.pyc` | `d26e46a8cea6976a625864a502e94390aeabcda99c378427b1856ca2edec04c8` | `0664` |
| `scripts/__pycache__/frames.cpython-311.pyc` | `6b2efa3bdac7ddd1c852e263a005224eaba49c154c7e3bc524762549351b4127` | `0664` |
| `scripts/__pycache__/local_whisper.cpython-311.pyc` | `f98cf5893b81591e0f5fdb74d7398f448f8d8dbdaf0673728cad040369b3fd1d` | `0664` |
| `scripts/__pycache__/runtime.cpython-311.pyc` | `9e6e245704a6120589b5cefb324c6b190122b5684ff6d263c24e4580722ef946` | `0664` |
| `scripts/__pycache__/transcript.cpython-311.pyc` | `32bbf8f7433d2f9e7cea06d1b2faeeb99c3fba779f3252a685c0cc6df4c4d7ea` | `0664` |

Before tree: `fe3958e8f284fb716babe95fecb8ca847f0619a8d17780fbc1ad585d4fac7009`. Required actual post-cleanup tree: `671a3b77f77809cc892c50d134005e9605df24c28586b4d0c188322b14a5f782`.

Archive under the proposed default-only rollback namespace using a unique transaction directory outside loadable skills. A later authorized executor must capture then-current ownership/device/inode/timestamps in its invocation-owned journal outside Git, enforce expected ownership policy, and use descriptor-relative handles. Journal and verify archived bytes/modes before unlink; guard cooperating writers and abort on invocation-local identity drift. Remove `scripts/__pycache__` only if empty and still the exact directory captured by that invocation. Never ignore cache directories in production hashing. Rehash all retained watch payload against the complete acquired closure. Changed bytes/types/modes or new paths require re-proposal; pre-invocation same-byte/type/mode replacement alone is not semantic drift.

Rollback uses no-replace publication of each original archived file at its exact path/mode; recreate the named directory only if safely absent. Preserve concurrently recreated files/directories; retain the archive and report manual recovery rather than overwrite a replacement. There is deliberately **no cleanup/apply implementation in this proposal**: the later authenticated receipt change must add/review the narrowly scoped archive transaction and concurrency/rollback tests before enabling production. A recorded identity is a precondition, never deletion authority.

## Full desired-set diagnostic and evidence

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/preview-knowledge-activation.py
HERMES_HOME="${HOME}/.hermes" PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/preview-knowledge-activation.py --profile lnx --live-read-only \
  --nixkit "${HOME}/.hermes/cache/scratch/activation-full-set-kit"
```

The live diagnostic requires a clean committed governance tree and exact clean Kit pin. It performs only reads: no subprocess installer, network, directory creation, persistent lock, archive, or apply. It validates all five proposed preserved trees, names/paths, exact watch cache semantic bindings, and every existing non-preserved full acquired closure including modes. Ephemeral identities bracket non-following opens/reads and the snapshot to refuse in-flight replacement, but are stripped before comparison/output and never persisted. It prints the full planned command as **NOT EXECUTED** and has no `--apply` option. Only the exact listed watch entries are projected out for this conditional comparison; production tree hashing remains unchanged. It is **NOT authorized production preview**.

Real scratch-only pinned Kit acquisition selected all 12 install targets. Ten existing target trees match acquired full bytes/types/modes; watch matches after the exact proposed cache-only projection; nix-knowledge is absent live. Full-set scratch offline repeat exited 0. Acquired knowledge's three files were independently compared with pinned Git blobs. Prior real acquisition/independent review covered both candidates, six files and scoped licenses; actual Hermes skills-guard-v10 community scan was safe/allowed. No claim of fresh Discord behavior, rendering, universal safety or future source provenance enforcement.

Effective set: 17 skills; five proposed native satisfactions; 12 planned Kit targets:

`caveman`, `dispatching-parallel-agents`, `handoff`, `lnx-domain-modeling`, `lnx-grill-me`, `lnx-grill-with-docs`, `lnx-to-spec`, `lnx-writing-for-agents`, `nix-knowledge`, `receiving-code-review`, `verification-before-completion`, `watch`.

## Remaining authority and execution gates

**Separate hosted Kit gate:** Parent verified Kit is private. Its Linux hosted job failed before steps with an account billing/spending annotation; macOS was queued at that readback. No hosted Kit green claim, visibility change, billing change, or expansion of earlier KB-only publication authority. Local Kit evidence remains 96 Node + 62 Watch tests and independent specification/quality review. Parent reports source OS1182 exact-head hosted CI green. These account/release gates do not block preparation of this local pending proposal.

The genuine production `scripts/install-skills.py` preview **and apply fail closed** while knowledge activation is pending, even if passed an arbitrary JSON claiming approval. Existing historical receipts are not sufficient for v2. This is deliberate, not a failed preview disguised as approval.

1. Independently review/publish this exact OS proposal (publication is parent-owned); complete source/catalog release gates.
2. Obtain actual human-authenticated approval naming this ancestor proposal commit and exact default-only scope.
3. In a later reviewed commit, retain genuine available evidence, bind the single-skill receipt to this ancestor, and add the authenticated v2 receipt consumer + narrow cache transaction/tests. Do not self-sign an approval or amend historical receipts. Approval of this proposal does not waive review of later executable cleanup code.
4. Capture fresh execution identities outside Git, enforce ownership, and revalidate all semantic bindings/source closures; execute the actual full-set authorized production preview. Any in-flight identity or semantic drift stops rollout. Review/test bad ownership, concurrency and rollback before any executor is enabled; none is enabled here.
5. Only after separate apply authority: perform approved cache transaction/install; validate preserved trees before/immediately before/after, actual post-cache tree, target closure equality, non-target preservation, rollback ownership and idempotence. Then verify a genuinely fresh Discord conversation outside the checkout and representative retrieval/render behavior.

The proposal is executable/test-backed for pure preview and denial, not an implementation claiming that later authenticated cleanup/install already exists.
