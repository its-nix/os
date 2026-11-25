#!/usr/bin/env python3
"""Apply a verified main revision to existing native state; never prepare new homes."""

from datetime import datetime, timezone
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


# Runtime images may change through normal deployment; other pins are installed native inputs.
RUNTIMES = ('hermes', 'paperclip', 't3')
T3_UID = T3_GID = 10010


def t3_build_tag(source):
    return hashlib.sha256((source / 'deploy/t3/Dockerfile').read_bytes()).hexdigest()[:16]


def ensure_t3_image(source, run):
    """Build the pinned T3 image only when its Dockerfile-hash tag is absent."""
    tag = 'nixos-t3:' + t3_build_tag(source)
    # `image ls -q` exits 0 with empty output for a missing tag; a daemon error still raises.
    if not run(['docker', 'image', 'ls', '--quiet', tag]).strip():
        run(['docker', 'build', '--tag', tag, str(source / 'deploy/t3')])


TWENTY_DB_OWNER, TWENTY_APP_OWNER = (999, 999), (1000, 1000)  # postgres / node users of the pinned images


def crm_enabled(state):
    """Twenty runs only after an operator records that a venture needs a CRM."""
    flag = state / 'twenty' / 'ENABLED'
    return flag.is_file() and not flag.is_symlink()


def ensure_twenty_state(state, token=None):
    """Create missing Twenty directories and secrets; never modify existing ones."""
    import secrets as _secrets
    token = token or (lambda: _secrets.token_hex(32))
    root = state / 'twenty'
    if root.is_symlink():
        raise ValueError('Twenty state path is not a plain directory')
    for name, (uid, gid) in (('db', TWENTY_DB_OWNER), ('storage', TWENTY_APP_OWNER), ('redis', TWENTY_DB_OWNER)):
        path = root / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('Twenty state path is not a plain directory')
        if not path.exists():
            path.mkdir(mode=0o700)
            os.chown(path, uid, gid)
    db_env, server_env = root / 'db.env', root / 'server.env'
    if not db_env.exists() and not server_env.exists():
        if any((root / 'db').iterdir()):
            raise ValueError('Twenty database exists but its secrets are missing; restore them, never regenerate')
        password = token()
        for path, body in ((db_env, f'POSTGRES_DB=default\nPOSTGRES_USER=twenty\nPOSTGRES_PASSWORD={password}\n'),
                           (server_env, f'PG_DATABASE_URL=postgres://twenty:{password}@twenty-db:5432/default\n'
                                        f'APP_SECRET={token()}\nENCRYPTION_KEY={token()}\n')):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(body)
    elif not (db_env.exists() and server_env.exists()):
        raise ValueError('Twenty secrets are half-present; restore them from recovery, do not regenerate')


def ensure_t3_state(state):
    """New, empty T3 state directories only; existing state is never modified."""
    root = state / 't3'
    for path in (root, root / 'home', root / 'work'):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('T3 state path is not a plain directory')
        if not path.exists():
            path.mkdir(mode=0o700)
            os.chown(path, T3_UID, T3_GID)


ROOT_UID = ROOT_GID = 0
REVIEWER_ENV_TEMPLATE = '''# Rotating independent reviewer (non-secret settings). Secrets: reviewer/secrets/app.pem.
# REVIEW_APP_ID=<GitHub App id>
REVIEW_POOL=claude,codex,opencode,grok,antigravity
# OPENCODE_REVIEW_MODEL=opencode/<Go model id>  (default opencode/kimi-k3.1-pro, via the Go endpoint)
'''


