#!/bin/sh
# One-time ChatGPT sign-in for Hindsight memory extraction: its OWN Codex login (device code),
# never a copy of Hermes's or T3's. Run by the `nixos` operator on the VPS:
#   ssh -t -l nixos vps sh /opt/nix-clean-source/deploy/hindsight/codex-login.sh
# It uses the Codex CLI already pinned in the deployed T3 image (no new download), writes
# auth.json as the Hindsight server's user into its state, then restarts that server so it loads it.
set -eu
image=$(docker inspect --format '{{.Config.Image}}' nixos-t3)
docker inspect --format '{{.Name}}' nixos-hindsight >/dev/null
docker run --rm -it --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
    --mount type=bind,source=/srv/nix-clean/hindsight/codex,target=/codex \
    --env HOME=/codex --env CODEX_HOME=/codex --entrypoint codex "$image" \
    -c 'cli_auth_credentials_store="file"' login --device-auth
docker run --rm --user 1000:1000 --network none --cap-drop ALL --security-opt no-new-privileges \
    --mount type=bind,source=/srv/nix-clean/hindsight/codex,target=/codex,readonly \
    --entrypoint test "$image" -s /codex/auth.json
docker restart --timeout 60 nixos-hindsight >/dev/null
echo 'Hindsight signed in; its server is restarting with the new login.'
