#!/usr/bin/env python3
"""Capture, restore-check and privately publish the running native OS. Preview by default."""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location('full_restore', HERE / 'full_restore.py')
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)
Refusal = state.Refusal
CONTAINERS = ['nixos-hermes', 'nixos-t3', 'nixos-reviewer']  # Paperclip retired: archived state only
# Optional CRM: stopped app-first, started database-first.
CRM_CONTAINERS = ['nixos-twenty-worker', 'nixos-twenty-server', 'nixos-twenty-db', 'nixos-twenty-redis']
# Probe image for a restored Twenty database, independent of whether the profile is active now
# (a test keeps it equal to deploy/compose.yaml).
TWENTY_DB_IMAGE = 'postgres@sha256:ce3efec6bc404f6b15ebf5d9bed6b45a9d4655aea238d7dacc3467ccb05a1175'


def crm_enabled(root=Path('/srv/nix-clean')):
    flag = root / 'twenty' / 'ENABLED'
    return flag.is_file() and not flag.is_symlink()


def containers(root=Path('/srv/nix-clean')):
    return CONTAINERS + (CRM_CONTAINERS if crm_enabled(root) else [])


def twenty_db_check(image, restored):
    """Open the restored Twenty PostgreSQL in single-user mode, no network."""
    output = run(['docker', 'run', '--rm', '--interactive', '--network', 'none', '--user', '999:999',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
                  '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777', '--tmpfs', '/var/run/postgresql:rw,mode=1777',
                  '--mount', f'type=bind,source={restored / "twenty/db"},target=/var/lib/postgresql/data',
                  '--entrypoint', 'postgres', image, '--single', '-D', '/var/lib/postgresql/data', 'default'],
                 input="SELECT 'NIXOS_TWENTY_DB_OK';\n")
    if 'NIXOS_TWENTY_DB_OK' not in output:
        raise Refusal('Restored Twenty database did not open')


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=1800, **kwargs)
    if result.returncode:
        # Name only the program and its subcommand (e.g. "docker push"): never arguments or output.
        name = ' '.join(a for a in argv[:2] if re.fullmatch(r'[a-z][a-z-]*', a))
        raise Refusal(f'A native recovery command failed ({name or "unknown"}, exit {result.returncode}); '
                      'private diagnostics suppressed')
    return result.stdout.strip()


def image_parts(image):
    match = re.fullmatch(r'docker\.io/([a-z0-9][a-z0-9_-]*)/([a-z0-9][a-z0-9._-]*)', image)
    if not match:
        raise Refusal('Use docker.io/your-account/repository without a tag')
    return match.groups()


