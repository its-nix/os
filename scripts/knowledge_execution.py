#!/usr/bin/env python3
"""Published v2 receipt consumer and exact, reversible Watch cache transaction.

Trusted publication, NOT JSON, authenticates the contextual human decision.
EXPECTED_EVIDENCE pins the exact parent-verified Discord prompt/voice/STT event.
Publication is governance approval, not evidence of live installation.
No CLI option or environment variable can substitute evidence.
Low-level transaction helpers are not authorization APIs; production callers
must use consume_receipt() before constructing them.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import uuid

ROOT = Path(__file__).resolve().parents[1]
ANCESTOR = '0ba0131f0da473d12bcb1fc3cadd8258bd66e15e'
PACKET = Path('skills/activation-proposals/lnx-knowledge-v2.json')
PACKET_HASH = '9c7978bf7b2302c1e9a82c1db91b9c03d448675113d0ed41621117f285f9f313'
RECEIPT = Path('skills/activation-approvals/lnx-knowledge-v2.json')
SINGLE = Path('skills/proposals/lnx-nix-knowledge-1182c51c.json')
QUOTE = 'approve lnx-knowledge-v2 at ' + ANCESTOR
# Exact contextual event read back by the parent; see the decision record.
# Never fill from local JSON, normalize the utterance, or infer installation.
EXPECTED_EVIDENCE = {
    'kind': 'context_bound_voice_approval', 'platform': 'discord',
    'channel_id': '1563201690663125142', 'proposal_git_commit': ANCESTOR,
    'prompt': {'message_id': '1564704949332615406', 'author_id': '1563178010020941956',
               'timestamp': '2026-10-27T18:18:50.733000+00:00'},
    'human_voice': {'message_id': '1564705817448423424', 'author_id': '454290355320520714',
                    'bot': False, 'timestamp': '2026-10-27T18:22:17.708000+00:00', 'content': '',
                    'attachment': {'filename': 'voice-message.ogg', 'size_bytes': 26402}},
    'bot_stt': {'message_id': '1564705827204370614', 'author_id': '1563178010020941956',
                'timestamp': '2026-10-27T18:22:20.034000+00:00',
                'quote': 'I approve and make the repository public so you can test each CI again and then make it private again.'},
    'scope': {'profile': 'lnx', 'hermes_profile': 'default', 'new_skill': 'nix-knowledge',
              'preserved_trees': 5, 'cache_files': 5, 'hooks': False},
    'human_written_text_quote': False, 'voice_payload_cryptographically_verified': False}
CACHE_NAMES = tuple(n + '.cpython-311.pyc' for n in ('download', 'frames', 'local_whisper', 'runtime', 'transcript'))
spec = importlib.util.spec_from_file_location('execution_installer', ROOT / 'scripts/install-skills.py')
assert spec and spec.loader
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
Refusal = installer.Refusal


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def load_packet():
    raw = (ROOT / PACKET).read_bytes()
    if digest(raw) != PACKET_HASH:
        raise Refusal('immutable activation proposal file changed')
    return json.loads(raw)


def approved_manifest(pending, packet):
    result = copy.deepcopy(pending)
    result['host_native_satisfaction']['lnx'] = copy.deepcopy(packet['preservation'])
    return result


def validate_evidence(evidence):
    if EXPECTED_EVIDENCE is None:
        raise Refusal('pending: authenticated genuine Discord evidence has not been published')
    if evidence != EXPECTED_EVIDENCE or not isinstance(evidence, dict):
        raise Refusal('v2 evidence differs from exact out-of-band verified Discord approval')
    if evidence.get('kind') == 'context_bound_voice_approval':
        # This ONE event may be published only after parent Discord readback.
        # Attachment bytes were not cryptographically authenticated. STT is
        # separately bot-authored and is never represented as human text.
        expected_voice = voice_decision_contract()
        if evidence != expected_voice:
            raise Refusal('voice decision differs from the exact prompt/human-event/STT provenance binding')
        return
    if (not installer._valid_discord_evidence(evidence, False)
            or evidence['author_id'] != '454290355320520714'
            or evidence['channel_id'] != '1563201690663125142'
            or evidence['quote'] != QUOTE):
        raise Refusal('v2 evidence differs from exact out-of-band verified Discord approval')
    # Discord snowflake timestamp must agree to the millisecond. This checks
    # consistency, not authenticity. No synthesized record is shipped here.
    milliseconds = (int(evidence['message_id']) >> 22) + 1420070400000
    actual = int(datetime.fromisoformat(evidence['timestamp'].replace('Z', '+00:00')).timestamp() * 1000)
    if actual != milliseconds:
        raise Refusal('Discord message ID/time mismatch')


def voice_decision_contract():
    """Exact contextual decision schema, independently pinned by publication.

    The parent read back the real prompt, human voice event and distinct bot
    transcription. This is not human-written text or installer authentication.
    """
    return {'kind': 'context_bound_voice_approval', 'platform': 'discord',
            'channel_id': '1563201690663125142', 'proposal_git_commit': ANCESTOR,
            'prompt': {'message_id': '1564704949332615406', 'author_id': '1563178010020941956',
                       'timestamp': '2026-10-27T18:18:50.733000+00:00'},
            'human_voice': {'message_id': '1564705817448423424', 'author_id': '454290355320520714',
                            'bot': False, 'timestamp': '2026-10-27T18:22:17.708000+00:00', 'content': '',
                            'attachment': {'filename': 'voice-message.ogg', 'size_bytes': 26402}},
            'bot_stt': {'message_id': '1564705827204370614', 'author_id': '1563178010020941956',
                        'timestamp': '2026-10-27T18:22:20.034000+00:00',
                        'quote': 'I approve and make the repository public so you can test each CI again and then make it private again.'},
            'scope': {'profile': 'lnx', 'hermes_profile': 'default', 'new_skill': 'nix-knowledge',
                      'preserved_trees': 5, 'cache_files': 5, 'hooks': False},
            'human_written_text_quote': False, 'voice_payload_cryptographically_verified': False}



def consume_receipt(manifest, *, require_approved=True):
    """Fixed committed route only. Refuse dirty, ignored/untracked orphans too."""
    p = load_packet()
    validate_evidence(EXPECTED_EVIDENCE)
    installer._check_no_symlink(ROOT)
    if installer._run(['git', 'status', '--porcelain', '--untracked-files=all'], ROOT):
        raise Refusal('v2 governance checkout must be completely clean')
    installer._run(['git', 'merge-base', '--is-ancestor', ANCESTOR, 'HEAD'], ROOT)
    for relative, expected in ((PACKET, PACKET_HASH), (SINGLE, p['skill_proposal_sha256']),
                               (Path('skills/packs.json'), p['manifest_sha256'])):
        if digest(installer._git_bytes(ANCESTOR, relative, 'v2 ancestor artifact')) != expected:
            raise Refusal('v2 ancestor artifact hash mismatch: ' + str(relative))
        if relative != Path('skills/packs.json') and digest((ROOT / relative).read_bytes()) != expected:
            raise Refusal('current v2 artifact differs from ancestor')
    approval_root = ROOT / RECEIPT.parent
    installer._check_no_symlink(approval_root)
    if not approval_root.is_dir() or {q.name for q in approval_root.iterdir()} != {RECEIPT.name}:
        raise Refusal('v2 receipts must contain exactly the canonical receipt, no orphan records')
    for relative in (PACKET, SINGLE, RECEIPT, Path('skills/packs.json'), Path('scripts/knowledge_execution.py'), Path('scripts/install-skills.py')):
        installer._check_no_symlink(ROOT / relative)
        installer._tracked_clean(relative)
        if (ROOT / relative).read_bytes() != installer._git_bytes('HEAD', relative, 'current governance'):
            raise Refusal('governance working bytes differ from HEAD')
    pending = json.loads(installer._git_bytes(ANCESTOR, Path('skills/packs.json'), 'pending manifest'))
    approved = approved_manifest(pending, p)
    if manifest != (approved if require_approved else pending):
        raise Refusal('manifest must be exact ancestor-derived preservation-only lifecycle state')
    receipt = installer._load_json(ROOT / RECEIPT, 'v2 receipt')
    if receipt != receipt_contract(p):
        raise Refusal('v2 receipt schema/scope/pins/evidence mismatch')
    validate_evidence(receipt['approval_evidence'])
    single_receipt = Path('skills/approvals/lnx-nix-knowledge-1182c51c.json')
    installer._check_no_symlink(ROOT / single_receipt)
    installer._tracked_clean(single_receipt)
    if installer._load_json(ROOT / single_receipt, 'single-skill receipt') != single_receipt_contract(p):
        raise Refusal('knowledge single-skill receipt is absent or misbound')
    return p


def receipt_contract(p):
    """Schema binding only, NOT a receipt issuer and never writes an approval."""
    return {'schema_version': 2, 'proposal_id': 'lnx-knowledge-v2',
                'proposal_git_commit': ANCESTOR, 'proposal_file_sha256': PACKET_HASH,
                'skill_proposal_sha256': p['skill_proposal_sha256'],
                'manifest_sha256': p['manifest_sha256'], 'profile': 'lnx', 'hermes_profile': 'default',
                'source': p['source'], 'nixkit': p['nixkit'], 'preservation': p['preservation'],
                'effective_skills': p['effective_skills'], 'install_targets': p['install_targets'],
                'cache_plan_sha256': canonical(p['cache']), 'approved_by': 'Lior',
                'approval_evidence': EXPECTED_EVIDENCE}


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns]


def stable_directory(info):
    return identity(info)[:5]


@contextmanager
def directory(path):
    """Open every absolute component without following links; bind parents too."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Refusal('directory path must be absolute without traversal')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    chain = []
    try:
        for part in path.parts[1:]:
            before = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise Refusal('non-directory or symlink ancestor')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if stable_directory(os.fstat(child)) != stable_directory(before):
                os.close(child)
                raise Refusal('ancestor replaced before open')
            chain.append((fd, part, stable_directory(before)))
            fd = child
        yield fd
        for parent, name, observed in chain:
            if stable_directory(os.stat(name, dir_fd=parent, follow_symlinks=False)) != observed:
                raise Refusal('ancestor replaced during operation')
    finally:
        os.close(fd)
        for parent, _, _ in reversed(chain):
            os.close(parent)


