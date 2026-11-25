#!/bin/sh
# One-time ChatGPT sign-in for Hindsight memory extraction: its OWN Codex login (device code),
# never a copy of Hermes's or T3's. The deploy installs it as /usr/local/bin/nix-hindsight-login;
# the `nixos` operator runs it:   ssh -t -l nixos vps nix-hindsight-login
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
echo 'Signed in. Waiting for the memory server to restart with the new login...'
tries=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' nixos-hindsight)" = healthy ]; do
    tries=$((tries + 1))
    [ "$tries" -le 60 ] || { echo 'The memory server did not come back healthy; tell the operator.' >&2; exit 1; }
    sleep 5
done
# One real extraction-model call through the new login (status only; no model or token is printed).
result=$(curl -fsS -X POST http://127.0.0.1:18888/v1/default/banks/lnx/health/llm || true)
case "$result" in
    *'"ok":true'*) case "$result" in *'"ok":false'*) ;; *) echo 'Hindsight is signed in and its model answered.'; exit 0;; esac;;
esac
echo 'Signed in, but the memory model check failed; tell the operator.' >&2
exit 1
