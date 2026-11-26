# NixKit packs and Skillshare

The deployment Kit revision comes from `deploy/versions.json`. Pack names in
`deploy/packs/packs.json` map to canonical Kit skill names. The lock records
Kit's upstream repository, full revision and tree digest for each selected
skill. A selection is the set union of core, role and task membership.
Core is shared. The selectable packs are engineering, product, growth,
sales, content, design, research, operations, and data-ai. A pack addition
or a lock change goes through the OS pull request gate.

The pack builder starts from a fresh checkout of the installed Kit revision.
Kit fetches selected sources, validates its snapshots and copies to a private
staging prefix. The builder compares Kit's resulting lock to the reviewed
entries. Skillshare v0.31.0 (commit
`dab6d4fe96f30ca1d49a6b5e5f8fa3682b010b24`) then syncs the staging prefix
in copy mode into a new directory. The builder checks the delivered file
trees and executable bits. Only publish the target after a successful exit.
A failed target can be discarded by the caller.

| Destination | Writer and deployment use |
| --- | --- |
| Private input prefix | Kit installer, disposable per build |
| Fresh copy target | Skillshare, configured via an isolated config and home |
| Hermes `$HERMES_HOME/skills` | Map fresh copies under a dedicated category. Preserve identical existing native skills and refuse conflicting names |
| T3 Codex `~/.agents/skills` | Expose immutable core copies when starting the container |
| Claude `~/.claude/skills`, OpenCode `~/.config/opencode/skills`, agy `~/.gemini/antigravity-cli/skills` | Skillshare built-in global paths. Check shared discovery before adding equivalent mounts |
| Project targets | Select role/task membership before starting a new worktree session. Never re-sync its running session |
| Hermes `skills/nix-os` | Existing OS deploy owns first-party skills |

Skillshare's Hermes default is `~/.hermes/skills`; the live container sets
`HERMES_HOME=/opt/data`. T3 sets `HOME=/var/lib/t3` and contains all four
harness executables. The upstream table also distinguishes agy from the
Antigravity IDE. [Pinned target definitions](https://github.com/runkids/skillshare/blob/dab6d4fe96f30ca1d49a6b5e5f8fa3682b010b24/internal/config/targets.yaml)

For a standalone compilation with Node 24, Git and Python 3, supply a local Kit
clone and an absent destination under a private parent directory:

```sh
python3 deploy/packs/build.py /path/to/kit /path/to/new/skills --role engineering
```

Omit the role for global core. The existing profile-specific installer and
its proposal and approval receipts remain historical. Neither supplies role
membership to this compiler. Agency-derived instructions retain the source,
license and pins in the native input contract. Adding Agency material to a
pack requires review of the selected material. This deployment runs only
Skillshare's skills sync. Harness credentials stay in native stores.

The main deploy exports the pinned Kit tree from the installed input and runs
the builder with `--runtime` as an unprivileged user in a disposable T3 image
container. It publishes all packs and the worktree installer in a verified,
root-owned store. The generated `packs.compose.json` is the second Compose
file for this deployment. It binds
only missing skill directories, read-only, at container creation. Reconcile
uses the same generation until the compiler, Kit revision, worktree installer
or pack lock changes. Existing native copies must match. Any divergence
blocks deployment before Compose starts or recreates the runtime services.
The first T3 target is the shared Codex/OpenCode global discovery directory.

Prepare a **new** detached worktree inside T3 before starting the harness.
For example, combine the product role with a research task:

```sh
python3 /opt/nixkit/.nixkit/worktree.py /work/repo /work/new-task HEAD --role product --task-pack research
```

The helper accepts repeated `--task-pack` options and deduplicates role and
task membership. The pinned Skillshare binary copies only the additions
beyond global core into the new worktree's `.agents/skills`. An existing
worktree path or checked-in skills destination causes refusal.
