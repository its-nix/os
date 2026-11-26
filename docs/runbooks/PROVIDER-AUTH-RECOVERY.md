# Provider login recovery

Operator runbook for expired or revoked logins. Per section: symptom, how to confirm, the exact re-login command, how to verify. All commands run from the operator machine over the `nixos` SSH alias, like the other guides.

| Login | Where it lives | Who uses it |
| --- | --- | --- |
| Claude Code, Codex, OpenCode, Antigravity (`agy`), Grok | `t3/home` in the T3 container | T3 coding agents |
| Same five CLIs, separate logins | `reviewer/home` in the reviewer container | the independent PR reviewer |
| Extraction login (`openai-codex`) | `hindsight/codex` in Hindsight state | memory extraction |
| Shared model login | Hermes native store | all Hermes profiles |
| Hermes→T3 bearer (`MCP_T3_API_KEY`) | Hermes' private `.env` | venture leads delegating to T3 |
| Drive vault (rclone) | `/srv/nix-clean/drive/` (root-only) | the host Drive bridge |

Ground rules:

- T3 and the reviewer use the **same pinned image but separate login homes**. Re-login in both containers when both need the provider; never copy login files between them, between profiles, or into Git or shell arguments.
- In `nixos-t3` the CLIs run as the default user `t3` (uid 10010). In `nixos-reviewer` the orchestrator runs as root, so every CLI needs `-u t3`.
- API keys are an alternative to subscription logins for unattended T3 use, subject to each provider's terms; set them in T3's provider settings.

## Harness logins inside T3

Symptom: a T3 thread or delegated task fails with a provider auth error while other providers still work. Confirm functionally — run a one-line task with that provider in the T3 cockpit (`ssh -N -l nixos -L 3773:127.0.0.1:3773 vps`, then open `http://127.0.0.1:3773`); a dead login fails immediately.

```bash
ssh -t -l nixos vps 'docker exec -it nixos-t3 claude'                  # then /login, interactive
ssh -t -l nixos vps 'docker exec -it nixos-t3 codex login --device-auth'
ssh -t -l nixos vps 'docker exec -it nixos-t3 opencode auth login'    # provider "OpenCode", paste the OpenCode Go key
ssh -t -l nixos vps 'docker exec -it nixos-t3 grok'                    # SuperGrok sign-in
ssh -t -l nixos vps 'docker exec -it nixos-t3 agy'                     # open the printed URL, paste the code back
```

Logins persist in `t3/home`. Verify with another one-line task in the cockpit: a model reply means the login is live.

## Harness logins inside the reviewer

Symptom: the `independent-review` check fails or names skipped providers; `docker logs nixos-reviewer` prints one line per failure (never PR contents). A provider without a working login simply drops out of rotation; if none answers, the check fails and retries after 30 minutes.

```bash
ssh -t -l nixos vps 'docker exec -it -u t3 nixos-reviewer claude'                  # /login (Claude Pro)
ssh -t -l nixos vps 'docker exec -it -u t3 nixos-reviewer codex login --device-auth'   # ChatGPT Plus
ssh -t -l nixos vps 'docker exec -it -u t3 nixos-reviewer opencode auth login'      # provider "OpenCode", paste the OpenCode Go key
ssh -t -l nixos vps 'docker exec -it -u t3 nixos-reviewer grok'                    # SuperGrok sign-in
ssh -t -l nixos vps 'docker exec -it -u t3 nixos-reviewer agy'                     # Google AI Pro: open the printed URL, paste the code back
```

Verify: on the next poll the check-run summary names the re-logged-in provider as the reviewer instead of skipping it, and `docker logs nixos-reviewer` stays quiet for it. These logins are separate from T3's on purpose ([deploy/reviewer](../../deploy/reviewer/README.md)): builders running in T3 can never reuse a reviewer session.

OpenCode specifics: reviews always go to the OpenCode Go endpoint, never pay-as-you-go; the review model defaults to `opencode/kimi-k3.1-pro` and can be changed with `OPENCODE_REVIEW_MODEL` in `reviewer.env`.

## Hermes shared model login

Symptom: model turns fail on every profile at once — the profiles share one fallback credential. Re-login through the native interactive setup, once:

```bash
ssh -t -l nixos vps 'docker exec -it -u 10000 nixos-hermes hermes model'
```

Complete the device authorization for OpenAI Codex. Named profiles use the shared global-root credential when they have no profile-specific override; never copy refresh-token files between profiles.

Verify: open the Hermes dashboard (`ssh -N -l nixos -L 19119:127.0.0.1:19119 vps`, then `http://127.0.0.1:19119`) and send a short message; a live model reply confirms it. Do not print keys or response traces into logs.