def ensure_reviewer_state(source, state):
    """Reviewer state dirs (new ones only), non-secret settings template, and the deployment-owned
    reviewer script. The App private key is founder-provided in reviewer/secrets/app.pem."""
    root = state / 'reviewer'
    for path, owner, mode in ((root, (ROOT_UID, ROOT_GID), 0o755), (root / 'home', (T3_UID, T3_GID), 0o700),
                              (root / 'app', (ROOT_UID, ROOT_GID), 0o755), (root / 'secrets', (ROOT_UID, ROOT_GID), 0o700)):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('Reviewer state path is not a plain directory')
        if not path.exists():
            path.mkdir(mode=mode)
            os.chown(path, *owner)
    if not (root / 'reviewer.env').exists():
        _root_write(root, 'reviewer.env', REVIEWER_ENV_TEMPLATE, 0o644)
    if not (root / 'secrets' / 'seed').exists():
        import secrets as _secrets  # selection seed: a builder must not be able to predict its reviewer
        _root_write(root / 'secrets', 'seed', _secrets.token_hex(32) + '\n', 0o400)
    script = (source / 'deploy/reviewer/reviewer.mjs').read_text()
    current = root / 'app' / 'reviewer.mjs'
    changed = not current.is_file() or current.read_text() != script
    _root_write(root / 'app', 'reviewer.mjs', script, 0o644)
    return changed  # a bind-mounted script change does not recreate the container by itself


