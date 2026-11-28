# Nix knowledge in Google Drive

Hermes works directly in **My Drive → Nix** on the founder’s own Google account. The pinned Google Workspace CLI (`gws`) runs in the Hermes container. The host sync bridge from PR #109 was retired before activation.

Keep shared notes, templates and links as ordinary Markdown files under **Nix**. For a native Google Doc, Sheet or Slides file, write a sibling `<title>.index.md` index note with YAML frontmatter, a one-line summary and its `webViewLink`, and keep the original in Google Drive. Keep raw customer data and bulk transcript imports out of shared notes. Reviewed policy stays in the read-only Git KB snapshot at `/knowledge/company`. Propose evidence-linked policy changes by PR against the KB.

## Obsidian vault structure

**My Drive → Nix** is structured so the founder can open it directly as an Obsidian vault (through the Google Drive desktop sync app or an Obsidian-compatible Drive sync tool on Android) while keeping every note readable in plain Markdown:

- `Nix/START-HERE.md` — vault home explaining what lives where, with relative links to `CONVENTIONS.md`, `Company/`, and `Ventures/`.
- `Nix/CONVENTIONS.md` — required YAML frontmatter keys (`title`, `type`, `venture`, `status`, `updated`, `source`), relative links (no absolute Drive URLs for local notes), per-area `_attachments/` folders, and the `<title>.index.md` rule for native Google Docs, Sheets, and Slides.
- `Nix/.obsidian/app.json` — minimal vault config (`useMarkdownLinks: true`, `newLinkFormat: "relative"`, `attachmentFolderPath: "./_attachments"`).
- `Nix/Ventures/<id>/` — per-venture working folder with a `README.md` index note linking to `PRD.md` and `GTM.md`.

Deterministic maintenance runs through `skills/google-drive/scripts/vault.py` (deployed inside Hermes at `/opt/data/skills/nix-os/google-drive/scripts/vault.py`). It resolves the single owned top-level **Nix** folder, never overwrites existing files, never shares or deletes, and never prints or stores raw Drive IDs in logs or receipts:

```sh
docker exec -u 10000 nixos-hermes python3 /opt/data/skills/nix-os/google-drive/scripts/vault.py bootstrap --dry-run
docker exec -u 10000 nixos-hermes python3 /opt/data/skills/nix-os/google-drive/scripts/vault.py bootstrap
docker exec -u 10000 nixos-hermes python3 /opt/data/skills/nix-os/google-drive/scripts/vault.py ensure-venture <id>
docker exec -u 10000 nixos-hermes python3 /opt/data/skills/nix-os/google-drive/scripts/vault.py index-native Ventures/<id>
docker exec -u 10000 nixos-hermes python3 /opt/data/skills/nix-os/google-drive/scripts/vault.py lint
```

Venture Factory (`skills/venture-factory/scripts/venture.py create`) calls `vault.py ensure-venture` automatically, persists a local redacted receipt (`$HERMES_HOME/ventures/<id>/knowledge-vault.json`, storing only SHA-256 digests of Drive IDs), reuses that receipt on retry, and records `pending` without failing venture creation when `gws` is unavailable or unauthenticated.

## Founder sign-in

1. Create or confirm one owned top-level folder named **Nix** in My Drive. In your Google Cloud project, enable the **Google Drive API**, add `https://www.googleapis.com/auth/drive` in the consent screen’s Data Access settings, and download the OAuth client JSON for a **Desktop app**. For a personal Google account, use an External audience. An External app left in Testing gets a refresh token that expires after seven days. For durable personal use, publish the consent configuration to In production. Google allows a personal-use verification exception and may show an unverified-app warning. [Google OAuth refresh-token rules](https://developers.google.com/identity/protocols/oauth2#expiration) · [Personal-use exception](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification#personal-use)
2. After the new Hermes image is deployed, send the client JSON directly from your device. Use your configured SSH alias in place of `vps` if needed.

   ```sh
   ssh -l nixos vps 'docker exec -i -u 10000 nixos-hermes sh -c "umask 077 && mkdir -p /opt/data/.config/gws && cat > /opt/data/.config/gws/client_secret.json"' < ./client_secret.json
   ```

3. Start login from the device where you will open the consent page. The explicit Workspace API scope is Drive read/write. This `gws` release also adds Google sign-in identity scopes (`openid`, email address and profile). [Pinned login implementation](https://github.com/googleworkspace/cli/blob/v0.22.5/crates/google-workspace-cli/src/auth_commands.rs#L595-L619)

   ```sh
   ssh -l nixos vps 'docker exec -u 10000 nixos-hermes gws auth login --scopes https://www.googleapis.com/auth/drive >/dev/null'
   ```

4. Keep login running. Read the port from the printed authorization URL’s `redirect_uri` (the digits after `localhost`, with the colon possibly encoded as `%3A`). In a second session on the same device, replace both instances of `PORT` with those digits and keep the tunnel running. Open the printed Google URL in that device’s browser and approve with your own Google account. Close the tunnel after the login command succeeds. The pinned CLI listens for the callback on `127.0.0.1:PORT` (IPv4) inside `nixos-hermes`. That container runs with `network_mode: host` (`deploy/compose.yaml`), so its loopback is the VPS host's loopback and the tunnel's `127.0.0.1:PORT` target reaches it; the explicit IPv4 address avoids a `localhost` → `::1` mismatch. On a phone, use an SSH client with local port forwarding, set the local and remote ports to the printed port, and open the URL in the phone’s browser. [Upstream authentication guide](https://github.com/googleworkspace/cli/tree/v0.22.5#authentication)

   ```sh
   ssh -N -o ExitOnForwardFailure=yes -L PORT:127.0.0.1:PORT -l nixos vps
   ```

5. Check the grant with a read-only Drive API call that returns no file names or IDs.

   ```sh
   ssh -l nixos vps 'docker exec -u 10000 nixos-hermes gws drive files list --params "{\"pageSize\":1,\"fields\":\"kind\"}"'
   ```

## Access and recovery

The grant covers the founder’s account-wide Drive access. Every Hermes profile runs under the same UID in the same container and can read the credential and its key. The **Nix** folder boundary and the founder-only sharing rules are agent instructions. The Drive API can enforce the Google account’s permissions, and the token can use them to change sharing. All sharing and permission changes remain founder-gated under [Authority](AUTHORITY.md). The deployment installs the [Drive skill](../skills/google-drive/SKILL.md) for LNX and venture leads. T3 and the independent reviewer have separate mounts and receive no Drive credential. Keep credential material and private Drive output out of delegated prompts and PRs.

`gws` stores `credentials.enc` (AES-256-GCM) with its `.encryption_key` in `/opt/data/.config/gws` in the shared Hermes home. The private daily cold recovery image includes both files and the OAuth client JSON. Treat access to those images as access to the grant. Restore the Hermes home as a unit. If the grant is exposed, revoke it in the founder’s Google account, run `gws auth logout` as UID 10000 in Hermes, then repeat the login. [Recovery runbook](DOCKERHUB-RECOVERY.md)
