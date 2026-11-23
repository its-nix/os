# T3 Code container

Pinned T3 Code v0.1.11 (upstream linux-x64 tarball, SHA-256 checked) plus the native harness CLIs: Claude Code, Codex, OpenCode, Gemini CLI and Grok, and `gh`. Versions are ARGs in `Dockerfile`, mirrored in `deploy/versions.json` (a test keeps them equal). The deployment builds it locally (`docker compose build t3`), so no registry is involved.

## Boundary

Full-access coding agents run here, so the container is the boundary:

- runs as non-root `t3` (uid 10010)
- has no capabilities and `no-new-privileges`
- has no Docker socket and no Hermes or Paperclip state
- sits on its own bridge network `nixos-coding` (not host networking), so the host-loopback Hermes API, Hermes dashboard and Paperclip are unreachable; this was verified from inside the container
- is published on host `127.0.0.1:3773` only

State:
- `/srv/nix-clean/t3/home` holds T3's database and the harnesses' native logins. It is backed up.
- `/srv/nix-clean/t3/work` holds clones and worktrees. It is rebuildable from GitHub and excluded from backups.

## Hermes access

Hermes connects as an outside agent over MCP at `http://127.0.0.1:3773/mcp`. T3 caps CLI-issued MCP sessions at `approval-required`, so the deployment runs `hermes_token.py`, which uses T3's own OAuth + PKCE approval with a one-time pairing code at **full-access**. It writes the 30-day bearer into Hermes' private `.env` as `MCP_T3_API_KEY` and renews it when fewer than 7 days remain. The token is never printed.

One-time registration in the LNX profile (after the first deployment):

```bash
docker exec -u 10000 nixos-hermes hermes mcp add t3 --url http://127.0.0.1:3773/mcp --auth header
```

## Operator cockpit and provider logins

```bash
ssh -N -l nixos -L 3773:127.0.0.1:3773 vps            # then open http://127.0.0.1:3773
docker exec -it nixos-t3 t3 pair                        # one-time pairing link for a browser/app
docker exec -it nixos-t3 claude                         # /login, interactive, once
docker exec -it nixos-t3 codex login --device-auth      # once
```

Logins persist in `t3/home`. Unattended use must follow each provider's terms; API keys can be set in T3 provider settings. T3 Connect stays off: access is via SSH tunnel or Tailscale.

The Antigravity ACP runtime is installed from T3's provider settings into the persistent home. Grok is supported through the pinned `grok` CLI.