def _token_module():
    spec = importlib.util.spec_from_file_location('hermes_token', Path(__file__).resolve().parents[1] / 'deploy/t3/hermes_token.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def renew_t3_credential(state):
    module = _token_module()
    renewed = module.ensure(state / 'hermes')
    return module.share_with_leads(state / 'hermes') or renewed


def register_t3_for_leads(state, run):
    """Install T3 after sharing the credential, and verify Hermes enabled it."""
    endpoint = 'http://127.0.0.1:3773/mcp'
    pending = state / '.t3-registration-reload'
    for profile in _token_module().venture_profiles(state / 'hermes'):
        cli = ['docker', 'exec', '-u', '10000', 'nixos-hermes', 'hermes', '-p', profile.name]
        key = 'mcp_servers.t3'
        try:
            server = json.loads(run(cli + ['config', 'get', '--json', key]))
        except subprocess.CalledProcessError as exc:
            diagnostic = (exc.stdout or '') + (exc.stderr or '')
            if exc.returncode != 1 or diagnostic.strip() != f'Config key not set: {key}':
                raise
            server = None
        if server is not None:
            if server.get('url') != endpoint:
                raise ValueError('Venture T3 registration uses an unexpected endpoint')
            if server.get('enabled', True):
                continue
            # The old deployment saved exactly these fields after failed discovery.
            # Leave disabled entries with operator-supplied options or filters alone.
            if set(server) != {'url', 'headers', 'enabled'} or set(server['headers']) != {'Authorization'}:
                continue
            if '✓ Tools discovered:' not in run(cli + ['mcp', 'test', 't3']):
                raise ValueError('Hermes could not discover venture T3 tools')
        _root_write(state, pending.name, '', 0o600)
        if server is None:
            run(['docker', 'exec', '-i', '-u', '10000', 'nixos-hermes', 'sh', '-c',
                 'printf "Y\\nY\\n" | hermes -p "$1" mcp add t3 --url "$2" --auth header',
                 'register-t3', profile.name, endpoint])
        else:
            run(cli + ['config', 'set', '--force', key + '.enabled', 'true'])
        if run(cli + ['config', 'get', '--json', key + '.enabled']) != 'true':
            raise ValueError('Hermes did not enable the venture T3 registration')
    return pending


def confirm_t3_credential(state):
    _token_module().mark_loaded(state / 'hermes')


RECOVERY_UNITS = ('nixos-recovery.service', 'nixos-recovery.timer')


def sync_recovery_units(source, run, unit_dir=Path('/etc/systemd/system')):
    """Keep already-adopted recovery units equal to the reviewed release (one root writer)."""
    changed = False
    for name in RECOVERY_UNITS:
        installed, reviewed = unit_dir / name, source / 'automation/recovery' / name
        if not installed.exists():
            continue  # recovery not adopted on this host; enabling it stays an explicit step
        if installed.read_bytes() != reviewed.read_bytes():
            shutil.copyfile(reviewed, installed)
            installed.chmod(0o644)
            changed = True
    if changed:
        run(['systemctl', 'daemon-reload'])
    return changed


def native_inputs(source):
    versions = json.loads((source / 'deploy/versions.json').read_text())
    files = ['deploy/pilot.json', 'scripts/prepare-clean-os.py']
    files += ['deploy/roles/' + role + '.md' for role in ('lead', 'engineer', 'reviewer')]
    return {
        'dependencies': {k: v for k, v in versions.items() if k not in RUNTIMES},
        'instructions': {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files},
    }


# First-party OS skills. The deployment is the ONLY writer of each profile's `skills/nix-os/`
# category; NixKit owns its own skill directories. Copies (not links) pin the reviewed revision.
OS_SKILLS = ('venture-factory', 'venture-lead', 't3-delegate', 'crm-lead-ops', 'company-ops')
OS_SKILL_CATEGORY = 'nix-os'


def _plain_dir_within(path, root):
    """True only for a real directory reached from root without any symlink component."""
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            return False
    return path.resolve().is_relative_to(root.resolve())


def os_skill_targets(state):
    """Default (LNX) profile plus every profile created by the venture registry."""
    hermes = state / 'hermes'
    targets = [hermes / 'skills']
    profiles = hermes / 'profiles'
    if profiles.is_dir() and not profiles.is_symlink():
        for marker in sorted(profiles.glob('*/nix-venture.json')):
            if not marker.is_file() or marker.is_symlink():
                continue
            try:
                owner = json.loads(marker.read_text()).get('venture')
            except (OSError, ValueError):
                continue
            if owner == marker.parent.name:  # `profile create --clone-all` copies markers verbatim
                targets.append(marker.parent / 'skills')
    return [t for t in targets if _plain_dir_within(t, hermes)]


def sync_os_skills(source, state):
    owner = (state / 'hermes').stat()
    for skills in os_skill_targets(state):
        final, staged, old = (skills / OS_SKILL_CATEGORY, skills / ('.' + OS_SKILL_CATEGORY + '.next'),
                              skills / ('.' + OS_SKILL_CATEGORY + '.prev'))
        for path in (final, staged, old):
            if path.is_symlink():
                raise ValueError('The nix-os skill category paths must not be symlinks')
        if old.exists() and not final.exists():
            old.replace(final)  # a previous run died between its two renames: restore first
        for leftover in (staged, old):
            if leftover.exists():
                shutil.rmtree(leftover)
        staged.mkdir(mode=0o755)
        for name in OS_SKILLS:
            shutil.copytree(source / 'skills' / name, staged / name, symlinks=False)
        for path in [staged, *staged.rglob('*')]:
            os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)
        if final.exists() and not final.is_dir():
            raise ValueError('The nix-os skill category is not a deployment-owned directory')
        if final.exists():
            final.replace(old)
        try:
            staged.replace(final)
        except OSError:
            old.replace(final)
            raise
        if old.exists():
            shutil.rmtree(old)


