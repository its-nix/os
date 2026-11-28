# Venture channels on the LNX Discord bot

Hermes v0.23.0 uses the default gateway's `gateway.profile_routes` table. A venture row matches the channel ID and the founder's Discord user ID. Replies use the existing LNX bot and the venture lead's profile. [Hermes v0.23.0 routing documentation](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/website/docs/user-guide/multi-profile-gateways.md#routing-shared-bot-chats-to-profiles-profile_routes)

## Bind a channel

1. In the server that already has LNX, create a private text channel visible to the founder and LNX. Give LNX permission to view the channel, read message history, and send messages. If LNX uses a channel allowlist or auto-creates threads, include the venture channel in the existing allowlist and grant the bot the required thread permissions.
2. Enable Discord Developer Mode and use **Copy Channel ID** on that channel. The default bot must already have exactly one numeric user ID in its private allowlist (`DISCORD_ALLOWED_USERS` or the equivalent Discord YAML setting). Confirm it belongs to the founder.
3. Run the bind from the runtime host. Supply the active venture ID and its channel ID as local shell variables.

   ```bash
   factory=/opt/data/skills/nix-os/venture-factory/scripts/venture.py
   docker exec -u 10000 nixos-hermes python3 "$factory" bind-discord "$venture" --channel "$channel" &&
     docker restart nixos-hermes &&
     docker exec -u 10000 nixos-hermes hermes -p default gateway status
   ```

The manifest receipt records the configuration readback. Hermes loads the route table at gateway startup. The restart applies the edit and briefly interrupts the shared bot. A repeated bind to the same channel reuses the route and receipt when they already match.

## Verify live

Mention LNX from the founder account in the venture channel and include a fresh, unique probe string in the message. Confirm LNX replies. Set `probe` to that string and run the following read-only check to attribute the turn without printing the conversation.

```bash
docker exec -i -u 10000 nixos-hermes python3 - "$venture" "$probe" <<'PY'
from pathlib import Path
import sqlite3
import sys

venture, probe = sys.argv[1:]
root = Path("/opt/data")
for profile, home in [("default", root), (venture, root / "profiles" / venture)]:
    with sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True) as db:
        count, = db.execute(
            "SELECT count(*) FROM messages JOIN sessions ON messages.session_id = sessions.id "
            "WHERE sessions.source = 'discord' AND messages.role = 'user' "
            "AND instr(messages.content, ?) > 0", (probe,),
        ).fetchone()
    print(profile, count)
PY
```

Expect zero matches in `default` and a match in the venture profile. Repeat with a different probe in the usual LNX channel. Expect a match in `default` and zero in the venture profile.

## Pause, resume, or retire

Run `venture.py pause|resume|retire --id <venture>` inside Hermes, then run `docker restart nixos-hermes` to apply the route change. Pause and retire disable the venture route and retain the channel binding. Resume restores it using the current founder allowlist. After a failed command, repeat the same lifecycle command before restarting. With a disabled route, the channel follows LNX's ordinary routing and Discord permissions. Remove the bot's access to the private channel in Discord if you want silence there.
