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
import stat
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
                              (root / 'app', (ROOT_UID, ROOT_GID), 0o755), (root / 'secrets', (ROOT_UID, ROOT_GID), 0o700),
                              (root / 'approvals', (ROOT_UID, ROOT_GID), 0o700)):
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
    changed = False
    for relative in ('deploy/reviewer/reviewer.mjs', '.github/CODEOWNERS'):
        payload = (source / relative).read_text()
        current = root / 'app' / Path(relative).name
        if not current.is_file() or current.is_symlink() or current.read_text() != payload:
            _root_write(root / 'app', current.name, payload, 0o644)
            changed = True
    return changed  # a bind-mounted file change does not recreate the container by itself


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


PERSONAL_MANIFEST = 'deploy/profiles/personal.json'
PERSONAL_STAGING_SUFFIX = 'setup'
PERSONAL_RECORD = 'personal.json'
HERMES_HOME = '/opt/data'  # where the Hermes container sees state/hermes


def _personal_contract(source):
    """Validate the whole personal-profile contract up front; every schema fault is a ValueError."""
    def need(ok, what):
        if not ok:
            raise ValueError('Personal profile contract: ' + what)

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def contained(relative, what):
        need(text(relative), what + ' must be a path')
        path = (source / relative).resolve()
        need(path.is_relative_to(source.resolve()) and path.is_file(), what + ' must be a file inside the release')
        return path

    try:
        manifest = json.loads((source / PERSONAL_MANIFEST).read_text())
        need(isinstance(manifest, dict), 'the manifest must be an object')
        name = manifest.get('profile')
        need(isinstance(name, str) and re.fullmatch(r'[a-z][a-z0-9]{1,23}', name) and name != 'default',
             'profile name is invalid')
        need(text(manifest.get('description')), 'description is required')
        role = contained(manifest.get('role_file'), 'role_file').read_text()
        need(text(role), 'the role file is empty')
        model = manifest.get('model')
        need(isinstance(model, dict), 'model must be an object')
        pilot = json.loads(contained(model.get('source'), 'model.source').read_text())
        need(isinstance(pilot, dict) and isinstance(pilot.get('model'), dict), 'the pilot model is missing')
        provider, default = pilot['model'].get('provider'), pilot['model'].get('default')
        need(text(provider) and text(default), 'the pilot model needs provider and default')
        config = manifest.get('config')
        need(isinstance(config, dict) and all(isinstance(k, str) and k for k in config), 'config must be an object')
        need(all(isinstance(v, bool) or text(v) for v in config.values()), 'config values must be booleans or text')
        need(config.get('platforms.discord.enabled') is False, 'Discord must be declared disabled')
        need(config.get('approvals.mode') == 'manual', 'approvals.mode must be manual')
        need(config.get('terminal.cwd') == f'{HERMES_HOME}/workspaces/{name}', 'terminal.cwd must be the profile workspace')
        need('model.provider' not in config and 'model.default' not in config, 'the model comes from the pilot only')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Personal profile contract: unexpected structure (' + type(exc).__name__ + ')') from exc
    # Restrictive keys first: whatever is applied before a failure must not widen access.
    first = ('platforms.discord.enabled', 'approvals.mode', 'approvals.cron_mode')
    settings = {key: config[key] for key in first if key in config}
    settings.update({'model.provider': provider, 'model.default': default})
    settings.update({key: value for key, value in config.items() if key not in settings})
    return {'name': name, 'description': manifest['description'], 'role': role, 'settings': settings}