def _root_write(directory, name, text, mode):
    """Root-safe write: refuse symlinked directories, random O_EXCL staging file, atomic rename."""
    import tempfile
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f'{directory.name} must be a real directory')
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.' + name + '.')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, directory / name)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def publish_health(state, receipt, recovery=Path('/var/lib/nixos-recovery')):
    """Non-secret status for the no-agent company health loop (Hermes cannot read root receipts).
    Best effort: a failure here never changes the deployment outcome."""
    def read(path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
    try:
        previous = read(state / 'deployment.json')
        success, attempt = read(recovery / 'last-success.json'), read(recovery / 'last-attempt.json')
        status = {'deployed_revision': previous.get('revision'), 'deployed_at': previous.get('completed_at'),
                  'deploy_attempt_status': receipt['status'], 'deploy_attempt_revision': receipt['revision'],
                  'backup_last_success': success.get('completed_at'),
                  'backup_last_attempt_status': attempt.get('status')}
        _root_write(state / 'hermes' / 'nix', 'health.json', json.dumps(status, indent=2) + '\n', 0o644)
    except (OSError, ValueError, KeyError):
        print('Health status publication failed; deployment outcome unchanged.', file=sys.stderr)


def install_health_script(source, state):
    """Deployment-owned copy of the health check for Hermes' no-agent cron (scripts/nix-os/)."""
    hermes = state / 'hermes'
    scripts = hermes / 'scripts'
    owner = hermes.stat()
    if scripts.is_symlink():
        raise ValueError('Hermes scripts path must not be a symlink')
    if not scripts.exists():
        scripts.mkdir(mode=0o755)
        os.chown(scripts, owner.st_uid, owner.st_gid)
    target = scripts / 'nix-os'
    if target.is_symlink():
        raise ValueError('Hermes scripts/nix-os must not be a symlink')
    target.mkdir(mode=0o755, exist_ok=True)
    _root_write(target, 'health.py', (source / 'skills/company-ops/scripts/health.py').read_text(), 0o755)


def apply(revision, source, state, current, run=run, t3_credential=renew_t3_credential,
          t3_loaded=confirm_t3_credential, units=sync_recovery_units):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('An exact commit SHA is required')
    if not state.is_dir() or state.is_symlink():
        raise ValueError('Existing native state root is required')
    candidate = state / ('.deployment-' + revision + '.env')
    attempt = {'revision': revision, 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'deploying'}
    write_json(state / 'deployment-attempt.json', attempt)
    try:
        if not all((state / name).is_dir() and not (state / name).is_symlink()
                   for name in ('hermes', 'knowledge/company')):
            raise ValueError('Existing native state and KB are required')
        if current.exists() and not current.is_symlink():
            raise ValueError('Operator must adopt the existing source directory as a symlink first')
        desired = native_inputs(source)
        if json.loads((state / 'installed-inputs.json').read_text()) != desired:
            raise ValueError('Native inputs changed; refresh KB, Kit, profiles or instructions explicitly first')
        versions = json.loads((source / 'deploy/versions.json').read_text())
        installed = json.loads((state / 'hermes/nix/versions.json').read_text())
        if {k: v for k, v in installed.items() if k not in RUNTIMES} != desired['dependencies']:
            raise ValueError('Installed native dependency pins do not match the reviewed source')
        for name, repository in [('hermes', 'nousresearch/hermes-agent')]:
            if not re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', versions[name]['image']):
                raise ValueError('Only digest-pinned official images may deploy')
        candidate.write_text(''.join(key + '=' + json.dumps(value) + '\n' for key, value in {
            'NIX_CLEAN_ROOT': str(state), 'HERMES_IMAGE': versions['hermes']['image'],
            'T3_BUILD_TAG': t3_build_tag(source),
        }.items()))
        ensure_t3_state(state)
        reviewer_changed = ensure_reviewer_state(source, state)
        candidate.chmod(0o600)
        crm = crm_enabled(state)
        if crm:
            ensure_twenty_state(state)
        else:
            # A disabled CRM must not keep writing: stop (never remove) any running Twenty container.
            running = set(run(['docker', 'ps', '--format', '{{.Names}}', '--filter', 'name=^nixos-twenty-']).split())
            for name in ('nixos-twenty-worker', 'nixos-twenty-server', 'nixos-twenty-db', 'nixos-twenty-redis'):
                if name in running:  # apps drain before their datastores stop
                    run(['docker', 'stop', '--time', '90', name])
        ensure_t3_image(source, run)
        run(['python3', str(source / 'scripts/deploy-os.py'), '--prepare-packs', revision, str(source)])
        compose = ['docker', 'compose', '--project-name', 'nixos', '--env-file', str(candidate),
                   '--file', str(source / 'deploy/compose.yaml'), '--file', str(state / 'packs.compose.json')] + (['--profile', 'crm'] if crm else [])
        run(compose + ['config', '--quiet'])
        run(compose + ['pull', '--ignore-buildable'])
        run(compose + ['up', '--detach', '--wait', '--wait-timeout', '300'])
        if reviewer_changed:  # the evaluator's new code must actually be the code that runs
            run(compose + ['up', '--detach', '--no-deps', '--force-recreate', 'reviewer'])
        # Paperclip is retired (no work since 2026-11-23): stop, never remove; its state stays under
        # NIX_CLEAN_ROOT/paperclip and in every recovery image for rollback.
        if run(['docker', 'ps', '--quiet', '--filter', 'name=^nixos-paperclip$']).strip():
            run(['docker', 'stop', '--time', '90', 'nixos-paperclip'])
        for name in ('hermes',):
            observed = run(['docker', 'inspect', '--format', '{{.Config.Image}} {{.State.Health.Status}}',
                            'nixos-' + name])
            if observed != versions[name]['image'] + ' healthy':
                raise ValueError('Native image or health readback differs from the declared deployment')
        if run(['docker', 'inspect', '--format', '{{.Config.Image}} {{.State.Health.Status}}', 'nixos-t3']) \
                != 'nixos-t3:' + t3_build_tag(source) + ' healthy':
            raise ValueError('T3 image or health readback differs from the declared deployment')
        if run(['docker', 'inspect', '--format', '{{.Config.Image}} {{.State.Status}}', 'nixos-reviewer']) \
                != 'nixos-t3:' + t3_build_tag(source) + ' running':
            raise ValueError('Reviewer image or state readback differs from the declared deployment')
        if crm and run(['docker', 'inspect', '--format', '{{.State.Health.Status}}', 'nixos-twenty-server']) != 'healthy':
            raise ValueError('Twenty CRM health readback failed')
        listeners = run(['ss', '-lntH']).splitlines()
        for port in ('18642', '19119', '3773') + (('3020',) if crm else ()):
            addresses = [line.split()[3] for line in listeners if len(line.split()) >= 5
                         and line.split()[3].rsplit(':', 1)[-1] == port]
            if addresses != ['127.0.0.1:' + port]:
                raise ValueError('A pilot listener is missing or is not exclusively loopback')
        run(['docker', 'exec', '-u', '10000', '-w', '/knowledge/company', 'nixos-hermes',
             '/opt/data/nix/kb/.venv/bin/python', '-m', 'unittest', 'discover', '-s', 'tests', '-q'])
        credential_changed = t3_credential(state)
        registration_pending = register_t3_for_leads(state, run)
        if credential_changed or registration_pending.exists():
            # Hermes reads MCP_T3_API_KEY from its private .env at start; renewals are ~monthly.
            run(['docker', 'restart', '--timeout', '60', 'nixos-hermes'])
            run(compose + ['up', '--detach', '--wait', '--wait-timeout', '300'])
            t3_loaded(state)  # only after a healthy restart; an interrupted run retries the restart
            registration_pending.unlink(missing_ok=True)
        units(source, run)
        sync_os_skills(source, state)
        install_health_script(source, state)
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
                       images={'hermes': versions['hermes']['image'], 't3': 'nixos-t3:' + t3_build_tag(source)})
        write_json(state / 'deployment.json', receipt)
        write_json(state / 'deployment-attempt.json', receipt)
        publish_health(state, receipt)
        print('Healthy deployment recorded: ' + revision)
    except Exception:
        write_json(state / 'deployment-attempt.json', dict(attempt, status='failed'))
        publish_health(state, dict(attempt, status='failed'))
        raise
    finally:
        candidate.unlink(missing_ok=True)