def read_file(fd, name, observed=None):
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
        raise Refusal('cache/backup must be a regular file owned by this user')
    expected = observed or identity(before)
    if identity(before) != expected:
        raise Refusal('object identity replaced before open')
    leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        if identity(os.fstat(leaf)) != expected:
            raise Refusal('object identity replaced during open')
        chunks = []
        while data := os.read(leaf, 1024 * 1024):
            chunks.append(data)
        if identity(os.fstat(leaf)) != expected or identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != expected:
            raise Refusal('object changed during read')
        return b''.join(chunks), expected
    finally:
        os.close(leaf)


def snapshot(root):
    rows, identities = [], {}
    with directory(root) as fd:
        def walk(parent, relative):
            info = os.fstat(parent)
            if info.st_uid != os.getuid():
                raise Refusal('tree directory ownership mismatch')
            identities[relative] = identity(info)
            rows.append({'path': relative, 'mode': stat.S_IMODE(info.st_mode), 'type': stat.S_IFDIR})
            names = sorted(os.listdir(parent))
            for name in names:
                rel = name if relative == '.' else relative + '/' + name
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        if stable_directory(os.fstat(child)) != stable_directory(before):
                            raise Refusal('snapshot directory replacement')
                        walk(child, rel)
                        if identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity(before):
                            raise Refusal('snapshot directory changed')
                    finally:
                        os.close(child)
                else:
                    raw, observed = read_file(parent, name, identity(before))
                    identities[rel] = observed
                    rows.append({'path': rel, 'mode': stat.S_IMODE(before.st_mode), 'type': stat.S_IFREG, 'sha256': digest(raw)})
            if names != sorted(os.listdir(parent)) or identity(os.fstat(parent)) != identity(info):
                raise Refusal('snapshot tree changed')
        walk(fd, '.')
    return sorted(rows, key=lambda row: row['path']), identities


