#!/usr/bin/env python3
"""Create a verified private Hermes recovery image. Defaults to preview."""

import argparse
import base64
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location('hermes_recovery', HERE / 'recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
Refusal = recovery.Refusal


def run(command, **kwargs):
    """Never forward subprocess diagnostics: they may contain private state."""
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=3600, **kwargs)
    if result.returncode:
        raise Refusal('A required command failed; no new image is approved for publication')
    return result.stdout.strip()


def check_coverage(home, members):
    """Native backup skips links; refuse silently losing durable user context."""
    roots = [home]
    profiles = home / 'profiles'
    if profiles.is_symlink():
        raise Refusal('Linked profile roots require materialization before backup')
    if profiles.is_dir():
        roots.extend(profiles.iterdir())
    for root in roots:
        if root.is_symlink():
            raise Refusal('Linked profiles require materialization before backup')
        for name in ('skills', 'memories', 'sessions'):
            directory = root / name
            if directory.is_symlink():
                raise Refusal('Linked context requires materialization before backup')
            for path in directory.rglob('*'):
                rel = path.relative_to(home).as_posix()
                if {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.cache'} & set(path.parts):
                    continue
                if path.is_symlink():
                    raise Refusal('Linked context requires materialization before backup')
                if path.is_file() and members.get(rel) != recovery.digest(path):
                    raise Refusal('Durable context missing or changed during backup')


def check_runtime(image, revision):
    actual = run(['docker', 'image', 'inspect', image, '--format',
                  '{{index .Config.Labels "org.opencontainers.image.revision"}}'])
    if actual != revision:
        raise Refusal('Runtime image revision does not match the installed Hermes revision')
    marker = run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                  '--entrypoint', 'cat', image, '/opt/hermes/.hermes_build_sha'])
    if marker != revision:
        raise Refusal('Runtime source revision marker does not match')


def require_private(image):
    owner, package = image.removeprefix('ghcr.io/').split('/', 1)
    package = package.split(':', 1)[0]
    metadata = json.loads(run(['gh', 'api', f'/orgs/{owner}/packages/container/{package}']))
    if metadata.get('visibility') != 'private':
        raise Refusal('Destination container package must already exist and be private')


def verify_and_publish(tag, work, publish):
    run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
         '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
         '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777', tag, 'verify'])
    if not publish:
        return
    image = tag.rsplit(':', 1)[0]
    require_private(image)
    # Only push uses this ephemeral login; never replace the source host's Docker login.
    auth = work / 'docker-auth'
    auth.mkdir(mode=0o700)
    token = run(['gh', 'auth', 'token'])
    actor = os.environ.get('GITHUB_ACTOR') or run(['gh', 'api', 'user', '--jq', '.login'])
    encoded = base64.b64encode(f'{actor}:{token}'.encode()).decode()
    config = auth / 'config.json'
    config.write_text(json.dumps({'auths': {'ghcr.io': {'auth': encoded}}}))
    config.chmod(0o600)
    endpoint = os.environ.get('DOCKER_HOST') or run([
        'docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
    run(['docker', '--config', str(auth), '--host', endpoint, 'push', tag])
    require_private(image)


def build(args):
    home = args.home.expanduser().resolve()
    source = args.hermes_source.expanduser().resolve()
    if not (home / 'config.yaml').is_file():
        raise Refusal('An existing default Hermes home is required')
    if run(['git', '-C', str(source), 'status', '--porcelain']):
        raise Refusal('Hermes source must be clean')
    revision = run(['git', '-C', str(source), 'rev-parse', 'HEAD'])
    version = run([args.hermes_bin, '--version'])
    match = re.search(r'upstream ([0-9a-f]{7,40})', version)
    if not match or not revision.startswith(match.group(1)):
        raise Refusal('Hermes executable does not match its source revision')
    check_runtime(args.runtime_image, revision)
    if args.publish:
        require_private(args.image)
    # TemporaryDirectory is mode 0700; never stage state inside the checkout.
    with tempfile.TemporaryDirectory(prefix='hermes-recovery-') as td:
        work = Path(td)
        env = dict(os.environ, HERMES_HOME=str(home))
        env.pop('HERMES_PROFILE', None)
        output = run([args.hermes_bin, 'backup', '-o', str(work / 'snapshot.zip'), '--keep', '0'], env=env)
        if re.search(r'Skipped \d+ memory-provider path', output):
            raise Refusal('External memory state outside the user home was skipped')
        members = recovery.inspect_archive(work / 'snapshot.zip')
        check_coverage(home, members)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        manifest = {
            'schema_version': 1, 'created_at': timestamp,
            'hermes_revision': revision, 'runtime_image': args.runtime_image,
            'os_revision': run(['git', '-C', str(REPO), 'rev-parse', 'HEAD']),
            'skill_policy_sha256': recovery.digest(REPO / 'skills/packs.json'),
            'archive_sha256': recovery.digest(work / 'snapshot.zip'),
            'file_count': len(members),
        }
        (work / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        for name in ('Dockerfile', 'recovery.py'):
            shutil.copyfile(HERE / name, work / name)
        tag = f'{args.image}:{timestamp}'
        run(['docker', 'build', '--network', 'none', '--build-arg',
             f'HERMES_IMAGE={args.runtime_image}', '--tag', tag, str(work)])
        verify_and_publish(tag, work, args.publish)
        print(f'Verified recovery image: {tag}' + (' (published privately)' if args.publish else ' (local only)'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True, type=Path, help='Default Hermes root, containing all profiles')
    parser.add_argument('--hermes-source', required=True, type=Path, help='Installed clean Hermes Git checkout')
    parser.add_argument('--hermes-bin', default='hermes')
    parser.add_argument('--runtime-image', required=True, help='Matching Hermes image pinned by digest')
    parser.add_argument('--image', required=True, help='GHCR image without a tag')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', args.runtime_image):
        parser.error('--runtime-image must use an immutable sha256 digest')
    if not re.fullmatch(r'ghcr\.io/[a-z0-9-]+/[a-z0-9][a-z0-9._-]*', args.image):
        parser.error('--image must be ghcr.io/owner/package without a tag')
    if args.publish and not args.apply:
        parser.error('--publish requires --apply')
    if not args.apply:
        print('Preview: full Hermes backup → pinned runtime → isolated restore check'
              + (' → private GHCR push.' if args.publish else '. No commands executed.'))
        return 0
    try:
        build(args)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Recovery build refused; no new recovery tag should be used. Check source, runtime revision,'
              ' Docker, and private package access. Private subprocess output was suppressed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
