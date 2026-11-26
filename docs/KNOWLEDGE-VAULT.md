# Nix knowledge vault

Open the founder's **My Drive → Nix** as the Obsidian vault. Put shared Markdown, templates, attachments and Google document index notes under **Workspace/**. Both Hermes and the founder can edit that subtree. Configure Obsidian's default new-note and attachment locations there. Ordinary files keep their names, subdirectories, YAML properties, relative links and wiki links. Vault-root Obsidian settings can remain device-local to the host bridge. Only sync trusted Obsidian plugin code between devices.

**Policies (from Git)/** mirrors the deployed, reviewed archive of `its-nix/knowledgebase`. The authority note inside identifies its source. Hermes can read the projection and write Workspace at `/opt/data/vault`. The bridge regenerates the policies folder from Git and can replace or remove Drive edits there. Agents must propose evidence-linked changes by PR against the KB. Only reviewed and deployed policy is executable. Never point bisync at a Git checkout.

For a Google Doc or Sheet, create an ordinary note such as `Workspace/Decisions/Forecast.md` with a title, owner, summary and a link copied from the original. Keep its access controls on Google. The job skips native Google files and shortcuts, including shortcuts leading outside Nix. For shared learning, record the observation, source link with appropriate access, proposed change and reviewer. Keep raw customer messages and bulk transcript imports out of the vault.

## Connect the founder's account

1. In the founder's personal My Drive, create or open the owned top-level folder **Nix**. In its browser URL, copy the component after `/folders/`, stopping before any question mark. Keep it private. Existing ordinary files in `Nix/Workspace` can seed the vault. Leave the VPS Workspace empty for first login. Reserve the new folder name `Policies (from Git)` for the reviewed projection.
2. After deployment, use the operator SSH alias from a computer with a browser. Run this single command and keep the terminal open.

   ```sh
   ssh -t -o ExitOnForwardFailure=yes -L 53682:127.0.0.1:53682 -l nixos vps 'sudo -n /usr/local/sbin/nix-drive login'
   ```

   Paste the folder ID at the hidden prompt. Open the loopback URL printed in the terminal **on that same device** and authorize rclone using the founder's account. The callback goes through SSH to the host. The VPS captures the token directly. A phone can use the same flow with an SSH client that supports local port forwarding and a browser on that phone. No desktop rclone installation is needed.
3. In Google Drive for desktop, make Nix available offline, wait for download, then open the local Nix folder as a vault. On Android, pair only the Nix folder with a local device folder using an Obsidian-compatible Drive sync client configured for two-way sync and conflict retention. Open that device folder in Obsidian. Keep existing note names and attachment paths when pairing. Set the Android client's Google access according to its own permission model.

The personal Drive OAuth grant covers **the account's entire Drive**. The application verifies the selected folder's ownership, type, name and My Drive parent on sign-in. rclone's `root_folder_id` sets its operating root. Google does not enforce that boundary on the token. Only the root-run host helper uses the mode-0600 config in `/srv/nix-clean/drive/`. No runtime container receives it. The host runs the checksum-pinned Linux amd64 rclone 1.70.3 binary. It needs util-linux with bind-mount `nosymfollow` support and a kernel supporting that flag.

## Operate and recover

Deployment installs the helper and systemd units. Successful login initializes bisync and enables the seven-minute timer. Check `systemctl status nix-drive.timer nix-drive.service` and `journalctl -u nix-drive.service` on the host. Normal sync requires matching access sentinels, refuses deletions above 10% on either side and keeps numbered copies on conflicts. Reconcile those copies explicitly. The wrapper takes the deploy/recovery lock before any state access.

If sync refuses, stop the timer (`sudo systemctl stop nix-drive.timer`), let any running service finish, and pause editing on all clients. Verify the account, folder, connectivity, and the two Workspace directories. Preserve both sides independently. For missing `RCLONE_TEST`, verify that the correct complete directories are present before restoring the sentinel from the saved copy. For excessive deletion, restore the missing files or reconcile the intended deletion on both sides from the saved copies. Never automatically force or resync past a refusal.

For lost listings or a rolled-back VPS, save the current Drive Workspace and the restored local Workspace separately. Resolve divergent versions under distinct names and make both trees agree. As root run `/usr/local/sbin/nix-drive resync`. It checks content equality before rebuilding listings. Any overwritten resync versions go into private host or Drive resync backups. After success run `systemctl enable --now nix-drive.timer`. Only one host may sync the vault. For a revoked grant, repeat the SSH login for the same folder. Keep an isolated restore drill offline.

After sign-in, verify from each client with a disposable Markdown note containing YAML, a wiki link and a relative attachment. Confirm a round trip on Hermes and the other device, open the original Doc from an index note, and confirm the policies mount rejects agent writes. [rclone bisync reference](https://rclone.org/bisync/) · [Drive backend](https://rclone.org/drive/)
