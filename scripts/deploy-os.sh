#!/bin/sh
# Root-owned forced-command entrypoint. No general CI shell or caller-selected paths.
set -eu
revision=${1:-}
if [ "$revision" = status ]; then
    cat /srv/nix-clean/deployment.json /srv/nix-clean/deployment-attempt.json
    exit 0
fi
case "$revision" in ''|*[!0-9a-f]*) echo 'Exact main SHA required' >&2; exit 1;; esac
[ "${#revision}" -eq 40 ] || exit 1
exec 9>/run/lock/nix-os-deploy.lock
flock -n 9 || { echo 'Deployment already running' >&2; exit 1; }
repository=/var/lib/nix-os/repository
mkdir -p /var/lib/nix-os /opt/nix-os/releases
if [ ! -d "$repository/.git" ]; then
    git clone --no-checkout --no-tags https://github.com/its-nix/os.git "$repository"
fi
[ "$(git -C "$repository" remote get-url origin)" = https://github.com/its-nix/os.git ] || exit 1
git -C "$repository" fetch --no-tags origin main
[ "$(git -C "$repository" rev-parse refs/remotes/origin/main)" = "$revision" ] || {
    echo 'Requested revision is no longer current main' >&2; exit 1;
}
release=/opt/nix-os/releases/$revision
if [ ! -d "$release" ]; then
    git -C "$repository" worktree add --detach "$release" "$revision"
fi
[ "$(git -C "$release" rev-parse HEAD)" = "$revision" ] || exit 1
[ -z "$(git -C "$release" status --porcelain --untracked-files=all)" ] || exit 1
exec python3 "$release/scripts/deploy-os.py" "$revision" "$release"