class Hub:
    """Read the selected native Docker store; never print or persist API tokens."""

    def __init__(self):
        config = Path(os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker'))) / 'config.json'
        data = json.loads(config.read_text())
        server = 'https://index.docker.io/v1/'
        helper = data.get('credHelpers', {}).get(server) or data.get('credsStore')
        if helper:
            entry = json.loads(run(['docker-credential-' + helper, 'get'], input=server))
            username, secret = entry['Username'], entry['Secret']
        else:
            entry = next((v for k, v in data.get('auths', {}).items() if k in
                          (server, 'docker.io', 'index.docker.io', 'registry-1.docker.io')), {})
            if entry.get('auth'):
                username, secret = base64.b64decode(entry['auth']).decode().split(':', 1)
            else:
                username, secret = '<token>', entry.get('identitytoken', '')
        if not secret:
            raise Refusal('Docker Hub login is required in the selected native Docker store')
        self._identifier, self._secret = username, secret
        self.token = secret
        try:
            self.username = self.request('GET', '/v2/user/')['username']
        except Refusal:
            if username == '<token>':
                raise Refusal('Hub management needs a PAT login: docker login --username YOUR_ACCOUNT') from None
            reply = self.request('POST', '/v2/auth/token', {'identifier': username, 'secret': secret}, authenticated=False)
            self.token = reply['access_token']
            self.username = self.request('GET', '/v2/user/')['username']

    def request(self, method, path, body=None, *, authenticated=True):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request('https://hub.docker.com' + path, method=method, headers=headers,
                                         data=json.dumps(body).encode() if body is not None else None)
        for attempt in range(2):
            if authenticated:
                request.headers['Authorization'] = 'Bearer ' + self.token
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and authenticated and attempt == 0 and self._identifier != '<token>':
                    reply = self.request('POST', '/v2/auth/token',
                                         {'identifier': self._identifier, 'secret': self._secret}, authenticated=False)
                    self.token = reply['access_token']
                    continue
                raise Refusal(f'Docker Hub request failed (HTTP {exc.code}); no private diagnostics logged') from None


def require_private(image, api):
    owner, name = image_parts(image)
    result = api('GET', f'/v2/namespaces/{owner}/repositories/{name}')
    if result.get('is_private') is not True:
        raise Refusal('Recovery repository must exist and be explicitly private')


def create_private(image, hub):
    owner, name = image_parts(image)
    if owner != hub.username:
        raise Refusal('Recovery repository must belong to the authenticated personal account')
    path = f'/v2/namespaces/{owner}/repositories/{name}'
    try:
        require_private(image, hub.request)
        return
    except Refusal as exc:
        if 'HTTP 404' not in str(exc):
            raise
    hub.request('POST', f'/v2/namespaces/{owner}/repositories',
                {'name': name, 'namespace': owner, 'registry': 'docker.io', 'is_private': True,
                 'description': 'Private daily Nix OS state and recovery snapshots'})
    require_private(image, hub.request)


HEALTH = '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
# Graceful exits after `docker stop`: 0; 130 (T3 under tini, observed 2026-11-23); 143 (SIGTERM,
# e.g. the Twenty queue worker). 137 (SIGKILL after the timeout) is never clean.
CLEAN_EXIT = {'exited 0', 'exited 130', 'exited 143'}


def ready(name):
    """Healthy when a healthcheck exists; running for services without one (Twenty worker)."""
    return run(['docker', 'inspect', '--format', HEALTH, name]) in ('healthy', 'running')


# A host auto-fixer outside the OS (server-monitor.service) runs `docker system prune -f` every
# 15s while / is above 90%, deleting stopped containers. A cold capture stops every OS service and
# itself writes several GB, so it refuses to start without headroom (incident 2026-11-25).
DISK_LIMIT_PERCENT = 85


def disk_used_percent(path='/'):
    """Use% exactly as df (and the host monitor) reports it: used / (used + available to
    non-root), rounded up. Root-reserved blocks are excluded, so this is never lower than df."""
    st = os.statvfs(path)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    visible = used + st.f_bavail * st.f_frsize
    return -(-100 * used // visible) if visible else 100


def recreate_missing(root=Path('/srv/nix-clean')):
    """Recreate deleted OS containers from the deployed release; existing ones are left alone."""
    source = Path('/opt/nix-clean-source').resolve()
    run(['docker', 'compose', '-p', 'nixos', '--env-file', str(root / 'deployment.env'),
         '-f', str(source / 'deploy/compose.yaml'), *(['--profile', 'crm'] if crm_enabled(root) else []),
         'up', '--detach', '--no-recreate'])


def start_all(names, root=Path('/srv/nix-clean')):
    """Attempt every start (databases first), then report; one failure never strands the rest."""
    failed = []
    for name in names:
        try:
            run(['docker', 'start', name])
        except Refusal:
            failed.append(name)
    if failed:
        # A container may have been deleted while stopped; state is all bind-mounted, so
        # recreating it from the deployed compose file restores the service as it was.
        try:
            recreate_missing(root)
        except Refusal:
            pass
        retry, failed = failed, []
        for name in retry:
            try:
                run(['docker', 'start', name])
            except Refusal:
                failed.append(name)
    if failed:
        raise Refusal('Some services did not start after capture: ' + ', '.join(failed))


def wait_services(names=None):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if all(ready(name) for name in (names or containers())):
            return
        time.sleep(2)
    raise Refusal('Native service health did not recover after capture')


def crm_writers_running():
    return [name for name in CRM_CONTAINERS
            if run(['docker', 'ps', '--quiet', '--filter', f'name=^{name}$']).strip()]


@contextmanager
def stopped_services(lock_path=Path('/run/lock/nix-os-deploy.lock'), root=Path('/srv/nix-clean')):
    # The same lock serializes CI/CD. No archive is ever copied from live database writers.
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        names = containers(root)
        used = disk_used_percent()
        if used >= DISK_LIMIT_PERCENT:
            raise Refusal(f'Disk is {used:.0f}% full; a cold capture needs it below {DISK_LIMIT_PERCENT}% '
                          '(the host auto-pruner deletes stopped containers above 90%)')
        if not crm_enabled(root) and crm_writers_running():
            raise Refusal('CRM containers run while CRM is disabled; reconcile with a deployment first')
        statuses = [run(['docker', 'inspect', '--format', '{{.State.Status}}', name]) for name in names]
        if statuses != ['running'] * len(names):
            raise Refusal('All OS services must be running before capture')
        try:
            for name in names:  # sequential: apps drain before the databases they use stop
                run(['docker', 'stop', '--time', '90', name])
            for name in names:
                if run(['docker', 'inspect', '--format', '{{.State.Status}} {{.State.ExitCode}}', name]) not in CLEAN_EXIT:
                    raise Refusal('Native services did not stop cleanly')
            yield
        finally:
            # Even a failed/partial stop must not leave the company OS down. Databases start first.
            start_all(list(reversed(names)), root)
            wait_services(names)


def deployment_model(root, versions, deployed_source):
    env = dict(os.environ, NIX_CLEAN_ROOT=str(root), HERMES_IMAGE=versions['hermes']['image'],
               T3_BUILD_TAG=hashlib.sha256((deployed_source / 'deploy/t3/Dockerfile').read_bytes()).hexdigest()[:16])
    model = json.loads(run(['docker', 'compose', '-f', str(deployed_source / 'deploy/compose.yaml'),
                            *(['--profile', 'crm'] if crm_enabled(root) else []),
                            'config', '--format', 'json'], env=env))
    for name in ('hermes', 't3', 'reviewer'):
        actual = json.loads(run(['docker', 'inspect', 'nixos-' + name]))[0]
        expected = versions.get(name, {}).get('image') or model['services'][name]['image']  # t3/reviewer: local build
        if actual['Config']['Image'] != expected:
            raise Refusal('Running runtime differs from the immutable OS image pins')
        environment = dict(row.split('=', 1) for row in actual['Config']['Env'] if '=' in row)
        model['services'][name]['environment'] = environment
        expected_mounts = {(str(Path(v['source']).resolve()), v['target'], not v.get('read_only', False))
                           for v in model['services'][name].get('volumes', [])}
        actual_mounts = {(str(Path(v['Source']).resolve()), v['Destination'], v['RW'])
                         for v in actual['Mounts'] if v['Type'] == 'bind'}
        if expected_mounts != actual_mounts or len(actual['Mounts']) != len(actual_mounts):
            raise Refusal('Actual native mounts differ from the captured deployment')
        for volume in model['services'][name].get('volumes', []):
            if volume.get('type') != 'bind' or not Path(volume['source']).is_relative_to(root):
                raise Refusal('A deployment mount is outside the captured native root')
    return model


def postgres_check(image, restored):
    command = 'postgres=$(find /app/node_modules -type f -path "*/native/bin/postgres" -print -quit); test -n "$postgres"; export LD_LIBRARY_PATH="$(dirname "$postgres")/../lib:${LD_LIBRARY_PATH:-}"; exec "$postgres" --single -D /paperclip/instances/default/db paperclip'
    sql = "SELECT CASE WHEN (SELECT count(*) FROM companies)>0 AND (SELECT count(*) FROM agents)>0 THEN 'NIXOS_RECOVERY_DB_OK' ELSE 'NIXOS_RECOVERY_DB_EMPTY' END;\n"
    output = run(['docker', 'run', '--rm', '--interactive', '--network', 'none', '--user', '1000:1000',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
                  '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777', '--mount',
                  f'type=bind,source={restored / "paperclip"},target=/paperclip',
                  '--entrypoint', '/bin/sh', image, '-c', command], input=sql)
    if 'NIXOS_RECOVERY_DB_OK' not in output:
        raise Refusal('Restored Paperclip database did not validate its company and agent rows')


def tree_fingerprint(top):
    """Content hash of a restored tree: paths, types, link targets and file bytes (not mtimes)."""
    total = hashlib.sha256()
    for path in sorted(top.rglob('*')):
        rel = path.relative_to(top).as_posix()
        if path.is_symlink():
            total.update(b'L\0' + rel.encode() + b'\0' + os.readlink(path).encode() + b'\0')
        elif path.is_file():
            total.update(b'F\0' + rel.encode() + b'\0' + state.digest(path).encode() + b'\0')
        elif path.is_dir():
            total.update(b'D\0' + rel.encode() + b'\0')
    return total.hexdigest()


def paperclip_check(image, restored, validated=None):
    """Archived Paperclip state is frozen (retired 2026-11-23). Its database is opened with the
    17 GB Paperclip image only when the restored bytes differ from the last validated copy, so
    the image need not stay on disk; docker run pulls the pinned digest when a check is due."""
    fingerprint = tree_fingerprint(restored / 'paperclip')
    record = {'image': image, 'fingerprint': fingerprint}
    if validated and validated.is_file():
        try:
            if json.loads(validated.read_text()) == record:
                return False
        except (OSError, ValueError):
            pass
    postgres_check(image, restored)
    if validated:
        receipt(validated, record)
    return True


def verify_payload(payload, work, versions, validated=None):
    manifest = json.loads((payload / 'manifest.json').read_text())
    restored = work / ('restored-' + uuid.uuid4().hex)
    state.restore_bundle(payload, restored)
    if (restored / 'state/paperclip/instances/default/db/PG_VERSION').is_file():
        # archived Paperclip stays restorable
        paperclip_check(versions['paperclip']['image'], restored / 'state', validated)
    if (restored / 'state/twenty/db/PG_VERSION').is_file():
        twenty_db_check(TWENTY_DB_IMAGE, restored / 'state')
    return manifest


def make_image(tag, payload, versions):
    holder = 'nixos-recovery-holder-' + uuid.uuid4().hex
    try:
        run(['docker', 'create', '--name', holder, '--network', 'none', versions['hermes']['image']])
        # docker commit excludes mounted volumes: the archive is explicitly copied into a NEW
        # holder's unmounted writable layer, never committed from a live application container.
        run(['docker', 'cp', str(payload), holder + ':/opt/nix-recovery'])
        run(['docker', 'commit', '--change', 'USER root', '--change',
             'ENTRYPOINT ["/opt/hermes/.venv/bin/python","/opt/nix-recovery/full_restore.py"]',
             '--change', 'CMD ["verify"]', '--change', 'LABEL org.its-nix.recovery=true', holder, tag])
    finally:
        run(['docker', 'rm', '-v', holder])


def verify_image(image):
    run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
         '--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'FOWNER',
         '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777', image, 'verify'])


