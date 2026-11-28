import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VAULT_TOOL = ROOT / 'skills/google-drive/scripts/vault.py'

# Stateful fake `gws` executable placed on PATH during tests.
# Supports drive files list (with pagination), create (folders + --upload), and get (alt=media --output).
# Rejects absolute paths on --output / --upload to mirror real gws v0.22.5 path-safety checks.
FAKE_GWS = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import sys

state_path = Path(os.environ['FAKE_GWS_STATE'])
log_path = Path(os.environ['FAKE_GWS_LOG'])
args = sys.argv[1:]
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(args) + '\n')

fail_code = os.environ.get('FAKE_GWS_EXIT')
if fail_code:
    # Include a tempting fake ID on stderr to ensure vault.py never leaks raw gws stderr.
    print('raw gws error for id_secret_999', file=sys.stderr)
    sys.exit(int(fail_code))

state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'next_id': 1, 'files': {}}


def save():
    state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')


def flag_value(name):
    if name in args:
        idx = args.index(name)
        return args[idx + 1]
    return None


def parse_query(q):
    clauses = [c.strip() for c in q.split(' and ') if c.strip()]
    conds = []
    for clause in clauses:
        m = re.fullmatch(r"'([^']+)'\s+in\s+parents", clause)
        if m:
            conds.append(('parent', m.group(1)))
            continue
        m = re.fullmatch(r"name\s*=\s*'((?:\\.|[^'])*)'", clause)
        if m:
            conds.append(('name', m.group(1).replace("\\'", "'").replace("\\\\", "\\")))
            continue
        m = re.fullmatch(r"mimeType\s*=\s*'([^']+)'", clause)
        if m:
            conds.append(('mime_eq', m.group(1)))
            continue
        m = re.fullmatch(r"mimeType\s*!=\s*'([^']+)'", clause)
        if m:
            conds.append(('mime_ne', m.group(1)))
            continue
        if clause == 'trashed = false':
            conds.append(('not_trashed', True))
            continue
        raise SystemExit(f'unsupported query clause: {clause}')
    return conds


def matches(item, conds):
    for kind, val in conds:
        if kind == 'parent' and val not in item.get('parents', []):
            return False
        if kind == 'name' and item.get('name') != val:
            return False
        if kind == 'mime_eq' and item.get('mimeType') != val:
            return False
        if kind == 'mime_ne' and item.get('mimeType') == val:
            return False
        if kind == 'not_trashed' and item.get('trashed', False):
            return False
    return True


if args[:3] == ['drive', 'files', 'list']:
    params = json.loads(flag_value('--params') or '{}')
    conds = parse_query(params.get('q', ''))
    all_items = [
        item for _, item in sorted(state['files'].items())
        if matches(item, conds)
    ]
    page_size = int(os.environ.get('FAKE_GWS_PAGE_SIZE', params.get('pageSize', 100)))
    offset = int(params.get('pageToken', '0'))
    batch = all_items[offset:offset + page_size]
    out = {
        'files': [
            {k: v for k, v in item.items() if k != 'content'}
            for item in batch
        ]
    }
    if offset + page_size < len(all_items):
        out['nextPageToken'] = str(offset + page_size)
    print(json.dumps(out))
    sys.exit(0)

if args[:3] == ['drive', 'files', 'create']:
    body = json.loads(flag_value('--json') or '{}')
    upload = flag_value('--upload')
    content = ''
    if upload is not None:
        if Path(upload).is_absolute() or '..' in Path(upload).parts:
            print('upload path must be relative to cwd', file=sys.stderr)
            sys.exit(3)
        content = Path(upload).read_text(encoding='utf-8')
    fid = f"drv_{state['next_id']:04d}"
    state['next_id'] += 1
    item = {
        'id': fid,
        'name': body['name'],
        'mimeType': body['mimeType'],
        'parents': body.get('parents', []),
        'ownedByMe': True,
        'trashed': False,
        'content': content,
    }
    state['files'][fid] = item
    save()
    print(json.dumps({k: v for k, v in item.items() if k != 'content'}))
    sys.exit(0)

