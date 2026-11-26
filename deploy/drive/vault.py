"""Host-only Drive bridge. Invoke through the locked mount-namespace wrapper."""
import configparser
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from urllib.request import Request, urlopen
import zipfile

ROOT = Path('/srv/nix-clean')
PRIVATE = ROOT / 'drive'
CONFIG = PRIVATE / 'rclone.conf'
WORK = ROOT / 'vault/Workspace'
POLICIES = ROOT / 'vault/Policies (from Git)'
KB = ROOT / 'knowledge/company'
REMOTE = 'nix:Workspace'
MARK = 'RCLONE_TEST'
READY = PRIVATE / 'initialized'
VERSION = '1.70.3'
BINARY = PRIVATE / ('rclone-' + VERSION)
SHA = '7d69057e69385f6514a9684c7eaa424d972096b130284bb34dd967c4ed4f9dad'


def install_binary():
    if BINARY.exists():
        return
    stem = f'rclone-v{VERSION}-linux-amd64'
    with tempfile.TemporaryFile() as archive:
        import shutil
        with urlopen(f'https://downloads.rclone.org/v{VERSION}/{stem}.zip', timeout=60) as response:
            shutil.copyfileobj(response, archive)
        archive.seek(0)
        if hashlib.sha256(archive.read()).hexdigest() != SHA:
            raise ValueError('rclone release checksum mismatch')
        with zipfile.ZipFile(archive) as release:
            staging = BINARY.with_suffix('.install')
            staging.write_bytes(release.read(stem + '/rclone'))
            staging.chmod(0o700)
            staging.replace(BINARY)


def command(*args, data=None):
    # Even failed listings can contain private note titles. Keep them off the journal.
    result = subprocess.run([BINARY, '--config', CONFIG, '--drive-skip-shortcuts',
                             '--drive-skip-gdocs', *args], input=data, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'rclone {args[0]} refused or failed (exit {result.returncode})')
    return result.stdout


def config_text(folder, token):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', folder) or folder == 'root':
        raise ValueError('Expected a folder ID')
    if not token.get('refresh_token') or not token.get('access_token'):
        raise ValueError('Incomplete OAuth result')
    return ('[nix]\ntype = drive\nscope = drive\nskip_shortcuts = true\nskip_gdocs = true\n'
            f'root_folder_id = {folder}\ntoken = {json.dumps(token)}\n')


def sign_in():
    folder = getpass.getpass('Private folder ID from My Drive > Nix: ').strip()
    # authorize emits the loopback URL on stderr and the credential on stdout.
    output = subprocess.check_output([BINARY, '--config', '/dev/null', 'authorize', 'drive',
                                      '--auth-no-open-browser'], text=True)
    token = json.loads(next(line for line in output.splitlines() if line.startswith('{')))
    settings = config_text(folder, token)
    def metadata(file_id):
        url = 'https://www.googleapis.com/drive/v3/files/' + file_id + '?fields=id,name,parents,mimeType,trashed,ownedByMe'
        request = Request(url, headers={'Authorization': 'Bearer ' + token['access_token']})
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    info = metadata(folder)
    if (info.get('name') != 'Nix' or info.get('parents') != [metadata('root')['id']]
            or info.get('mimeType') != 'application/vnd.google-apps.folder'
            or info.get('trashed') or not info.get('ownedByMe')):
        raise ValueError('Select the owned Nix folder directly under My Drive')
    if CONFIG.exists():
        existing = configparser.RawConfigParser()
        existing.read(CONFIG)
        if existing['nix']['root_folder_id'] != folder:
            raise ValueError('Existing bisync state belongs to a different folder')
    temporary = PRIVATE / 'login.tmp'
    temporary.write_text(settings)
    temporary.chmod(0o600)
    temporary.replace(CONFIG)


def bisync_args(work, remote, directory):
    return ['bisync', str(work), remote, '--workdir', str(directory),
            '--check-access', '--max-delete', '10', '--conflict-resolve', 'none',
            '--conflict-loser', 'num', '--compare', 'size,modtime,checksum',
            '--modify-window', '1s', '--create-empty-src-dirs']


def sync(mode):
    args = bisync_args(WORK, REMOTE, PRIVATE / 'listings')
    if mode == 'login' and not READY.exists():
        if any(p.name != MARK for p in WORK.iterdir()):
            raise ValueError('First login requires an empty local Workspace')
        # Reserve the remote projection namespace before its first one-way sync.
        entries = command('lsf', 'nix:', '--max-depth', '1').decode().splitlines()
        if any(name.rstrip('/').casefold() == POLICIES.name.casefold() for name in entries):
            raise ValueError('Archive the existing Drive projection folder first')
        command('mkdir', REMOTE)
        command('touch', REMOTE + '/' + MARK, '--ignore-existing')
        (WORK / MARK).touch(exist_ok=True)
        args.append('--resync')
    elif mode == 'resync':
        # Both listings must agree byte-for-byte before rebuilding history.
        command('check', WORK, REMOTE, '--download')
        args.append('--resync')
    elif not READY.exists():
        raise ValueError('First login required')
    if not (WORK / MARK).is_file():
        raise ValueError('Missing local access sentinel')
    command('lsjson', REMOTE + '/' + MARK, '--stat')
    if '--resync' in args:
        # Preserve a losing copy even if a client edits during the comparison window.
        run_id = uuid.uuid4().hex
        args += ['--backup-dir1', str(PRIVATE / 'resync-backups' / run_id),
                 '--backup-dir2', 'nix:Resync backups/' + run_id]
    os.setegid(WORK.stat().st_gid)
    try:
        command(*args)
    finally:
        os.setegid(0)
    READY.touch(mode=0o600)
    if not (KB / 'START-HERE.md').is_file():
        raise ValueError('Missing reviewed KB snapshot')
    os.umask(0o022)
    command('sync', KB, POLICIES, '--exclude', '/VAULT-AUTHORITY.md', '--exclude', '/.git/**')
    (POLICIES / 'VAULT-AUTHORITY.md').write_text(
        '---\nread_only: true\nsource: its-nix/knowledgebase\n---\n'
        'This folder mirrors the reviewed Git snapshot. Propose changes by pull request.\n')
    command('sync', POLICIES, 'nix:' + POLICIES.name, '--max-delete', '25')


def main(mode):
    os.umask(0o077)
    if mode == 'login':
        PRIVATE.mkdir(mode=0o700, exist_ok=True)
        install_binary()
        sign_in()
    if CONFIG.stat().st_mode & 0o077 or CONFIG.stat().st_uid != 0:
        raise ValueError('Unsafe ownership or permissions on Drive credential')
    os.umask(0o007)
    sync(mode)
    if mode == 'login':
        subprocess.run(['systemctl', 'enable', '--now', 'nix-drive.timer'], check=True)


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except Exception as error:
        # HTTP errors include the private folder ID. Only our own diagnostics are public.
        sys.exit(str(error) if type(error) in (ValueError, RuntimeError) else
                 'Drive job failed. Check connectivity, authorization and the private state.')
