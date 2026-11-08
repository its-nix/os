#!/usr/bin/env python3
"""Apply a verified main revision to existing native state; never prepare new homes."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def run(argv):
    # Native output can contain private knowledge or configuration. Keep it off CI logs.
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()


def write_json(path, payload):
    temporary = path.with_suffix(path.suffix + '.next')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.chmod(0o600)
    if path.exists():
        stat = path.stat()
        os.chown(temporary, stat.st_uid, stat.st_gid)
    temporary.replace(path)


def native_inputs(source):
    versions = json.loads((source / 'deploy/versions.json').read_text())
    files = ['deploy/pilot.json', 'scripts/prepare-clean-os.py']
    files += ['deploy/roles/' + role + '.md' for role in ('lead', 'engineer', 'reviewer')]
    return {
        'dependencies': {k: v for k, v in versions.items() if k not in ('hermes', 'paperclip')},
        'instructions': {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files},
    }


def apply(revision, source, state, current, run=run):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('An exact commit SHA is required')
    if not state.is_dir() or state.is_symlink():
        raise ValueError('Existing native state root is required')
    candidate = state / ('.deployment-' + revision + '.env')
    attempt = {'revision': revision, 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'deploying'}
    write_json(state / 'deployment-attempt.json', attempt)
    try:
        if not all((state / name).is_dir() and not (state / name).is_symlink()
                   for name in ('hermes', 'paperclip', 'knowledge/company')):
            raise ValueError('Existing native state and KB are required')
        if current.exists() and not current.is_symlink():
            raise ValueError('Operator must adopt the existing source directory as a symlink first')
        desired = native_inputs(source)
        if json.loads((state / 'installed-inputs.json').read_text()) != desired:
            raise ValueError('Native inputs changed; refresh KB, Kit, profiles or instructions explicitly first')
        versions = json.loads((source / 'deploy/versions.json').read_text())
        installed = json.loads((state / 'hermes/nix/versions.json').read_text())
        if {k: v for k, v in installed.items() if k not in ('hermes', 'paperclip')} != desired['dependencies']:
            raise ValueError('Installed native dependency pins do not match the reviewed source')
        for name, repository in [('hermes', 'nousresearch/hermes-agent'), ('paperclip', 'ghcr.io/paperclipai/paperclip')]:
            if not re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', versions[name]['image']):
                raise ValueError('Only digest-pinned official images may deploy')
        candidate.write_text(''.join(key + '=' + json.dumps(value) + '\n' for key, value in {
            'NIX_CLEAN_ROOT': str(state), 'HERMES_IMAGE': versions['hermes']['image'],
            'PAPERCLIP_IMAGE': versions['paperclip']['image'],
        }.items()))
        candidate.chmod(0o600)
        compose = ['docker', 'compose', '--project-name', 'nix-clean', '--env-file', str(candidate),
                   '--file', str(source / 'deploy/compose.yaml')]
        run(compose + ['config', '--quiet'])
        run(compose + ['pull'])
        run(compose + ['up', '--detach', '--wait', '--wait-timeout', '300'])
        for name in ('hermes', 'paperclip'):
            observed = run(['docker', 'inspect', '--format', '{{.Config.Image}} {{.State.Health.Status}}',
                            'nix-clean-' + name + '-1'])
            if observed != versions[name]['image'] + ' healthy':
                raise ValueError('Native image or health readback differs from the declared deployment')
        listeners = run(['ss', '-lntH']).splitlines()
        for port in ('18642', '19119', '13100'):
            addresses = [line.split()[3] for line in listeners if len(line.split()) >= 5
                         and line.split()[3].rsplit(':', 1)[-1] == port]
            if addresses != ['127.0.0.1:' + port]:
                raise ValueError('A pilot listener is missing or is not exclusively loopback')
        run(['docker', 'exec', '-u', '10000', '-w', '/knowledge/company', 'nix-clean-hermes-1',
             '/opt/data/nix/kb/.venv/bin/python', '-m', 'unittest', 'discover', '-s', 'tests', '-q'])
        link = current.with_name(current.name + '.next')
        if link.is_symlink():
            link.unlink()  # Recover a temporary pointer left by an interrupted deployment.
        link.symlink_to(source)
        link.replace(current)
        candidate.replace(state / 'deployment.env')
        write_json(state / 'hermes/nix/versions.json', versions)
        write_json(state / 'versions.json', versions)
        (state / 'source-revision').write_text(revision + '\n')
        receipt = dict(attempt, status='healthy', completed_at=datetime.now(timezone.utc).isoformat(),
                       images={name: versions[name]['image'] for name in ('hermes', 'paperclip')})
        write_json(state / 'deployment.json', receipt)
        write_json(state / 'deployment-attempt.json', receipt)
        print('Healthy deployment recorded: ' + revision)
    except Exception:
        write_json(state / 'deployment-attempt.json', dict(attempt, status='failed'))
        raise
    finally:
        candidate.unlink(missing_ok=True)


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-f]{40}', sys.argv[1]):
        raise ValueError('Deployment requires an exact verified revision and its root-owned release')
    revision, source = sys.argv[1], Path(sys.argv[2]).resolve()
    if source != Path('/opt/nix-os/releases') / revision or source != Path(__file__).resolve().parents[1]:
        raise ValueError('Deployment source must be the verified release')
    apply(revision, source, Path('/srv/nix-clean'), Path('/opt/nix-clean-source'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Native deployment command failed; inspect the VPS locally'
        print('Deployment failed: ' + message, file=sys.stderr)
        sys.exit(1)