def prepare_packs(release, state, image):
    pack_dir = release / 'deploy/packs'
    kit = json.loads((release / 'deploy/versions.json').read_text())['kit']['revision']
    key = hashlib.sha256(kit.encode() + (pack_dir / 'packs.json').read_bytes() +
                         (pack_dir / 'build.py').read_bytes() + (pack_dir / 'worktree.py').read_bytes()).hexdigest()
    store = state / 'pack-releases'
    store.mkdir(mode=0o755, exist_ok=True)
    if store.is_symlink() or store.stat().st_uid != 0 or store.stat().st_mode & 0o022:
        raise ValueError('Unsafe pack release store')
    core = store / key
    runner = ['docker', 'run', '--rm', '--cap-drop=ALL', '--security-opt=no-new-privileges',
              '-v', f'{release}/deploy:/release/deploy:ro', '--entrypoint', 'python3']
    if not core.exists():
        with tempfile.TemporaryDirectory(dir=store) as tmp:
            archive = Path(tmp) / 'kit.tar'
            subprocess.run(['git', '-C', str(state / 'inputs/kit'), 'archive', '--format=tar',
                            '--output', archive, kit], check=True)
            archive.chmod(0o644)
            output = Path(tmp) / 'out'
            output.mkdir()
            os.chown(output, 65534, 65534)
            subprocess.run([*runner, '--user', '65534:65534', '-v', f'{archive}:/kit.tar:ro',
                            '-v', f'{output}:/out', image, '/release/deploy/packs/build.py',
                            '/kit.tar', '/out/core', '--role', 'engineering', '--runtime'], check=True)
            built = output / 'core'
            for entry in [built, *built.rglob('*')]:
                if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
                    raise ValueError('Invalid pack release entry')
                os.chown(entry, 0, 0)
                # Skillshare preserves directory modes when copying into a task worktree.
                # The store is root-owned and every harness mount is read-only.
                entry.chmod(0o755 if entry.is_dir() or entry.stat().st_mode & 0o111 else 0o644)
            built.rename(core)
    volumes = {}
    for service, uid, home in [('hermes', '10000', state / 'hermes'), ('t3', '10010', state / 't3/home')]:
        raw = subprocess.check_output([*runner, '--read-only', '--network=none', '--user', uid,
                                      '-v', f'{home}:/native:ro', '-v', f'{core}:/core:ro',
                                      image, '/release/deploy/packs/mounts.py', service])
        names = json.loads(raw)
        allowed = set(json.loads((pack_dir / 'packs.json').read_text())['packs']['core'])
        if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r'[a-z0-9-]+', n)
                                              or n not in allowed for n in names):
            raise ValueError('Invalid core mount selection')
        target = '/opt/data/skills/nixkit' if service == 'hermes' else '/var/lib/t3/.agents/skills'
        volumes[service] = {'volumes': [
            {'type': 'bind', 'source': str(core / name), 'target': target + '/' + name, 'read_only': True,
             'bind': {'create_host_path': False}} for name in names]}
    volumes['t3']['volumes'].append({'type': 'bind', 'source': str(core), 'target': '/opt/nixkit',
                                     'read_only': True, 'bind': {'create_host_path': False}})
    override = state / 'packs.compose.json'
    with tempfile.NamedTemporaryFile(dir=store, mode='w', delete=False) as tmp:
        json.dump({'services': volumes}, tmp)
    os.replace(tmp.name, override)


def main():
    pack_build = sys.argv[1:2] == ['--prepare-packs']
    if pack_build:
        del sys.argv[1]
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-f]{40}', sys.argv[1]):
        raise ValueError('Deployment requires an exact verified revision and its root-owned release')
    revision, source = sys.argv[1], Path(sys.argv[2]).resolve()
    if source != Path('/opt/nix-os/releases') / revision or source != Path(__file__).resolve().parents[1]:
        raise ValueError('Deployment source must be the verified release')
    if pack_build:
        prepare_packs(source, Path('/srv/nix-clean'), 'nixos-t3:' + t3_build_tag(source))
        return
    apply(revision, source, Path('/srv/nix-clean'), Path('/opt/nix-clean-source'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Native deployment command failed; inspect the VPS locally'
        print('Deployment failed: ' + message, file=sys.stderr)
        sys.exit(1)
