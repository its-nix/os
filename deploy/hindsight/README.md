# Hindsight memory

Long-term memory for Hermes (PRD §7.4). Hermes keeps its built-in `MEMORY.md`/`USER.md`; Hindsight adds recall and retention per profile. It does not hold current state: CRM, GitHub and task boards are still the source of truth for that.

## What runs

| Part | Where | Pin |
| --- | --- | --- |
| Server | `nixos-hindsight` (Compose service `hindsight`), own bridge network `nixos-memory`, published on `127.0.0.1:18888` only | `ghcr.io/vectorize-io/hindsight-api@sha256:ef1a5ac5…` (1.2.0) |
| Plugin | Hermes catalog plugin `hindsight`, one install per profile, mode `local_external` | catalog pin `f14cc1eb…` (plugin 1.2.4), checked by the deploy |
| State | `/srv/nix-clean/hindsight` mounted at `/var/lib/hindsight`: embedded Postgres in `.pg0`, the server's own Codex login in `codex/` | In the daily cold recovery capture; the server stops after Hermes |

Models and providers (set in `deploy/compose.yaml`):

- **Extraction LLM:** `openai-codex` with `gpt-6-luna`, through the founder's ChatGPT subscription. It uses a separate device login made for Hindsight, never a copy of Hermes's or T3's.
- **Embeddings:** local `BAAI/bge-small-en-v1.5`, baked into the pinned image. The server runs with `HF_HUB_OFFLINE=1`, so nothing is downloaded at runtime.
- **Reranker:** `rrf` (rank fusion), so no reranker model is loaded. The image's default reranker (`hs-rerank-1`) was killed for running out of memory at a 1.5 GB limit while it loaded.
- **Off:** decision model, MCP endpoint and the bank-config write API. The file parser is `markitdown` (local).
- **Retention:** observations are on. The extraction mode is `concise`, with a reviewed retain mission: keep decisions, preferences, commitments, lessons and stable facts; skip small talk and status other systems already hold; never keep secrets.

## Banks

The deploy (`configure_memory` in `scripts/deploy-os.py`) gives each profile its own bank:

| Profile | Bank |
| --- | --- |
| `default` (LNX) | `lnx` |
| `engineer` | `engineer` |
| `reviewer` | `reviewer` |
| venture lead `<name>` (registry marker) | `venture-<name>` |
| `personal` (when it exists) | `personal` |

Any other profile gets no memory provider. Each profile's `hindsight/config.json` is written once, with mode, URL and bank set explicitly. It is never rewritten. The deploy refuses and leaves the file alone in these cases:

- The config points elsewhere.
- It names extra or recall-only banks, trusted project directories or a bank template.
- The profile `.env` sets any `HINDSIGHT_*` value, which would override the bank or server.
- A shared `~/.hindsight/config.json` exists.

Two related rules:

- A profile switched to another provider is left alone. So is one turned off with `hermes -p <profile> memory off` after setup.
- `hermes memory setup` is not used. It fetches templates from an unpinned branch.

Isolation is by bank on one local server. Company profiles cannot name another bank, and none can reach `personal`. Every profile runs as the same Hermes user in one container, though, so this is not an OS-level sandbox between profiles.

## Founder: one-time sign-in

```bash
ssh -t -l nixos vps sh /opt/nix-clean-source/deploy/hindsight/codex-login.sh
```

Approve the device code it prints. The script then:

1. Runs the Codex CLI pinned in the deployed T3 image as the server's user.
2. Stores `auth.json` in a file under `hindsight/codex`.
3. Restarts `nixos-hindsight`. The server loads its login only at start.

From then on Hindsight refreshes its own tokens. Until the sign-in is done, recall works but new turns are not extracted.

## Check

```bash
docker exec -u 10000 nixos-hermes hermes -p engineer memory status        # Provider hindsight, available
curl -s http://127.0.0.1:18888/health
docker logs nixos-hindsight 2>&1 | grep -ci 'vectorize.io'                # 0
```

Updating the plugin or the server is a reviewed PR that changes the pins. The deploy refuses a plugin that is not at the reviewed commit. Plugin auto-update is off in Hermes.