## Hindsight extraction login

Symptom: recall still works but new turns are not extracted; the server logs report the LLM as unavailable (`deploy/hindsight/README.md`). Confirm with `docker logs nixos-hindsight` while `curl -s http://127.0.0.1:18888/health` still answers — the server is up, only its model login is dead.

Hindsight has its **own** ChatGPT device login, never a copy of Hermes's or T3's. The deploy installs the one-time sign-in script:

```bash
ssh -t -l nixos vps nix-hindsight-login
```

Approve the device code it prints. The script takes the deploy/recovery lock, stores `auth.json` under the server's state, restarts `nixos-hindsight` (it loads the login only at start) and makes one test call to the extraction model. From then on Hindsight refreshes its own tokens.

Verify: the script prints `Hindsight is signed in and its model answered.` Then the standing checks:

```bash
docker exec -u 10000 nixos-hermes hermes -p engineer config get memory.provider   # hindsight
curl -s http://127.0.0.1:18888/health
```

## Hermes→T3 token

Symptom: a venture lead's delegation to T3 fails; `hermes mcp test t3` does not connect. This is the 30-day OAuth bearer the root deploy mints via T3's own pairing approval ([REFACTOR D7](../REFACTOR.md)); it has no refresh. A deployment mints a new one only when fewer than 7 days remain on the recorded expiry, or when the stored token is missing or rejected by T3, or when the non-secret sidecar `t3-mcp.json` is missing (`RENEW_DAYS = 7`, `ensure()` in [deploy/t3/hermes_token.py](../../deploy/t3/hermes_token.py)); with a current, accepted token and its sidecar present, a deployment leaves the token unchanged.

Confirm:

```bash
docker exec -u 10000 nixos-hermes hermes -p <profile> mcp test t3
sudo cat /srv/nix-clean/hermes/nix/t3-mcp.json     # non-secret sidecar: expires_at, renewed_at
```

The sidecar date alone proves nothing: a token can be rejected while still unexpired. Trust `mcp test t3`.

Renew — preferred, because it also restarts Hermes when the credential changed, records the loaded fingerprint, and shares the token with every venture lead's private `.env`:

```bash
sudo -n /usr/local/sbin/nix-os-deploy <current main sha>
```

This is also the recovery path for a rejected-but-unexpired token: the deploy probes the stored token against T3 first and mints a fresh one when the probe fails, whatever the sidecar date says (`ensure()` in [deploy/t3/hermes_token.py](../../deploy/t3/hermes_token.py)).

Manual, when a deploy is not wanted right now — same probe-then-mint logic, and the gateway loads the key only at start:

```bash
sudo python3 /opt/nix-clean-source/deploy/t3/hermes_token.py     # probes, mints and writes MCP_T3_API_KEY
docker restart --timeout 60 nixos-hermes
```

The standalone script writes Hermes' own private `.env` only; it does not touch the venture leads' `.env` files — the next deploy propagates the token to them and completes the bookkeeping (`share_with_leads` in [scripts/deploy-os.py](../../scripts/deploy-os.py)). It never prints the token.

Verify:

```bash
docker exec -u 10000 nixos-hermes hermes -p <profile> mcp test t3
```

`Connected` with discovered tools confirms the credential end to end. Substitute the profile name; registered leads are the venture profiles.

## Google Drive vault

There is no `skills/google-drive` in this repository; the only Google login this deployment implements is the Drive vault's rclone token, held root-only on the host ([KNOWLEDGE-VAULT](../KNOWLEDGE-VAULT.md)). The Drive design is moving from this host bridge to the gws CLI inside Hermes ([PR #112](https://github.com/its-nix/os/pull/112), open at the time of writing); until it merges, this section tracks main and the rclone login below is the one to recover. Symptom: `nix-drive.service` fails or bisync refuses; the grant expired or was revoked. Confirm on the host:

```bash
systemctl status nix-drive.timer nix-drive.service
journalctl -u nix-drive.service
```

For a revoked or expired grant, repeat the one-time login from a computer with a browser, and keep the terminal open:

```bash
ssh -t -o ExitOnForwardFailure=yes -L 53682:127.0.0.1:53682 -l nixos vps 'sudo -n /usr/local/sbin/nix-drive login'
```

Paste the folder ID at the hidden prompt, open the loopback URL printed in the terminal on that same device, and authorize rclone with the founder's account; the callback reaches the VPS through the SSH tunnel.

Verify: a successful login initializes bisync and enables the seven-minute timer — re-check `systemctl status nix-drive.timer nix-drive.service`, then round-trip a disposable Markdown note from a client. If the timer had been stopped, `sudo systemctl enable --now nix-drive.timer` after the login.
