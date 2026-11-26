# Hindsight memory

Long-term memory for Hermes (PRD §7.4). Hermes keeps its built-in `MEMORY.md`/`USER.md`; Hindsight adds recall and retention per profile. It does not hold current state: CRM, GitHub and task boards are still the source of truth for that.

## What runs

| Part | Where | Pin |
| --- | --- | --- |
| Server | `nixos-hindsight` (Compose service `hindsight`), own bridge network `nixos-memory`, published on `127.0.0.1:18888` only | `ghcr.io/vectorize-io/hindsight-api@sha256:ef1a5ac5…` (1.2.0) |
| Plugin | Hermes catalog plugin `hindsight`, one install per profile, mode `local_external` | catalog pin `f14cc1eb…` (plugin 1.2.4), checked before and after install |
| State | `/srv/nix-clean/hindsight` mounted at `/var/lib/hindsight`: embedded Postgres in `.pg0`, the server's own Codex login in `codex/` | In the daily cold recovery capture; the server stops after Hermes |
| API key | `/srv/nix-clean/hindsight.env` (root, `0600`), generated once by the deploy; copied into each memory profile's `hindsight/config.json` | Compose `env_file` |

Models and providers (set in `deploy/compose.yaml`):

- **Extraction LLM:** `openai-codex` with `gpt-6-luna`, through the founder's ChatGPT subscription. It uses a separate device login made for Hindsight, never a copy of Hermes's or T3's.
- **Embeddings:** local `BAAI/bge-small-en-v1.5`, baked into the pinned image. The server runs with `HF_HUB_OFFLINE=1`, so nothing is downloaded at runtime.
- **Reranker:** `rrf` (rank fusion), so no reranker model is loaded. The image's default reranker (`hs-rerank-1`) was killed for running out of memory at a 1.5 GB limit while it loaded.
- **Off:** decision model, MCP endpoint and the bank-config write API. The file parser is `markitdown` (local).
- **Retention:** observations are on. The extraction mode is `concise`, with a reviewed retain mission: keep decisions, preferences, commitments, lessons and stable facts; skip small talk and status other systems already hold; never keep secrets. The mission is a second layer only; see [Secrets](#secrets).
- **API key:** the server runs Hindsight's built-in `ApiKeyTenantExtension`. Every bank call needs the key; `/health` does not. The bank LLM check is on, so the founder sign-in can test the model, and it sits behind the key.

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

- The config points elsewhere, or does not carry the server's API key.
- It names extra or recall-only banks, trusted project directories or a bank template.
- The profile `.env` sets any `HINDSIGHT_*` value, which would override the bank or server.
- A shared `~/.hindsight/config.json` exists.

Two related rules:

- A profile switched to another provider is left alone. So is one turned off with `hermes -p <profile> memory off` after setup.
- `hermes memory setup` is not used. It fetches templates from an unpinned branch.

## Threat model

Banks separate profiles **organizationally**, like Kanban boards. They are **not** a confidentiality boundary between Hermes profiles.

- All Hermes profiles run in the one `nixos-hermes` container as uid 10000. Each can read every other profile's files, `.env` and `hindsight/config.json`, which holds the bank name and the API key.
- So any Hermes process can address any bank, `personal` included, by calling the API directly. The deploy checks above stop a profile's *configuration* from naming another bank; they do not stop such a call at runtime.
- Per-profile keys or server-side bank authorization would not change that, because every profile can read every key. Hard isolation needs a separate container and uid per profile (or per scope), which is out of scope (PRD: personal and company scopes are logically separated, with no confidentiality against other profiles in the same container).

What the setup does keep out:

| Who | How |
| --- | --- |
| T3 and the reviewer | They run on their own bridge networks (`nixos-coding`, `nixos-review`); the server is on `nixos-memory` and published on host loopback only. Probed from both containers on the VPS: host-loopback ports (Hermes's API and dashboard) and a loopback-published container port on another network (T3's) all time out via the bridge gateway and the other container's address. |
| Other host processes and users | The API key. Hermes is the only container on the host network. Root and the `nixos` operator (Docker group) can read the key anyway. |
| The internet | Published on `127.0.0.1` only; the deploy checks the listener. |

## Secrets

Hermes itself scrubs secrets before the plugin sees any text. In the pinned Hermes (0.23.0) the memory manager passes every provider hand-off through `_redact_for_provider`: turn sync, recall queries, tool arguments (`retain`/`recall`), built-in memory mirrors, session-end and pre-compress transcripts, and delegation results. That function uses Hermes's egress scrub (`redact_for_egress`), which is forced on regardless of `security.redact_secrets` and fails closed. A value it masks anywhere in a hand-off is then masked everywhere in that hand-off. The plugin has no other input. Its automatic capture (`sync_turn`) takes only the user and assistant text of a turn, already scrubbed, and it reads no transcripts or files of its own.

The deploy proves this on every rollout. Before `compose up`, it runs [`redaction_probe.py`](redaction_probe.py) inside the Hermes image it is about to start, in a throwaway container with no network. The probe feeds synthetic credentials through the scrub: OpenAI-style, GitHub and AWS keys, a `Bearer` header, a JWT, a PEM private key, and `PASSWORD=` / `API_KEY=` lines in `.env` form. It registers a recording provider, exercises each hand-off, drains queued work, and checks every recorded payload for the sample secrets. Missing callbacks fail the probe. Unless it prints `redaction-probe: ok`, the deploy refuses and nothing running changes.

Residual risk:

- A secret with no recognisable shape, such as a password written in plain prose, is not caught by the scrub. The retain mission tells the extraction model never to keep secrets, but that is an instruction, not a guarantee.
- Such text goes to the extraction model. That model is OpenAI through the founder's ChatGPT subscription, the same company whose model already receives every Hermes turn as Hermes's own main model. So no new recipient sees the text.
- What is new is **durable storage**. Extracted facts and the retained turn text are kept in the local Hindsight Postgres (`/srv/nix-clean/hindsight`), and therefore in the daily private recovery image, until deleted.

## Founder: one-time sign-in

```bash
ssh -t -l nixos vps nix-hindsight-login
```

The deploy installs [`codex-login.sh`](codex-login.sh) as `/usr/local/bin/nix-hindsight-login`. It takes the deployment and recovery lock before sign-in and holds it through the model check. Approve the device code it prints. The script then:

1. Runs the Codex CLI pinned in the deployed T3 image as the server's user.
2. Stores `auth.json` in a file under `hindsight/codex`.
3. Restarts `nixos-hindsight` (the server loads its login only at start) and makes one test call to the extraction model. The call runs inside the server container with its own API key, so the key never appears on a host command line. The script reports success or failure without printing the model, key or token.

From then on Hindsight refreshes its own tokens. Until the sign-in is done, recall works but new turns are not extracted.

## Check

```bash
docker exec -u 10000 nixos-hermes hermes -p engineer config get memory.provider   # hindsight
curl -s http://127.0.0.1:18888/health                                             # no key needed
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18888/v1/default/banks   # 401 without the key
docker logs nixos-hindsight 2>&1 | grep -ci 'vectorize.io'                # 0
```

Updating the plugin or the server is a reviewed PR that changes the pins. Plugin auto-update is off in Hermes. Before starting the gateway, the candidate image reads each profile’s provider with its home mounted read-only. Active Hindsight profiles must have a recognized bank, a valid config and the reviewed plugin bytes. For a profile without the plugin, the deploy follows this order:

1. It checks that the Hermes image's catalog entry names the reviewed repository, commit and directory. On a mismatch it stops before writing, downloading or installing anything.
2. It writes the profile's config.
3. It installs the plugin **disabled** at that exact commit (`--ref <sha> --no-deps --no-enable`).
4. It checks the catalog sidecar and compares every installed file against the SHA-256 manifest for the pinned upstream directory. On a mismatch it stops with the plugin disabled and the provider unset.
5. Only then does it set `memory.provider`.

A marker in the root-owned `hindsight-enable/` directory outside the Hermes home lets an interrupted run finish the activation rather than mistake it for `memory off`.

## Runtime and recreation checks

Deployment, clean preparation and full recovery select the Hermes runtime tag in `versions.json`. Its tag covers the Dockerfile, including the official base digest and the wheel hashes from the reviewed plugin's `uv.lock`. The build resolves the three local wheels offline against the base environment. Every profile uses this venv, including immediately after recreation. Before gateway startup the deployment checks that the candidate Python suppresses bytecode and imports the client and embed entry points. Compose also explicitly disables bytecode writes. The manifest still rejects unexpected files, including existing bytecode.

The pinned upstream image already reported `sys.dont_write_bytecode == True`. Loading the configured provider as uid 10000 wrote zero bytecode files and reported unavailable because its venv lacked `hindsight_client`. Unsetting the bytecode environment variable produced six unexpected `.pyc` files.

The Linux/amd64 lifecycle test started with an empty profile, ran the real catalog installation and activation, and loaded the configured provider through Hermes as uid 10000. It then removed that container, mounted the same profile in a fresh container without network access, and repeated the deployment preflight, configuration and enabled provider load. Both availability checks passed and the plugin still matched the full manifest after both imports. This exercised the restored profile startup condition. The test did not start a gateway, log in, or run an extraction.

To repeat, build the runtime as described in the deployment README and run `HINDSIGHT_RUNTIME_TEST=1 python3 -m unittest discover -s tests -p test_hindsight_runtime.py` with local Docker access, initial catalog network access and permission to manage the fixture's uid 10000 files.