def remote_readback(image, work, versions, validated=None):
    run(['docker', 'pull', '--quiet', image])
    digests = json.loads(run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', image]))
    repository = image.rsplit(':', 1)[0].removeprefix('docker.io/')
    ref = next((d for d in digests if d.removeprefix('docker.io/').startswith(repository + '@sha256:')), None)
    if not ref:
        raise Refusal('Published recovery manifest digest was not read back')
    verify_image(ref)
    holder = 'nixos-recovery-readback-' + uuid.uuid4().hex
    payload = work / 'remote-payload'
    try:
        run(['docker', 'create', '--name', holder, '--network', 'none', ref])
        run(['docker', 'cp', holder + ':/opt/nix-recovery', str(payload)])
        verify_payload(payload, work, versions, validated)
    finally:
        run(['docker', 'rm', '-v', holder])
    return ref


def receipt(path, data):
    temporary = path.with_suffix('.next')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def build(args):
    root = args.root.resolve()
    if root.is_relative_to(REPO) or REPO.is_relative_to(root):
        raise Refusal('State and backup work must be outside the source checkout')
    status = args.status.resolve()
    if status.is_relative_to(root) or status.is_relative_to(REPO):
        raise Refusal('Backup work and receipts must be outside captured state and source')
    args.status.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    tag = (args.image if args.publish else 'nixos-recovery-local') + ':' + timestamp
    attempt = {'started_at': timestamp, 'image': tag, 'status': 'running'}
    receipt(args.status / 'last-attempt.json', attempt)
    try:
        hub = Hub() if args.publish else None
        if hub:
            if image_parts(args.image)[0] != hub.username:
                raise Refusal('Destination is not the authenticated personal account')
            create_private(args.image, hub) if args.create_repository else require_private(args.image, hub.request)
        with tempfile.TemporaryDirectory(prefix='nixos-recovery-', dir=args.status) as td:
            work = Path(td)
            payload = work / 'payload'
            payload.mkdir(mode=0o700)
            with stopped_services(root=root):
                versions = json.loads((root / 'versions.json').read_text())
                # Resolve after taking the shared lock: a deployment may have advanced
                # the source pointer while this backup process was waiting.
                deployed_source = Path('/opt/nix-clean-source').resolve()
                revision = run(['git', '-C', str(deployed_source), 'rev-parse', 'HEAD'])
                if (root / 'source-revision').read_text().strip() != revision:
                    raise Refusal('Deployed source and native state revision differ')
                model = deployment_model(root, versions, deployed_source)
                (payload / 'deployment.json').write_text(json.dumps(model))
                files = state.snapshot(root, payload / 'snapshot.tar.gz')
            manifest = {'schema_version': 1, 'created_at': timestamp, 'state_root': str(root),
                        'os_revision': revision,
                        'backup_tool_revision': run(['git', '-C', str(REPO), 'rev-parse', 'HEAD']), 'versions': versions,
                        'archive_sha256': state.digest(payload / 'snapshot.tar.gz'), 'files': files,
                        'deployment_sha256': state.digest(payload / 'deployment.json')}
            (payload / 'manifest.json').write_text(json.dumps(manifest))
            for name in ['full_restore.py', 'recovery.py']:
                shutil.copyfile(HERE / name, payload / name)
            validated = args.status / 'paperclip-validated.json'
            verify_payload(payload, work, versions, validated)
            make_image(tag, payload, versions)
            verify_image(tag)
            if hub:
                require_private(args.image, hub.request)
                run(['docker', 'push', tag])
                require_private(args.image, hub.request)
                remote_digest = remote_readback(tag, work, versions, validated)
                require_private(args.image, hub.request)
                result = dict(attempt, status='published', manifest_digest=remote_digest,
                              completed_at=datetime.now(timezone.utc).isoformat(), entries=len(files))
                receipt(args.status / 'last-success.json', result)
            else:
                result = dict(attempt, status='verified_local', entries=len(files))
                receipt(args.status / 'last-local-check.json', result)
            receipt(args.status / 'last-attempt.json', result)
            print('Full OS recovery ' + result['status'] + ': ' + tag)
    except Exception as exc:
        failed = dict(attempt, status='failed')
        if isinstance(exc, Refusal):
            # Refusal messages are authored, fixed, non-secret strings; record them so the
            # next failure is diagnosable. Arbitrary exceptions/stderr are never recorded.
            failed['reason'] = str(exc)
        receipt(args.status / 'last-attempt.json', failed)
        raise
    finally:
        # No BuildKit cache is created. Remove only this job's local recovery tag/layer;
        # cleanup failure must not relabel a verified remote backup as unsuccessful.
        try:
            result = subprocess.run(['docker', 'image', 'rm', tag], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is None or result.returncode not in (0, 1):
            print('Local recovery-image cleanup needs attention; private output suppressed.', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/srv/nix-clean'))
    parser.add_argument('--status', type=Path, default=Path('/var/lib/nixos-recovery'))
    parser.add_argument('--image', help='docker.io/account/nixos-recovery')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--create-repository', action='store_true')
    args = parser.parse_args(argv)
    if not args.apply:
        print('Preview: cold native state capture, isolated restore/database checks, and private publication if requested.')
        return 0
    try:
        if args.publish:
            image_parts(args.image or '')
        build(args)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print('Full OS backup failed; inspect native login/state and last-attempt receipt. Private diagnostics suppressed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