def _directory_identity(path):
    """[device, inode] of a real directory, None when absent; a symlink or file there is an error."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f'{path.name} must be a real directory')
    return [info.st_dev, info.st_ino]


def _personal_record(state, name):
    path = state / 'provisioning' / PERSONAL_RECORD
    if path.is_symlink() or (path.parent.exists() and (path.parent.is_symlink() or not path.parent.is_dir())):
        raise ValueError('The personal provisioning record must not be a symlink')
    if not path.exists():
        return {'state': 'none'}
    record = json.loads(path.read_text())
    ok = (isinstance(record, dict) and record.get('profile') == name
          and record.get('state') in ('creating', 'staged', 'complete'))
    if ok and record['state'] == 'staged':
        ok = (isinstance(record.get('identity'), list) and len(record['identity']) == 2
              and all(isinstance(n, int) for n in record['identity']))
    if not ok:
        raise ValueError('The personal provisioning record is unreadable')
    return record


def _save_personal_record(state, name, phase, **fields):
    directory = state / 'provisioning'
    directory.mkdir(mode=0o700, exist_ok=True)
    payload = {'profile': name, 'state': phase, 'updated': datetime.now(timezone.utc).isoformat(), **fields}
    _root_write(directory, PERSONAL_RECORD, json.dumps(payload) + '\n', 0o600)


def _make_personal_workspace(hermes, name):
    """Create hermes/workspaces/<name> for the Hermes user without ever following a symlink.

    The Hermes user can rename or replace anything under its home, so root opens every component
    relative to its parent descriptor with O_NOFOLLOW and changes ownership on the descriptor."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        descriptors.append(os.open(hermes, flags))
        owner = os.fstat(descriptors[0])
        for part, mode in (('workspaces', 0o755), (name, 0o700)):
            try:
                os.mkdir(part, mode, dir_fd=descriptors[-1])
            except FileExistsError:
                pass
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        os.fchown(descriptors[-1], owner.st_uid, owner.st_gid)
        os.fchmod(descriptors[-1], 0o700)
    except OSError as exc:
        raise ValueError('The personal workspace path must be real directories') from exc
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def ensure_personal_profile(source, state, run):
    """Provision the declared personal Hermes profile once, through Hermes' own commands.

    The profile is built under a reserved staging name and renamed to `personal` only after every
    setting and the role instructions read back, so `personal` never exists half-configured.
    Root never writes inside the Hermes-writable home (SOUL.md and settings go through
    `docker exec -u 10000`; the workspace uses descriptor-relative, symlink-refusing calls).
    Ownership is proven by a root-only record under state/provisioning that binds the staging
    directory's device and inode; a profile without that proof (the operator's, an agent's) is
    never adopted or modified. Returns 'created', 'resumed' or 'present'."""
    contract = _personal_contract(source)
    name = contract['name']
    staging = name + PERSONAL_STAGING_SUFFIX
    hermes = state / 'hermes'
    profiles = hermes / 'profiles'
    if hermes.is_symlink() or profiles.is_symlink():
        raise ValueError('The personal profile paths must not be symlinks')
    record = _personal_record(state, name)
    if record['state'] == 'complete':
        return 'present'
    target_id, staging_id = _directory_identity(profiles / name), _directory_identity(profiles / staging)
    container = ['docker', 'exec', '-u', '10000', 'nixos-hermes']
    cli = container + ['hermes']

    def finish():
        # Rename moved the directory we recorded; Hermes does its own routing/identity migration.
        if _directory_identity(profiles / name) != record['identity']:
            raise ValueError('The personal profile is not the directory this deployment staged')
        run(cli + ['profile', 'describe', name, '--text', contract['description']])
        _save_personal_record(state, name, 'complete')

    if target_id is not None:
        if record['state'] == 'staged' and target_id == record['identity']:
            finish()  # an interrupted rename/describe: the directory is provably ours
            return 'resumed'
        return 'present'
    outcome = 'resumed'
    if record['state'] == 'staged' and staging_id is None:
        record = {'state': 'none'}  # our staging directory is gone: nothing to adopt, start over
    if record['state'] == 'staged':
        if staging_id != record['identity']:
            raise ValueError('The staging profile is not the directory this deployment created; not adopted')
    else:
        if staging_id is not None:
            raise ValueError(f'A profile named {staging} exists without a deployment record; '
                             f'inspect it and remove it with `hermes profile delete {staging}`')
        outcome = 'created'
        _save_personal_record(state, name, 'creating')
        run(cli + ['profile', 'create', staging, '--no-alias'])
        staging_id = _directory_identity(profiles / staging)
        if staging_id is None:
            raise ValueError('Hermes did not create the personal staging profile')
        record = {'state': 'staged', 'identity': staging_id}
        _save_personal_record(state, name, 'staged', identity=staging_id)
    settings = contract['settings']
    for key, value in settings.items():
        run(cli + ['-p', staging, 'config', 'set', key, json.dumps(value) if isinstance(value, bool) else str(value)])
    for key, value in settings.items():
        if json.loads(run(cli + ['-p', staging, 'config', 'get', '--json', key])) != value:
            raise ValueError('Hermes did not apply the personal profile setting ' + key)
    soul = f'{HERMES_HOME}/profiles/{staging}/SOUL.md'
    run(container + ['sh', '-c', 'umask 022; printf %s "$1" > "$2.next" && mv -f "$2.next" "$2"',
                     'write-soul', contract['role'], soul])
    if run(container + ['cat', soul]).strip() != contract['role'].strip():
        raise ValueError('The personal role instructions did not read back')
    _make_personal_workspace(hermes, name)
    if _directory_identity(profiles / staging) != record['identity']:
        raise ValueError('The staging profile changed while it was configured; not renamed')
    run(cli + ['profile', 'rename', staging, name])
    finish()
    return outcome


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
          t3_loaded=confirm_t3_credential, units=sync_recovery_units, personal=ensure_personal_profile):
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
        try:
            personal(source, state, run)
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            # Additive and retried by every deployment: never block the company runtime on it. Only our
            # own validation messages are printed; native command output and argv can hold role text.
            detail = f': {exc}' if type(exc) is ValueError else ''
            print('Personal profile provisioning failed; deployment continues and retries next run' + detail,
                  file=sys.stderr)
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