def cache_preview(home, p):
    root = home / 'skills/watch'
    rows, ids = snapshot(root)
    if rows != p['cache']['inventory'] or installer._skill_tree_digest(root) != p['cache']['before_tree_sha256']:
        raise Refusal('exact cache before-tree/inventory drift')
    names = [r['path'] for r in p['cache']['files']]
    if names != ['scripts/__pycache__/' + n for n in CACHE_NAMES]:
        raise Refusal('only the exact five Watch caches may be archived')
    for row in p['cache']['files']:
        if row not in rows:
            raise Refusal('cache plan file mismatch')
    return ids


class CacheTransaction:
    """Invocation-owned durable archive. Unknown/replaced objects are never deleted.

    Cooperating writers lock the state directory inode with flock. Each archive
    is exclusive, mode 0700, outside loadable skills. Copies and journal are
    fsynced BEFORE any source unlink. Runtime identities live only in this journal.
    No archive is ever automatically garbage collected.
    """
    def __init__(self, home, state, p, recovery=None):
        self.home, self.state, self.p = Path(home), Path(state), p
        self.root = self.home / 'skills/watch'
        self.directory = Path(recovery) if recovery else self.state / uuid.uuid4().hex
        self.recovery = recovery
        self.guard = None
        self.lock = None

    def __enter__(self):
        # mkdir only through non-following descriptor walks; no implicit cleanup.
        self._mkdirs(self.state)
        self.guard = directory(self.state)
        self.lock = self.guard.__enter__()
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.recovery:
                if self.directory.parent != self.state or len(self.directory.name) != 32 or any(c not in '0123456789abcdef' for c in self.directory.name):
                    raise Refusal('recovery must name an owned unique transaction')
                with directory(self.directory) as fd:
                    raw, _ = read_file(fd, 'journal.json')
                self.journal = json.loads(raw)
                self._validate_journal()
                if (self.journal.get('plan') != canonical(self.p['cache'])
                        or self.journal.get('home') != str(self.home)
                        or self.journal.get('token') != self.directory.name):
                    raise Refusal('unknown transaction journal; manual recovery required')
            else:
                # Retain completed/rolled-back archives forever; unknown or
                # incomplete transactions require explicit recovery, not cleanup.
                for name in os.listdir(self.lock):
                    if len(name) != 32 or any(c not in '0123456789abcdef' for c in name):
                        raise Refusal('unknown state entry requires manual recovery: ' + name)
                    with directory(self.state / name) as fd:
                        raw, _ = read_file(fd, 'journal.json')
                    previous = json.loads(raw)
                    self.journal = previous
                    self._validate_journal()
                    if (previous.get('plan') != canonical(self.p['cache']) or previous.get('home') != str(self.home)
                            or previous.get('token') != name or previous.get('state') not in {'completed', 'rolled_back'}):
                        raise Refusal('existing transaction requires explicit readback/recovery: ' + name)
                os.mkdir(self.directory.name, 0o700, dir_fd=self.lock)
                os.fsync(self.lock)
                self.journal = {'version': 1, 'plan': canonical(self.p['cache']), 'home': str(self.home),
                                'token': self.directory.name, 'state': 'preparing', 'files': {}}
                self._save()
            return self
        except BaseException:
            self.guard.__exit__(None, None, None)
            self.guard = None
            raise

    def _validate_journal(self):
        j = self.journal
        required = {'version', 'plan', 'home', 'token', 'state', 'files'}
        if (not isinstance(j, dict) or not required <= j.keys()
                or set(j) - required - {'identities', 'restored_directory'}
                or j['version'] != 1 or j['state'] not in {'preparing', 'archiving', 'removing', 'removed', 'completed', 'rolled_back', 'manual_recovery'}
                or not isinstance(j['files'], dict) or not set(j['files']) <= set(CACHE_NAMES)):
            raise Refusal('unknown journal schema; manual recovery required')
        def valid_id(value):
            return isinstance(value, list) and len(value) == 7 and all(type(v) is int and v >= 0 for v in value)
        if 'identities' in j:
            expected = {r['path']: r for r in self.p['cache']['inventory']}
            if set(j['identities']) != set(expected):
                raise Refusal('journal inventory mismatch')
            for relative, observed in j['identities'].items():
                row = expected[relative]
                if (not valid_id(observed) or observed[3] != os.getuid()
                        or stat.S_IMODE(observed[2]) != row['mode'] or stat.S_IFMT(observed[2]) != row['type']):
                    raise Refusal('journal identity mismatch')
        for name, record in j['files'].items():
            row = next(r for r in self.p['cache']['files'] if Path(r['path']).name == name)
            if (not isinstance(record, dict) or set(record) != {'original', 'backup', 'sha256', 'mode'}
                    or record['sha256'] != row['sha256'] or record['mode'] != row['mode']
                    or not valid_id(record['original']) or not valid_id(record['backup'])
                    or record['original'] != j.get('identities', {}).get(row['path'])
                    or record['backup'][3] != os.getuid()
                    or record['backup'][2] != stat.S_IFREG | row['mode']):
                raise Refusal('journal file scope mismatch')
        if 'restored_directory' in j and not valid_id(j['restored_directory']):
            raise Refusal('journal restored directory malformed')
        if j['state'] in {'removing', 'removed', 'completed'} and set(j['files']) != set(CACHE_NAMES):
            raise Refusal('incomplete archive journal; manual recovery required')

    @staticmethod
    def _mkdirs(path):
        current = Path('/')
        for part in path.parts[1:]:
            with directory(current) as fd:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            current /= part
        with directory(path) as fd:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise Refusal('transaction state must be private and owned by current user')

    def __exit__(self, *args):
        if self.guard:
            self.guard.__exit__(*args)
            self.guard = None

    def _save(self):
        with directory(self.directory) as fd:
            name = 'journal-' + uuid.uuid4().hex
            raw = (json.dumps(self.journal, sort_keys=True) + '\n').encode()
            leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(leaf, 'wb', closefd=False) as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(leaf)
            finally:
                os.close(leaf)
            os.replace(name, 'journal.json', src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)

    def _expect(self, removed):
        excluded = {'scripts/__pycache__/' + n for n in removed}
        rows, ids = snapshot(self.root)
        expected = [r for r in self.p['cache']['inventory'] if r['path'] not in excluded]
        if rows != expected:
            raise Refusal('Watch tree drift during transaction')
        for relative, original in self.journal['identities'].items():
            if relative in excluded:
                continue
            current = ids.get(relative)
            if current is None or (current[:5] != original[:5] if stat.S_ISDIR(original[2]) else current != original):
                raise Refusal('Watch object identity replacement during transaction')

    def remove(self):
        ids = cache_preview(self.home, self.p)
        self.journal['identities'] = ids
        self.journal['state'] = 'archiving'
        self._save()
        cache = self.root / 'scripts/__pycache__'
        # Archive every byte and mode durably before removing ANY cache file.
        with directory(cache) as src, directory(self.directory) as dst:
            for row in self.p['cache']['files']:
                name = Path(row['path']).name
                raw, observed = read_file(src, name, ids[row['path']])
                if digest(raw) != row['sha256']:
                    raise Refusal('cache bytes drift')
                leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, row['mode'], dir_fd=dst)
                try:
                    with os.fdopen(leaf, 'wb', closefd=False) as out:
                        out.write(raw)
                        out.flush()
                        os.fchmod(leaf, row['mode'])
                        os.fsync(leaf)
                    backup = identity(os.fstat(leaf))
                finally:
                    os.close(leaf)
                if read_file(dst, name, backup)[0] != raw:
                    raise Refusal('backup verification failed')
                self.journal['files'][name] = {'original': observed, 'backup': backup, 'sha256': row['sha256'], 'mode': row['mode']}
            os.fsync(dst)
        self.journal['state'] = 'removing'
        self._save()
        removed = []
        for name in CACHE_NAMES:
            self._expect(removed)
            with directory(cache) as src:
                if stable_directory(os.fstat(src)) != ids['scripts/__pycache__'][:5]:
                    raise Refusal('cache directory replaced')
                read_file(src, name, ids['scripts/__pycache__/' + name])
                os.unlink(name, dir_fd=src)
                os.fsync(src)
            removed.append(name)
        self._expect(removed)
        with directory(self.root / 'scripts') as parent:
            observed = os.stat('__pycache__', dir_fd=parent, follow_symlinks=False)
            if stable_directory(observed) != ids['scripts/__pycache__'][:5]:
                raise Refusal('cache directory identity changed before rmdir')
            os.rmdir('__pycache__', dir_fd=parent)  # refuses non-empty
            os.fsync(parent)
        if snapshot(self.root)[0] != self.p['acquired_target_closures']['watch'] or installer._skill_tree_digest(self.root) != self.p['cache']['after_tree_sha256']:
            raise Refusal('Watch semantic post-tree mismatch')
        self.journal['state'] = 'removed'
        self._save()

    def rollback(self):
        if self.journal['state'] == 'rolled_back':
            if snapshot(self.root)[0] != self.p['cache']['inventory']:
                raise Refusal('rolled-back tree drift; manual recovery required')
            return
        errors = []
        cache = self.root / 'scripts/__pycache__'
        if self.journal['state'] in {'preparing', 'archiving'}:
            if snapshot(self.root)[0] != self.p['cache']['inventory']:
                raise Refusal('pre-removal tree changed; manual recovery required')
            self.journal['state'] = 'rolled_back'
            self._save()
            return
        try:
            with directory(cache.parent) as parent:
                try:
                    os.mkdir(cache.name, self.journal['identities']['scripts/__pycache__'][2] & 0o7777, dir_fd=parent)
                    child = os.open(cache.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        os.fchmod(child, self.journal['identities']['scripts/__pycache__'][2] & 0o7777)
                        self.journal['restored_directory'] = identity(os.fstat(child))
                        os.fsync(child)
                    finally:
                        os.close(child)
                    os.fsync(parent)
                    self._save()
                except FileExistsError:
                    current = stable_directory(os.stat(cache.name, dir_fd=parent, follow_symlinks=False))
                    allowed = [self.journal['identities']['scripts/__pycache__'][:5]]
                    if 'restored_directory' in self.journal:
                        allowed.append(self.journal['restored_directory'][:5])
                    if current not in allowed:
                        raise Refusal('cache directory concurrently replaced; manual recovery required')
            with directory(cache) as dst, directory(self.directory) as src:
                for name, record in self.journal['files'].items():
                    raw, _ = read_file(src, name, record['backup'])
                    if digest(raw) != record['sha256']:
                        raise Refusal('backup changed')
                    try:
                        current = os.stat(name, dir_fd=dst, follow_symlinks=False)
                    except FileNotFoundError:
                        current = None
                    if current is not None:
                        # Original untouched or already restored owned hard link.
                        if identity(current) not in (record['original'], record['backup']):
                            errors.append(name)
                        continue
                    try:
                        # Atomic NOREPLACE publication, preserving backup forever.
                        os.link(name, name, src_dir_fd=src, dst_dir_fd=dst, follow_symlinks=False)
                        os.fsync(dst)
                    except FileExistsError:
                        errors.append(name)
            if snapshot(self.root)[0] != self.p['cache']['inventory'] or installer._skill_tree_digest(self.root) != self.p['cache']['before_tree_sha256']:
                errors.append('before-tree mismatch')
        except (OSError, Refusal, KeyError) as exc:
            errors.append(str(exc))
        self.journal['state'] = 'manual_recovery' if errors else 'rolled_back'
        self._save()
        if errors:
            raise Refusal('manual recovery required; archive retained: ' + '; '.join(errors))


def kit_tree_digest(root):
    """Exact NixKit v2 length-delimited hash; no symlink allowance in this route."""
    rows, ids = snapshot(root)
    by_path = {r['path']: r for r in rows}
    h = hashlib.sha256(b'nixkit-directory-hash-v2\0')
    def field(raw):
        h.update(len(raw).to_bytes(8, 'big'))
        h.update(raw)
    def visit(relative):
        row = by_path[relative]
        is_directory = row['type'] == stat.S_IFDIR
        h.update(bytes([1 if is_directory else 2]))
        field(('' if relative == '.' else relative).encode())
        h.update((row['mode'] & 0o777).to_bytes(4, 'big'))
        if not is_directory:
            field(ids[relative][5].to_bytes(8, 'big') + bytes.fromhex(row['sha256']))
        else:
            for child in sorted(q for q in by_path if q != '.' and (str(Path(q).parent) == relative)):
                visit(child)
    visit('.')
    return h.hexdigest()


def source_preflight(kit, p):
    """Require already acquired, exact full source closures. NEVER fetch here."""
    installer._check_no_symlink(kit)
    if installer._run(['git', 'rev-parse', 'HEAD'], kit) != p['nixkit']['commit'] or installer._run(['git', 'status', '--porcelain', '--untracked-files=all'], kit):
        raise Refusal('Kit must be clean at exact pin')
    if digest((kit / 'sources.json').read_bytes()) != p['nixkit']['sources_sha256']:
        raise Refusal('Kit source catalog hash mismatch')
    catalog = installer._source_catalog(kit / 'sources.json')
    installer._verify_installer(kit)
    for relative, tree in installer.NIXKIT_LOCAL_TREES.items():
        installer._verify_local_tree(kit, relative, tree)
    installer._validate_declared_pack_proposals(installer._load_json(ROOT / 'skills/packs.json', 'manifest'), catalog)
    for profile in installer.PROFILES:
        for pack in installer._load_json(ROOT / 'skills/packs.json', 'manifest')['approved_pack_additions'][profile]:
            installer._pack_approval(profile, pack, installer._load_json(ROOT / 'skills/packs.json', 'manifest'), catalog)
    installer._check_no_symlink(kit / 'skills-lock.json')
    lock = installer._load_json(kit / 'skills-lock.json', 'pre-acquired Kit integrity lock')
    if set(lock) != {'version', 'skills'} or lock['version'] != 2:
        raise Refusal('Kit integrity lock schema mismatch')
    for name in p['install_targets']:
        source = kit / ('skills' if name == 'watch' else '.agents/skills') / name
        if snapshot(source)[0] != p['acquired_target_closures'][name]:
            raise Refusal('full acquired source closure drift: ' + name)
        if installer._frontmatter_name(source / 'SKILL.md') != name:
            raise Refusal('source skill identity mismatch')
        if name != 'watch':
            repo, revision, path = catalog[name]
            entry = {'repo': repo, 'ref': revision, 'sha256': kit_tree_digest(source)}
            # Catalog scopes a discovery parent to an exact package; Kit lock
            # records the ORIGINAL catalog selector, not the scoped package.
            sources = json.loads((kit / 'sources.json').read_bytes())
            owner = next(s for s in sources if name in s['skills'])
            if 'path' in owner:
                entry['path'] = owner['path']
            if lock['skills'].get(name) != entry:
                raise Refusal('source integrity/provenance lock mismatch: ' + name)
    # Independent immutable Git-blob check for all three knowledge files.
    for row in p['acquired_target_closures']['nix-knowledge']:
        if row['type'] == stat.S_IFREG:
            raw = installer._git_bytes(p['source']['revision'], Path(p['source']['skill_path']) / row['path'], 'knowledge source blob')
            if digest(raw) != row['sha256']:
                raise Refusal('knowledge file does not match original source commit')
    return catalog


def targets_preflight(home, p, *, cleaned=False, installed=False):
    preserved = installer._validate_host_native_satisfaction('lnx', home, {'lnx': p['preservation']})
    installer._preflight_duplicate_names(home, set(p['install_targets']), preserved)
    if (home / 'skills/nix-document-design').exists() or (home / 'skills/nix-document-design').is_symlink():
        raise Refusal('task-only skill unexpectedly installed')
    for name in p['install_targets']:
        root = home / 'skills' / name
        if name == 'nix-knowledge' and not installed:
            if root.exists() or root.is_symlink():
                raise Refusal('new skill destination already exists')
            continue
        if name == 'watch' and not cleaned:
            cache_preview(home, p)
        elif snapshot(root)[0] != p['acquired_target_closures'][name]:
            raise Refusal('full destination closure drift: ' + name)
        if installer._frontmatter_name(root / 'SKILL.md') != name:
            raise Refusal('destination skill identity mismatch')
    return preserved


def single_receipt_contract(p):
    return {'schema_version': 2, 'proposal_id': 'lnx-nix-knowledge-1182c51c',
            'profile': 'lnx', 'skills': ['nix-knowledge'], 'approved_by': 'Lior',
            'proposal_git_commit': ANCESTOR, 'proposal_file_sha256': p['skill_proposal_sha256'],
            'activation_receipt': RECEIPT.as_posix(), 'approval_evidence': EXPECTED_EVIDENCE}


def validate_single_receipt(p, catalog, manifest):
    """A v1 same-quote record cannot authorize the new skill or new hashes."""
    relative = Path('skills/approvals/lnx-nix-knowledge-1182c51c.json')
    installer._tracked_clean(relative)
    installer._check_no_symlink(ROOT / relative)
    expected = single_receipt_contract(p)
    if installer._load_json(ROOT / relative, 'knowledge single-skill receipt') != expected:
        raise Refusal('knowledge single-skill receipt must bind exact v2 authority, ancestor, source and evidence')
    installer._validate_proposal(installer._load_json(ROOT / SINGLE, 'knowledge proposal'),
                                expected['proposal_id'], 'lnx', {'nix-knowledge'}, catalog)
    for name, proposal in (('caveman', 'lnx-caveman-d8ede5cf'), ('watch', 'lnx-watch-a9cb5b57'), ('todoist-cli', 'lnx-todoist-cli-340d7b21')):
        installer._approval(ROOT / 'skills/approvals' / (proposal + '.json'), 'lnx', manifest,
                            set(manifest['approved_additions']['lnx']), catalog)


def cached_node():
    """Use only an already present pinned Node runtime; never download packages."""
    import subprocess
    for path in sorted((Path.home() / '.npm/_npx').glob('*/node_modules/node/bin/node')):
        installer._check_no_symlink(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
            continue
        result = subprocess.run([str(path), '--version'], capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip() == 'v' + installer.NODE_RUNTIME:
            return path
    raise Refusal('already cached exact Node ' + installer.NODE_RUNTIME + ' required; acquisition is separate')


def scan_knowledge(root):
    """Uncached native Skills Guard policy, community trust, force=False only."""
    import subprocess
    import sys
    scanner = Path.home() / '.hermes/hermes-agent/tools/skills_guard.py'
    installer._check_no_symlink(scanner)
    if not scanner.is_file():
        raise Refusal('native Skills Guard is unavailable; no scanner bypass')
    code = ('import importlib.util,json,sys; from pathlib import Path; '
            's=importlib.util.spec_from_file_location("v2_native_guard",sys.argv[1]); '
            'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m); '
            'r=m.scan_skill(Path(sys.argv[2]),source="its-nix/os"); '
            'allowed,reason=m.should_allow_install(r,force=False); '
            'print(json.dumps({"scanner":m.SCANNER_VERSION,"trust":r.trust_level,"verdict":r.verdict,"allowed":allowed,"reason":reason})); '
            'sys.exit(0 if allowed is True and r.trust_level=="community" and r.verdict=="safe" else 2)')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(scanner), str(root)],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise Refusal('native skill scan refused: ' + (result.stdout or result.stderr).strip())
    return json.loads(result.stdout)


def production(args, manifest):
    """Production entry: pure preview by default; no path/test/env authority bypass."""
    import subprocess
    p = consume_receipt(manifest)
    if args.profile != 'lnx':
        raise Refusal('v2 supports default profile only')
    home = Path.home() / '.hermes'
    if Path(os.environ.get('HERMES_HOME', str(home))).absolute() != home:
        raise Refusal('v2 default home cannot be redirected')
    if any(k.startswith('NIXKIT_TEST_') for k in os.environ):
        raise Refusal('Kit test hooks forbidden in production integration')
    expected_paths = {ROOT / 'skills/approvals' / (n + '.json') for n in
                      ('lnx-caveman-d8ede5cf', 'lnx-watch-a9cb5b57', 'lnx-todoist-cli-340d7b21', 'lnx-nix-knowledge-1182c51c')}
    if (len(args.skill) != 4 or set(args.skill) != set(manifest['approved_additions']['lnx'])
            or len(args.approval) != 4 or {q.absolute() for q in args.approval} != expected_paths):
        raise Refusal('v2 requires all four exact additions and canonical single-skill receipts')
    kit = args.nixkit.expanduser().absolute()
    catalog = source_preflight(kit, p)
    validate_single_receipt(p, catalog, manifest)
    installed = (home / 'skills/nix-knowledge').exists()
    if installed:
        before = targets_preflight(home, p, cleaned=True, installed=True)
        scan_knowledge(home / 'skills/nix-knowledge')
        print('VERIFIED NO-OP default-only knowledge v2: all 12 closures and five preserved trees match')
        return 0
    before = targets_preflight(home, p)
    scan_knowledge(kit / '.agents/skills/nix-knowledge')
    node = cached_node()
    # Source acquisition is OFFLINE. No new authentication or credential
    # handling, and no hidden acquisition in cleanup/apply.
    command = [str(node), str(kit / 'install.mjs'), '--offline', '--skills', '--agent', 'hermes', '--prefix', str(home)]
    for name in p['install_targets']:
        command += ['--skill', name]
    print(('APPLY' if args.apply else 'PREVIEW') + ' default-only knowledge v2')
    print(json.dumps({'command': command + ['--apply'], 'cache': p['cache']['files'], 'preserved': list(before)}, sort_keys=True))
    if not args.apply:
        return 0
    # Full repeated preflight before first irreversible effect. Kit alone owns
    # source/target staging; this transaction owns only the exact five caches.
    consume_receipt(manifest)
    source_preflight(kit, p)
    targets_preflight(home, p)
    state = Path.home() / '.local/state/nix-skill-rollbacks/lnx-knowledge-v2'
    with CacheTransaction(home, state, p) as tx:
        try:
            tx.remove()
            targets_preflight(home, p, cleaned=True)
            source_preflight(kit, p)
            installer._validate_host_native_satisfaction('lnx', home, manifest['host_native_satisfaction'])
            result = subprocess.run(command + ['--apply'], cwd=kit, check=False)
            installer._validate_host_native_satisfaction('lnx', home, manifest['host_native_satisfaction'])
            if result.returncode:
                raise Refusal('Kit execution failed; Kit retains responsibility for its owned targets')
            targets_preflight(home, p, cleaned=True, installed=True)
            scan_knowledge(home / 'skills/nix-knowledge')
            targets_preflight(home, p, cleaned=True, installed=True)
            tx.journal['state'] = 'completed'
            tx._save()
        except BaseException:
            tx.rollback()
            raise
    return 0


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Receipt-gated v2 transaction readback/recovery. Preview is pure.')
    parser.add_argument('--rollback', metavar='TRANSACTION_TOKEN')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        p = consume_receipt(installer._load_json(ROOT / 'skills/packs.json', 'manifest'))
        home = Path.home() / '.hermes'
        if Path(os.environ.get('HERMES_HOME', str(home))).absolute() != home:
            raise Refusal('only the default home is supported')
        state = Path.home() / '.local/state/nix-skill-rollbacks/lnx-knowledge-v2'
        if args.rollback:
            token = args.rollback
            if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
                raise Refusal('invalid transaction token')
            path = state / token
            if args.apply:
                with CacheTransaction(home, state, p, recovery=path) as tx:
                    tx.rollback()
            else:
                with directory(path) as fd:
                    raw, _ = read_file(fd, 'journal.json')
                print(raw.decode(), end='')
        else:
            if args.apply:
                raise Refusal('standalone cleanup is forbidden; use installer integration')
            cache_preview(home, p)
            print(json.dumps({'preview': True, 'cache': p['cache']['files']}))
        return 0
    except (OSError, ValueError, Refusal) as exc:
        print('Refusing: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
