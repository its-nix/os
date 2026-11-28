---
name: t3-delegate
description: "Use when a venture lead or LNX needs code written, fixed or reviewed in a repository: delegate it to a T3 coding thread and track it to PR evidence."
license: MIT
---

# Delegate coding to T3

T3 Code runs the coding harnesses (Claude Code, Codex, OpenCode, Grok, Antigravity) in its own isolated container. You reach it through the `t3` MCP server (tools named `t3_*` and `pull_request_*`). You never run code changes in the Hermes runtime yourself.

## Launch (idempotent)
1. **Preflight.** Run `hermes -p <your-profile> mcp test t3`. It must report `Connected`. If the command fails or does not report `Connected`, block the card with the error and do not launch.
2. **Launch guard.** Run `python3 scripts/launch_guard.py begin --venture <venture id> --card <card id>` (LNX uses its own profile name as the venture id). Exit 0 with `"action": "launch"` records the intent and is the only go-ahead. Exit 3 means a launch already exists: do what its `next` says (read the thread, or search by title when no thread id was recorded) and never call `t3_thread_launch`. This holds after a restart, a retry and a lost reply. Also call `t3_thread_search` with the card id; a thread found there that the record lacks is registered with `launched`. A launch that is certainly dead (read it first) is replaced only with `begin --recover --reason "<what you read>"`, which starts a new branch `-r<n>` and keeps the old attempt in the record.
3. **Project.** Use `t3_project_list`. If the repository isn't there, `t3_project_clone` it (GitHub URL).
4. **Launch** with `t3_thread_launch`:
   - `projectId`
   - `title`: `"<card id>: <short goal>"` (this makes step 2 work)
   - `runtimeMode: "full-access"`; the container is the boundary
   - `workspaceStrategy`: `{type: "worktree", branch: "<venture>/<card id>", baseRef: "origin/main", startFromOrigin: true}`, so every task gets its own worktree
   - `modelSelection`: from `orchestrator_capabilities`. Codex or Claude for ordinary work, a second, different provider for review.
   - `message`: the brief below, and *"open a PR with these acceptance checks in its description; do not merge"*. Require a line `Builder: <provider instance id>` (e.g. `Builder: codex`) in the PR description so the independent reviewer can exclude the authoring provider.
5. In the same step run `python3 scripts/launch_guard.py launched --venture <venture id> --card <card id> --thread-id <threadId> --provider <provider> --link <thread link>` and put the thread id and link on the Kanban card. If the launch call failed or its reply was lost, do not retry: search by title first, as step 2 says.

## Brief template
Put these in the launch `message`. Keep it short; a delegate that cannot fill a line asks on the thread.
- **Requirement** verbatim, and the **acceptance checks**.
- **Verification oracle.** Name the check that existed before this change (existing test suite, CI job, fixture or command). Do not edit, delete or loosen an existing test or assertion in the same PR; changing one needs the PR title to carry `test-change` and say why. New tests are fine.
- **Before/after evidence** for any user-visible change: one observation before, one after, and the command or steps to repeat it. Use synthetic data and redact captures.
- **Expensive to reverse?** Ask "what would be expensive to reverse?" Only when the answer is persisted state or an external contract (schema, migration, public API, outbound message), the PR body must include a migration and rollback sketch. Cosmetic or internal changes need none.
- **No planning documents.** Do not commit plans, design notes or progress files. The plan, decisions and rollback live in the PR body.
- **Expected diff size.** State it in lines and files. If the real diff is far larger, say so in the PR body and split or justify it; size alone is a review flag.
- **Task packs.** List the task packs selected for this worktree (core packs are always present). Skills outside them are not available; do not tell the delegate to use one.

## Follow and verify
- `t3_thread_wait` (bounded) or a later `t3_thread_read`. If T3 restarted, read the thread before resending anything.
- Completion evidence is `pull_request_read` showing the PR, its head SHA and **green required checks**, plus the independent review result. The thread saying "done" is not evidence.
- If checks fail, send the failure to the same thread with `t3_thread_send`, so one task maps to one thread and one PR.
- Merging follows the repository's ruleset (independent review plus required checks, then auto-merge). Never merge by hand to bypass a red check, and never edit the files that define the checks or reviewer.

## Outcome receipt
When the PR is open and again when its state changes, record the evidence and paste the output of `python3 scripts/launch_guard.py receipt --venture <venture id> --card <card id>` as a comment on the Kanban card:

    python3 scripts/launch_guard.py complete --venture <venture id> --card <card id> --pr <PR URL> \
      --ci pending|green|red|n/a --review pending|approved|changes-requested|n/a \
      --merge open|auto-merge-armed|merged|closed|n/a --deploy pending|deployed|failed|not-applicable \
      --evidence "<what you observed, e.g. the before/after command and its result>"

The receipt carries thread, provider, branch, PR, CI and review state, merge and deploy state, and verification evidence. Take CI, review and merge from `pull_request_read`, never from the thread's own claim. `complete` can be rerun as states advance (for example `--merge merged`). A different PR for the same card is a recovery.
