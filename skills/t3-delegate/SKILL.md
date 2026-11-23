---
name: t3-delegate
description: "Use when a venture lead or LNX needs code written, fixed or reviewed in a repository: delegate it to a T3 coding thread and track it to PR evidence."
license: MIT
---

# Delegate coding to T3

T3 Code runs the coding harnesses (Claude Code, Codex, OpenCode, Grok, Gemini) in its own isolated container. You reach it through the `t3` MCP server (tools named `t3_*` and `pull_request_*`). You never run code changes in the Hermes runtime yourself.

## Launch (idempotent)
1. **Duplicate check first.** Call `t3_thread_search` with the Kanban card id (e.g. `t_1b64c5d9`). If a thread for this card exists, resume it with `t3_thread_send` or `t3_thread_read` instead of launching another. A lost reply is not proof that no thread exists.
2. **Project.** Use `t3_project_list`. If the repository isn't there, `t3_project_clone` it (GitHub URL).
3. **Launch** with `t3_thread_launch`:
   - `projectId`
   - `title`: `"<card id>: <short goal>"` (this makes step 1 work)
   - `runtimeMode: "full-access"`; the container is the boundary
   - `workspaceStrategy`: `{type: "worktree", branch: "<venture>/<card id>", baseRef: "origin/main", startFromOrigin: true}`, so every task gets its own worktree
   - `modelSelection`: from `orchestrator_capabilities`. Codex or Claude for ordinary work, a second, different provider for review.
   - `message`: the original requirement verbatim, the acceptance checks, the selected skills, and *"open a PR with these acceptance checks in its description; do not merge"*.
4. Record the returned `threadId` and link on the Kanban card in the same step.

## Follow and verify
- `t3_thread_wait` (bounded) or a later `t3_thread_read`. If T3 restarted, read the thread before resending anything.
- Completion evidence is `pull_request_read` showing the PR, its head SHA and **green required checks**, plus the independent review result. The thread saying "done" is not evidence.
- If checks fail, send the failure to the same thread with `t3_thread_send`, so one task maps to one thread and one PR.
- Merging follows the repository's ruleset (independent review plus required checks, then auto-merge). Never merge by hand to bypass a red check, and never edit the files that define the checks or reviewer.