if args[:3] == ['drive', 'files', 'get']:
    params = json.loads(flag_value('--params') or '{}')
    fid = params['fileId']
    item = state['files'].get(fid)
    if not item:
        print(f'not found: {fid}', file=sys.stderr)
        sys.exit(1)
    if params.get('alt') == 'media':
        out_path = flag_value('--output') or flag_value('-o')
        if not out_path or Path(out_path).is_absolute() or '..' in Path(out_path).parts:
            print('output path must be relative to cwd', file=sys.stderr)
            sys.exit(3)
        Path(out_path).write_text(item.get('content', ''), encoding='utf-8')
        print(json.dumps({'saved': out_path}))
        sys.exit(0)
    print(json.dumps({k: v for k, v in item.items() if k != 'content'}))
    sys.exit(0)

print(f'unexpected gws invocation: {args}', file=sys.stderr)
sys.exit(3)
'''


class FakeDriveHarness:
    def __init__(self, tmpdir):
        self.root = Path(tmpdir)
        self.bin_dir = self.root / 'bin'
        self.bin_dir.mkdir()
        self.state_path = self.root / 'drive_state.json'
        self.log_path = self.root / 'gws_calls.log'
        gws = self.bin_dir / 'gws'
        gws.write_text(FAKE_GWS, encoding='utf-8')
        gws.chmod(gws.stat().st_mode | stat.S_IEXEC)
        self.reset_state({'next_id': 10, 'files': {
            'drv_nix_owned': {
                'id': 'drv_nix_owned',
                'name': 'Nix',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': ['root'],
                'ownedByMe': True,
                'trashed': False,
                'content': '',
            },
        }})

    def reset_state(self, state):
        self.state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')

    def load_state(self):
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def add_item(self, fid, name, mime_type, parent_id, content='', owned_by_me=True, web_view_link=None):
        state = self.load_state()
        item = {
            'id': fid,
            'name': name,
            'mimeType': mime_type,
            'parents': [parent_id],
            'ownedByMe': owned_by_me,
            'trashed': False,
            'content': content,
        }
        if web_view_link:
            item['webViewLink'] = web_view_link
        state['files'][fid] = item
        self.reset_state(state)

    def env(self, extra=None):
        env = dict(os.environ)
        env['PATH'] = f"{self.bin_dir}:{env.get('PATH', '')}"
        env['FAKE_GWS_STATE'] = str(self.state_path)
        env['FAKE_GWS_LOG'] = str(self.log_path)
        env.pop('GWS_BIN', None)
        if extra:
            env.update(extra)
        return env

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding='utf-8').splitlines() if line]

    def clear_calls(self):
        self.log_path.unlink(missing_ok=True)

    def find_by_path(self, rel_path):
        parts = [p for p in rel_path.strip('/').split('/') if p]
        state = self.load_state()['files']
        cur = 'root'
        found = None
        for part in parts:
            matches = [f for f in state.values() if f['name'] == part and cur in f.get('parents', [])]
            if not matches:
                return None
            found = matches[0]
            cur = found['id']
        return found

    def all_ids(self):
        return list(self.load_state()['files'].keys())


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.drive = FakeDriveHarness(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def run_vault(self, *args, extra_env=None):
        return subprocess.run(
            [sys.executable, str(VAULT_TOOL), *args],
            capture_output=True,
            text=True,
            env=self.drive.env(extra_env),
        )

    def assertNoDriveIdsLeaked(self, *texts):
        ids = self.drive.all_ids() + ['id_secret_999']
        for text in texts:
            for fid in ids:
                self.assertNotIn(fid, text, f'Raw Drive ID {fid!r} leaked in output')

    def test_bootstrap_dry_run_writes_nothing_and_real_run_is_idempotent(self):
        dry = self.run_vault('bootstrap', '--dry-run')
        self.assertEqual(0, dry.returncode, dry.stderr)
        self.assertNoDriveIdsLeaked(dry.stdout, dry.stderr)
        dry_data = json.loads(dry.stdout)
        self.assertTrue(dry_data['dry_run'])
        self.assertEqual(['would_create'] * 3, [f['action'] for f in dry_data['files']])
        self.assertFalse(any(c[:3] == ['drive', 'files', 'create'] for c in self.drive.calls()))

        self.drive.clear_calls()
        first = self.run_vault('bootstrap')
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertNoDriveIdsLeaked(first.stdout, first.stderr)
        first_data = json.loads(first.stdout)
        self.assertEqual([], first_data['differences'])
        self.assertEqual(['created'] * 3, [f['action'] for f in first_data['files']])

        start_here = self.drive.find_by_path('Nix/START-HERE.md')
        conventions = self.drive.find_by_path('Nix/CONVENTIONS.md')
        app_json = self.drive.find_by_path('Nix/.obsidian/app.json')
        self.assertIsNotNone(start_here)
        self.assertIsNotNone(conventions)
        self.assertIsNotNone(app_json)
        self.assertEqual('text/markdown', start_here['mimeType'])
        self.assertIn('[Company](Company/)', start_here['content'])
        self.assertIn('[Ventures](Ventures/)', start_here['content'])
        for key in ('title', 'type', 'venture', 'status', 'updated', 'source'):
            self.assertIn(f'`{key}`', conventions['content'])
        self.assertIn('_attachments/', conventions['content'])
        self.assertIn('.index.md', conventions['content'])
        cfg = json.loads(app_json['content'])
        self.assertTrue(cfg['useMarkdownLinks'])
        self.assertEqual('relative', cfg['newLinkFormat'])
        self.assertEqual('./_attachments', cfg['attachmentFolderPath'])

        # Second run is strictly idempotent: zero create calls, zero differences.
        self.drive.clear_calls()
        second = self.run_vault('bootstrap')
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertNoDriveIdsLeaked(second.stdout, second.stderr)
        second_data = json.loads(second.stdout)
        self.assertEqual([], second_data['differences'])
        self.assertEqual(['unchanged'] * 3, [f['action'] for f in second_data['files']])
        self.assertFalse(any(c[:3] == ['drive', 'files', 'create'] for c in self.drive.calls()))

    def test_bootstrap_never_overwrites_existing_files_and_reports_differences(self):
        self.assertEqual(0, self.run_vault('bootstrap').returncode)
        state = self.drive.load_state()
        start_id = self.drive.find_by_path('Nix/START-HERE.md')['id']
        app_id = self.drive.find_by_path('Nix/.obsidian/app.json')['id']
        custom_start = '# Custom Founder Home\nKept intact.\n'
        custom_app = json.dumps({'useMarkdownLinks': False, 'attachmentFolderPath': './files'})
        state['files'][start_id]['content'] = custom_start
        state['files'][app_id]['content'] = custom_app
        self.drive.reset_state(state)
        self.drive.clear_calls()

        res = self.run_vault('bootstrap')
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertNoDriveIdsLeaked(res.stdout, res.stderr)
        data = json.loads(res.stdout)
        diff_paths = {d['path']: d for d in data['differences']}
        self.assertEqual({'Nix/START-HERE.md', 'Nix/.obsidian/app.json'}, set(diff_paths))
        self.assertEqual('differs', diff_paths['Nix/START-HERE.md']['action'])
        self.assertIn('newLinkFormat', diff_paths['Nix/.obsidian/app.json']['missing_keys'])
        self.assertIn('useMarkdownLinks', diff_paths['Nix/.obsidian/app.json']['mismatched_keys'])
        self.assertFalse(any(c[:3] == ['drive', 'files', 'create'] for c in self.drive.calls()))
        # Original file contents are untouched.
        self.assertEqual(custom_start, self.drive.find_by_path('Nix/START-HERE.md')['content'])
        self.assertEqual(custom_app, self.drive.find_by_path('Nix/.obsidian/app.json')['content'])

    def test_resolve_nix_follows_pagination_and_rejects_missing_or_ambiguous(self):
        # Add a non-owned Nix folder and paginate 1 item per page to prove pagination works.
        self.drive.add_item('drv_nix_shared', 'Nix', 'application/vnd.google-apps.folder', 'root', owned_by_me=False)
        res = self.run_vault('bootstrap', '--dry-run', extra_env={'FAKE_GWS_PAGE_SIZE': '1'})
        self.assertEqual(0, res.returncode, res.stderr)
        list_calls = [c for c in self.drive.calls() if c[:3] == ['drive', 'files', 'list']]
        self.assertTrue(any('pageToken' in c[c.index('--params') + 1] for c in list_calls))

        # Ambiguous: second owned Nix folder.
        self.drive.add_item('drv_nix_owned_2', 'Nix', 'application/vnd.google-apps.folder', 'root', owned_by_me=True)
        amb = self.run_vault('bootstrap', '--dry-run')
        self.assertEqual(1, amb.returncode)
        self.assertIn('Ambiguous', amb.stderr)
        self.assertNoDriveIdsLeaked(amb.stdout, amb.stderr)

        # Missing: zero owned Nix folders.
        self.drive.reset_state({'next_id': 20, 'files': {}})
        missing = self.run_vault('bootstrap', '--dry-run')
        self.assertEqual(1, missing.returncode)
        self.assertIn('Could not find', missing.stderr)

    def test_ensure_venture_creates_reuses_and_persists_redacted_receipt(self):
        receipt = Path(self.td.name) / 'knowledge-vault.json'
        first = self.run_vault(
            'ensure-venture', 'acme',
            '--name', 'Acme Widgets',
            '--mission', 'Validate 5 shops',
            '--receipt', str(receipt),
        )
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertNoDriveIdsLeaked(first.stdout, first.stderr, receipt.read_text(encoding='utf-8'))
        data = json.loads(first.stdout)
        self.assertEqual('provisioned', data['status'])
        self.assertEqual('created', data['folder_action'])
        self.assertEqual('created', data['index_action'])
        self.assertEqual('Nix/Ventures/acme', data['path'])
        self.assertEqual(64, len(data['folder_id_sha256']))
        self.assertEqual(0o600, stat.S_IMODE(receipt.stat().st_mode))

        readme = self.drive.find_by_path('Nix/Ventures/acme/README.md')
        self.assertIsNotNone(readme)
        self.assertIn('[PRD](PRD.md)', readme['content'])
        self.assertIn('[GTM](GTM.md)', readme['content'])
        self.assertIn('venture: "acme"', readme['content'])

        # Retry with the same receipt reuses it with ZERO gws calls.
        self.drive.clear_calls()
        second = self.run_vault('ensure-venture', 'acme', '--receipt', str(receipt))
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertTrue(json.loads(second.stdout)['reused_receipt'])
        self.assertEqual([], self.drive.calls())

        # Direct call without receipt reuses existing folder and README without creating duplicates.
        self.drive.clear_calls()
        third = self.run_vault('ensure-venture', 'acme')
        self.assertEqual(0, third.returncode, third.stderr)
        third_data = json.loads(third.stdout)
        self.assertEqual('reused', third_data['folder_action'])
        self.assertEqual('existing', third_data['index_action'])
        self.assertFalse(any(c[:3] == ['drive', 'files', 'create'] for c in self.drive.calls()))

    def test_ensure_venture_handles_partially_provisioned_and_preexisting_folders(self):
        # Partially provisioned: Ventures/beta exists without README.md.
        self.drive.add_item('drv_ventures', 'Ventures', 'application/vnd.google-apps.folder', 'drv_nix_owned')
        self.drive.add_item('drv_beta', 'beta', 'application/vnd.google-apps.folder', 'drv_ventures')
        res = self.run_vault('ensure-venture', 'beta', '--name', 'Beta Co')
        self.assertEqual(0, res.returncode, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual('reused', data['folder_action'])
        self.assertEqual('created', data['index_action'])
        # Only one beta folder exists.
        beta_folders = [
            f for f in self.drive.load_state()['files'].values()
            if f['name'] == 'beta' and f['mimeType'] == 'application/vnd.google-apps.folder'
        ]
        self.assertEqual(1, len(beta_folders))

        # Preexisting README is never overwritten.
        readme = self.drive.find_by_path('Nix/Ventures/beta/README.md')
        state = self.drive.load_state()
        state['files'][readme['id']]['content'] = '# Hand-edited Beta Index\n'
        self.drive.reset_state(state)
        self.drive.clear_calls()
        res2 = self.run_vault('ensure-venture', 'beta')
        self.assertEqual(0, res2.returncode, res2.stderr)
        self.assertEqual('existing', json.loads(res2.stdout)['index_action'])
        self.assertEqual('# Hand-edited Beta Index\n', self.drive.find_by_path('Nix/Ventures/beta/README.md')['content'])

    def test_index_native_generates_missing_index_notes_idempotently(self):
        self.assertEqual(0, self.run_vault('ensure-venture', 'acme').returncode)
        acme_id = self.drive.find_by_path('Nix/Ventures/acme')['id']
        self.drive.add_item(
            'drv_doc1', 'Q1 Roadmap', 'application/vnd.google-apps.document', acme_id,
            web_view_link='https://docs.google.com/document/d/drv_doc1/edit',
        )
        self.drive.add_item(
            'drv_sheet1', 'Unit Economics', 'application/vnd.google-apps.spreadsheet', acme_id,
            web_view_link='https://docs.google.com/spreadsheets/d/drv_sheet1/edit',
        )
        self.drive.add_item(
            'drv_slides1', 'Pitch / Deck', 'application/vnd.google-apps.presentation', acme_id,
            web_view_link='https://docs.google.com/presentation/d/drv_slides1/edit',
        )
        # Preexisting index note must not be overwritten.
        self.drive.add_item(
            'drv_doc2', 'Existing Doc', 'application/vnd.google-apps.document', acme_id,
            web_view_link='https://docs.google.com/document/d/drv_doc2/edit',
        )
        existing_body = '---\ntitle: Existing Doc\ntype: index-note\nventure: acme\nstatus: active\nupdated: "2026-11-28"\nsource: founder\n---\n\nCustom summary.\n'
        self.drive.add_item('drv_doc2_idx', 'Existing Doc.index.md', 'text/markdown', acme_id, content=existing_body)

        self.drive.clear_calls()
        res = self.run_vault('index-native', 'Ventures/acme')
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertNoDriveIdsLeaked(res.stdout, res.stderr)
        self.assertNotIn('https://docs.google.com/', res.stdout)
        data = json.loads(res.stdout)
        by_path = {entry['path']: entry for entry in data['indexed']}
        self.assertEqual('created', by_path['Nix/Ventures/acme/Q1 Roadmap.index.md']['action'])
        self.assertEqual('created', by_path['Nix/Ventures/acme/Unit Economics.index.md']['action'])
        self.assertEqual('created', by_path['Nix/Ventures/acme/Pitch - Deck.index.md']['action'])
        self.assertEqual('existing', by_path['Nix/Ventures/acme/Existing Doc.index.md']['action'])

        roadmap = self.drive.find_by_path('Nix/Ventures/acme/Q1 Roadmap.index.md')
        self.assertIn('type: index-note', roadmap['content'])
        self.assertIn('venture: "acme"', roadmap['content'])
        self.assertIn('TODO: Add a one-line summary.', roadmap['content'])
        self.assertIn('https://docs.google.com/document/d/drv_doc1/edit', roadmap['content'])
        # Existing index note untouched.
        self.assertEqual(existing_body, self.drive.find_by_path('Nix/Ventures/acme/Existing Doc.index.md')['content'])

        # Second run creates nothing.
        self.drive.clear_calls()
        res2 = self.run_vault('index-native', 'Nix/Ventures/acme')
        self.assertEqual(0, res2.returncode, res2.stderr)
        self.assertFalse(any(c[:3] == ['drive', 'files', 'create'] for c in self.drive.calls()))

    def test_lint_passes_generated_vault_and_flags_violations_without_leaking_ids(self):
        self.assertEqual(0, self.run_vault('bootstrap').returncode)
        self.assertEqual(0, self.run_vault('ensure-venture', 'acme', '--name', 'Acme').returncode)
        acme_id = self.drive.find_by_path('Nix/Ventures/acme')['id']
        self.drive.add_item(
            'drv_doc1', 'Spec', 'application/vnd.google-apps.document', acme_id,
            web_view_link='https://drive.google.com/file/d/drv_doc1/view',
        )
        self.assertEqual(0, self.run_vault('index-native', 'Ventures/acme').returncode)

        # Clean vault passes lint (including generated index notes).
        clean = self.run_vault('lint')
        self.assertEqual(0, clean.returncode, clean.stderr)
        clean_data = json.loads(clean.stdout)
        self.assertEqual(0, clean_data['failed'])
        self.assertEqual(4, clean_data['checked'])
        self.assertNoDriveIdsLeaked(clean.stdout, clean.stderr)

        # Add a note missing frontmatter and a note with an absolute Drive URL for a local note.
        self.drive.add_item('drv_bad1', 'no-frontmatter.md', 'text/markdown', acme_id, content='# Raw Note\nHello.\n')
        bad_link_body = (
            '---\n'
            'title: "Bad Links"\n'
            'type: note\n'
            'venture: acme\n'
            'status: draft\n'
            'updated: "2026-11-28"\n'
            'source: test\n'
            '---\n\n'
            '# Bad Links\n'
            'See [other](https://drive.google.com/file/d/drv_bad1/view).\n'
        )
        self.drive.add_item('drv_bad2', 'bad-links.md', 'text/markdown', acme_id, content=bad_link_body)

        dirty = self.run_vault('lint', '--folder', 'Ventures/acme')
        self.assertEqual(1, dirty.returncode)
        self.assertNoDriveIdsLeaked(dirty.stdout, dirty.stderr)
        self.assertNotIn('https://drive.google.com/', dirty.stdout)
        dirty_data = json.loads(dirty.stdout)
        self.assertEqual(2, dirty_data['failed'])
        by_code = {(i['path'], i['code']) for i in dirty_data['issues']}
        self.assertIn(('Nix/Ventures/acme/no-frontmatter.md', 'missing-frontmatter'), by_code)
        self.assertIn(('Nix/Ventures/acme/bad-links.md', 'absolute-drive-url'), by_code)

    def test_gws_errors_are_sanitized_and_lint_catches_missing_keys_and_folder_urls(self):
        auth_fail = self.run_vault('bootstrap', extra_env={'FAKE_GWS_EXIT': '2'})
        self.assertEqual(2, auth_fail.returncode)
        self.assertNoDriveIdsLeaked(auth_fail.stdout, auth_fail.stderr)
        self.assertIn('authentication failed', auth_fail.stderr)

        api_fail = self.run_vault('bootstrap', extra_env={'FAKE_GWS_EXIT': '1'})
        self.assertEqual(1, api_fail.returncode)
        self.assertNoDriveIdsLeaked(api_fail.stdout, api_fail.stderr)

        # Partial frontmatter + folder URL inside an index note must both be flagged.
        self.assertEqual(0, self.run_vault('ensure-venture', 'acme').returncode)
        acme_id = self.drive.find_by_path('Nix/Ventures/acme')['id']
        partial_fm = (
            '---\n'
            'title: "Incomplete"\n'
            'type: index-note\n'
            '---\n\n'
            'See folder https://drive.google.com/drive/folders/drv_acme.\n'
        )
        self.drive.add_item('drv_partial', 'Partial.index.md', 'text/markdown', acme_id, content=partial_fm)
        lint_res = self.run_vault('lint', '--folder', 'Ventures/acme')
        self.assertEqual(1, lint_res.returncode)
        self.assertNoDriveIdsLeaked(lint_res.stdout, lint_res.stderr)
        issues = json.loads(lint_res.stdout)['issues']
        codes = {i['code'] for i in issues}
        self.assertIn('missing-frontmatter-keys', codes)
        self.assertIn('absolute-drive-url', codes)

    def test_no_legacy_venture_names_hardcoded(self):
        text = (
            VAULT_TOOL.read_text(encoding='utf-8')
            + (ROOT / 'skills/google-drive/SKILL.md').read_text(encoding='utf-8')
            + (ROOT / 'docs/KNOWLEDGE-VAULT.md').read_text(encoding='utf-8')
        ).lower()
        for legacy in ('pulse', 'algua', 'meabeclick', 'nixvert'):
            self.assertNotIn(legacy, text)


if __name__ == '__main__':
    unittest.main()
