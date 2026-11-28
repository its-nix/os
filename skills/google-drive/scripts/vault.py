#!/usr/bin/env python3
"""Obsidian-compatible Google Drive vault management for Nix.

Uses Python stdlib + the pinned `gws` CLI in the Hermes image. Resolves the
single owned top-level `Nix` folder in My Drive before any read or write, never
overwrites existing files, never shares or deletes, and never prints or stores
raw Drive folder/file IDs or URLs in logs or receipts.

  vault.py bootstrap [--dry-run]
  vault.py ensure-venture <id> [--name NAME] [--mission TEXT] [--receipt PATH] [--dry-run]
  vault.py index-native <folder> [--no-recursive] [--dry-run]
  vault.py lint [--folder PATH]
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

FOLDER_MIME = 'application/vnd.google-apps.folder'
MARKDOWN_MIME = 'text/markdown'
JSON_MIME = 'application/json'
NATIVE_MIME_TYPES = {
    'application/vnd.google-apps.document': 'Google Doc',
    'application/vnd.google-apps.spreadsheet': 'Google Sheet',
    'application/vnd.google-apps.presentation': 'Google Slides',
}
REQUIRED_FRONTMATTER_KEYS = ('title', 'type', 'venture', 'status', 'updated', 'source')
SKIP_FOLDERS = {'.obsidian', '_attachments', 'backups'}
VENTURE_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
DRIVE_URL_RE = re.compile(r'https?://drive\.google\.com/[^\s)\]>"]+', re.IGNORECASE)
DRIVE_FOLDER_URL_RE = re.compile(r'https?://drive\.google\.com/drive/(?:u/\d+/)?folders/[^\s)\]>"]+', re.IGNORECASE)
SOURCE_LINE_RE = re.compile(r'^\s*[-*]\s+\*\*Source:\*\*\s+', re.IGNORECASE)
UPDATED_LINE_RE = re.compile(r'^updated:\s*.*$', re.MULTILINE)
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
MAX_PAGES = 500
SCHEMA_VERSION = 1

OBSIDIAN_APP_CONFIG = {
    'useMarkdownLinks': True,
    'newLinkFormat': 'relative',
    'attachmentFolderPath': './_attachments',
}


class VaultError(RuntimeError):
    """Error safe to print (never contains Drive IDs or URLs)."""


class GwsUnavailable(VaultError):
    """Raised when `gws` is missing, timed out, or unauthenticated."""


class GwsCommandError(VaultError):
    """Raised when `gws` returns a non-zero API or invocation error."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def utc_today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def yaml_quote(value):
    return json.dumps(' '.join(str(value).splitlines()).strip(), ensure_ascii=False)


def escape_query(value):
    return value.replace('\\', '\\\\').replace("'", "\\'")


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def sanitize_title(title):
    cleaned = re.sub(r'\s+', ' ', re.sub(r'[/\\\x00]+', '-', (title or '').strip()).strip())
    return cleaned or 'untitled'


def normalize_subpath(raw):
    parts = [p for p in (raw or '').strip().strip('/').split('/') if p]
    if parts and parts[0] == 'Nix':
        parts = parts[1:]
    for part in parts:
        if part in ('.', '..') or '\\' in part or '\x00' in part:
            raise VaultError('Folder path must be relative to Nix without traversal segments')
    return parts


def render_frontmatter(title, note_type, venture, status, source, today=None):
    date = today or utc_today()
    return (
        f'---\n'
        f'title: {yaml_quote(title)}\n'
        f'type: {note_type}\n'
        f'venture: {yaml_quote(venture)}\n'
        f'status: {status}\n'
        f'updated: {yaml_quote(date)}\n'
        f'source: {source}\n'
        f'---\n\n'
    )


def render_start_here(today=None):
    return render_frontmatter('Nix Knowledge Vault', 'home', 'company', 'active', 'vault-bootstrap', today) + (
        '# Nix Knowledge Vault\n\n'
        'Markdown-first working workspace for Nix in **My Drive → Nix**, structured for '
        'Obsidian and ordinary Markdown readers.\n\n'
        '## What lives where\n\n'
        '- [Vault Conventions](CONVENTIONS.md) — frontmatter schema, relative links, attachments, and index-note rules.\n'
        '- [Company](Company/) — shared company operations, notes, and working deliverables.\n'
        '- [Ventures](Ventures/) — one folder per venture (`Ventures/<id>/`) provisioned by Venture Factory with a `README.md` index note.\n\n'
        '## Canonical boundaries\n\n'
        '- Reviewed company policy and skill baselines stay in the read-only Git KB at `/knowledge/company`.\n'
        '- Promote durable learnings to the Git KB through evidence-linked PRs; never maintain two conflicting policy copies.\n'
        '- Keep native Google Docs, Sheets, and Slides in Drive and reference them via sibling `<title>.index.md` notes.\n'
        '- Keep raw customer data and bulk transcript imports out of shared notes. Sharing and permissions remain founder-gated.\n'
    )


def render_conventions(today=None):
    return render_frontmatter('Vault Conventions', 'reference', 'company', 'active', 'vault-bootstrap', today) + (
        '# Vault Conventions\n\n'
        'All notes under **Nix** follow portable Markdown conventions that render cleanly in Obsidian, GitHub, and plain editors.\n\n'
        '## YAML frontmatter\n\n'
        'Every Markdown note begins with YAML frontmatter containing these six required keys:\n\n'
        '- `title`: human-readable note title\n'
        '- `type`: note kind (`home`, `reference`, `index`, `index-note`, `prd`, `gtm`, `decision`, `note`)\n'
        '- `venture`: venture id (`company` for company-wide notes, or `<id>` for a venture)\n'
        '- `status`: lifecycle status (`draft`, `active`, `archived`, `superseded`)\n'
        '- `updated`: quoted ISO date (`"YYYY-MM-DD"`)\n'
        '- `source`: origin of the note (e.g. `founder`, `venture-factory`, `google-drive-native`)\n\n'
        '## Links\n\n'
        '- Use **relative Markdown links** between local notes and folders, such as `[Vault Home](START-HERE.md)`.\n'
        '- Never use absolute Google Drive URLs to link between local Markdown notes or vault folders.\n\n'
        '## Attachments\n\n'
        '- Store binary assets (images, PDFs, exports) in an `_attachments/` folder within each area '
        '(configured via `.obsidian/app.json` with `attachmentFolderPath: "./_attachments"`).\n\n'
        '## Native Google Docs, Sheets, and Slides\n\n'
        '- Never treat `.gdoc`, `.gsheet`, or `.gslides` files as editable Markdown.\n'
        '- Keep the original native file in Google Drive and create a sibling `<title>.index.md` Markdown note '
        'containing required YAML frontmatter, a one-line summary, and its `webViewLink`.\n'
        '- Link to native documents from other notes by linking relatively to their `<title>.index.md` note.\n'
    )


def render_obsidian_app_json():
    return json.dumps(OBSIDIAN_APP_CONFIG, indent=2) + '\n'


def render_venture_index(vid, name=None, mission=None, today=None):
    heading = (name or vid).strip()
    mission_block = f'\n{mission.strip()}\n' if (mission or '').strip() else ''
    return render_frontmatter(f'{heading} — Venture Index', 'index', vid, 'active', 'venture-factory', today) + (
        f'# {heading}\n{mission_block}\n'
        '## Core documents\n\n'
        '- [PRD](PRD.md) — approved product requirements and measurable outcomes\n'
        '- [GTM](GTM.md) — acquisition hypothesis, channels, and approval boundaries\n\n'
        '## Workspace conventions\n\n'
        '- Place binary assets under `_attachments/`.\n'
        '- Keep native Google Docs, Sheets, and Slides as sibling `<title>.index.md` notes.\n'
        '- See [Vault Home](../../START-HERE.md) and [Vault Conventions](../../CONVENTIONS.md).\n'
    )


def render_native_index(title, native_type, venture, web_view_link, today=None):
    clean = ' '.join(str(title).splitlines()).strip() or 'Untitled'
    return render_frontmatter(clean, 'index-note', venture, 'draft', 'google-drive-native', today) + (
        f'# {clean}\n\n'
        f'- **Format:** {native_type}\n'
        '- **Summary:** TODO: Add a one-line summary.\n'
        f'- **Source:** [Open in Google Drive]({web_view_link})\n'
    )


class GwsClient:
    """Thin wrapper over `gws`; never leaks raw Drive IDs or stderr."""

    def __init__(self, binary=None, timeout=120):
        self.binary = binary or os.environ.get('GWS_BIN', 'gws')
        self.timeout = timeout

    def _run(self, *argv, cwd=None):
        try:
            result = subprocess.run([self.binary, *argv], capture_output=True, text=True, timeout=self.timeout, cwd=cwd)
        except FileNotFoundError:
            raise GwsUnavailable('gws CLI is not available on PATH') from None
        except subprocess.TimeoutExpired:
            raise GwsUnavailable('gws command timed out') from None
        if result.returncode == 2:
            raise GwsUnavailable('gws authentication failed (exit 2); ask founder to restore grant')
        if result.returncode != 0:
            raise GwsCommandError(f'gws command failed (exit {result.returncode})')
        return result.stdout

    def _json(self, *argv, cwd=None, what='response'):
        raw = self._run(*argv, cwd=cwd)
        try:
            return json.loads(raw or '{}')
        except json.JSONDecodeError:
            raise GwsCommandError(f'gws returned invalid JSON for {what}') from None

    def list_files(self, query, fields):
        files, token, seen = [], None, set()
        for _ in range(MAX_PAGES):
            params = {'q': query, 'fields': fields, 'pageSize': 100}
            if token:
                params['pageToken'] = token
            data = self._json('drive', 'files', 'list', '--params', json.dumps(params), what='files list')
            batch = data.get('files', [])
            if not isinstance(batch, list):
                raise GwsCommandError('gws returned unexpected files list payload')
            files.extend(batch)
            token = data.get('nextPageToken')
            if not token:
                return files
            if token in seen:
                raise GwsCommandError('gws pagination repeated a pageToken')
            seen.add(token)
        raise GwsCommandError('gws pagination exceeded maximum page limit')

    def create_folder(self, name, parent_id):
        body = {'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id]}
        data = self._json('drive', 'files', 'create', '--json', json.dumps(body),
                          '--params', json.dumps({'fields': 'id,name,mimeType,parents'}), what='folder create')
        if not data.get('id'):
            raise GwsCommandError('gws folder create returned no id')
        return data

    def upload_file(self, name, parent_id, content, mime_type):
        body = {'name': name, 'mimeType': mime_type, 'parents': [parent_id]}
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / 'upload.tmp').write_text(content, encoding='utf-8')
            data = self._json('drive', 'files', 'create', '--json', json.dumps(body),
                              '--params', json.dumps({'fields': 'id,name,mimeType,parents'}),
                              '--upload', 'upload.tmp', cwd=tmpdir, what='file upload')
        if not data.get('id'):
            raise GwsCommandError('gws file upload returned no id')
        return data

    def download_text(self, file_id):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run('drive', 'files', 'get', '--params', json.dumps({'fileId': file_id, 'alt': 'media'}),
                      '--output', 'download.tmp', cwd=tmpdir)
            target = Path(tmpdir) / 'download.tmp'
            if not target.is_file():
                raise GwsCommandError('gws download did not produce the expected output file')
            return target.read_text(encoding='utf-8', errors='replace')


def resolve_nix_root(client):
    query = f"'root' in parents and name = 'Nix' and mimeType = '{FOLDER_MIME}' and trashed = false"
    owned = [f for f in client.list_files(query, 'nextPageToken,files(id,ownedByMe)') if f.get('ownedByMe') is True and f.get('id')]
    if not owned:
        raise VaultError("Could not find an owned top-level 'Nix' folder in My Drive")
    if len(owned) > 1:
        raise VaultError("Ambiguous top-level 'Nix' folder: found multiple owned matches")
    return owned[0]['id']


def list_children(client, parent_id, name=None, mime_type=None, exclude_folders=False,
                  fields='nextPageToken,files(id,name,mimeType,parents)'):
    clauses = [f"'{escape_query(parent_id)}' in parents", 'trashed = false']
    if name is not None:
        clauses.append(f"name = '{escape_query(name)}'")
    if mime_type is not None:
        clauses.append(f"mimeType = '{escape_query(mime_type)}'")
    elif exclude_folders:
        clauses.append(f"mimeType != '{FOLDER_MIME}'")
    items = client.list_files(' and '.join(clauses), fields)
    return [i for i in items if not i.get('parents') or parent_id in i.get('parents', [])]


def find_or_create_folder(client, parent_id, name, rel_path, dry_run=False):
    matches = list_children(client, parent_id, name=name, mime_type=FOLDER_MIME)
    if len(matches) > 1:
        raise VaultError(f'Ambiguous folder at {rel_path}: multiple matches exist')
    if len(matches) == 1:
        return matches[0]['id'], 'reused'
    if dry_run:
        return None, 'would_create'
    return client.create_folder(name, parent_id)['id'], 'created'


def resolve_folder_path(client, nix_id, parts):
    cur_id, walked = nix_id, ['Nix']
    for part in parts:
        walked.append(part)
        matches = list_children(client, cur_id, name=part, mime_type=FOLDER_MIME)
        if not matches:
            raise VaultError(f'Folder not found: {"/".join(walked)}')
        if len(matches) > 1:
            raise VaultError(f'Ambiguous folder: {"/".join(walked)}')
        cur_id = matches[0]['id']
    return cur_id, '/'.join(walked)


def walk_drive_tree(client, start_id, start_label, fields, recursive=True):
    queue, visited = [(start_id, start_label)], set()
    while queue:
        cur_id, cur_label = queue.pop(0)
        if cur_id in visited:
            continue
        visited.add(cur_id)
        children = sorted(list_children(client, cur_id, fields=fields),
                          key=lambda c: (c.get('name') or '', c.get('mimeType') or ''))
        yield cur_id, cur_label, children
        if recursive:
            for child in children:
                if child.get('mimeType') == FOLDER_MIME and (child.get('name') or '') not in SKIP_FOLDERS:
                    queue.append((child['id'], f"{cur_label}/{child['name']}"))


def parse_frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return None, text, 1, []
    end_idx = next((i for i in range(1, len(lines)) if lines[i].strip() in ('---', '...')), None)
    if end_idx is None:
        return None, text, 1, []
    mapping, duplicates, current_key = {}, [], None
    for raw_line in lines[1:end_idx]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if raw_line[0].isspace():
            if current_key is not None and not mapping[current_key]:
                mapping[current_key] = stripped.lstrip('-').strip()
            continue
        if ':' not in raw_line:
            continue
        key, val = (p.strip() for p in raw_line.split(':', 1))
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1].strip()
        if key in mapping:
            duplicates.append(key)
        mapping[key], current_key = val, key
    return mapping, '\n'.join(lines[end_idx + 1:]), end_idx + 2, duplicates


def compare_managed_file(path, expected, actual, is_json=False):
    if is_json:
        try:
            exp_obj, act_obj = json.loads(expected), json.loads(actual)
        except json.JSONDecodeError:
            return {'path': path, 'action': 'differs', 'detail': 'existing file is not valid JSON',
                    'actual_sha256': sha256_text(actual), 'expected_sha256': sha256_text(expected)}
        if isinstance(act_obj, dict) and act_obj == exp_obj:
            return {'path': path, 'action': 'unchanged'}
        return {
            'path': path, 'action': 'differs',
            'missing_keys': sorted(k for k in exp_obj if not isinstance(act_obj, dict) or k not in act_obj),
            'mismatched_keys': sorted(k for k in exp_obj if isinstance(act_obj, dict) and k in act_obj and act_obj[k] != exp_obj[k]),
            'extra_keys': sorted(k for k in act_obj if k not in exp_obj) if isinstance(act_obj, dict) else [],
            'actual_sha256': sha256_text(actual), 'expected_sha256': sha256_text(expected),
        }
    norm = lambda s: UPDATED_LINE_RE.sub('updated: "<DATE>"', s.replace('\r\n', '\n')).strip()
    norm_exp, norm_act = norm(expected), norm(actual)
    if norm_act == norm_exp:
        return {'path': path, 'action': 'unchanged'}
    fm, _, _, _ = parse_frontmatter(actual)
    missing_fm = list(REQUIRED_FRONTMATTER_KEYS) if fm is None else [k for k in REQUIRED_FRONTMATTER_KEYS if not (fm.get(k) or '').strip()]
    return {'path': path, 'action': 'differs', 'actual_lines': len(norm_act.splitlines()),
            'expected_lines': len(norm_exp.splitlines()), 'missing_frontmatter_keys': missing_fm,
            'actual_sha256': sha256_text(actual), 'expected_sha256': sha256_text(expected)}


def cmd_bootstrap(client, dry_run=False):
    nix_id = resolve_nix_root(client)
    folder_results, folder_ids = [], {}
    for folder_name in ('Company', 'Ventures', '.obsidian'):
        rel = f'Nix/{folder_name}'
        fid, action = find_or_create_folder(client, nix_id, folder_name, rel, dry_run=dry_run)
        folder_ids[folder_name] = fid
        folder_results.append({'path': rel, 'action': 'existing' if action == 'reused' else action})

    managed = [
        (nix_id, 'START-HERE.md', 'Nix/START-HERE.md', MARKDOWN_MIME, render_start_here(), False),
        (nix_id, 'CONVENTIONS.md', 'Nix/CONVENTIONS.md', MARKDOWN_MIME, render_conventions(), False),
        (folder_ids['.obsidian'], 'app.json', 'Nix/.obsidian/app.json', JSON_MIME, render_obsidian_app_json(), True),
    ]
    file_results, differences = [], []
    for parent_id, name, rel_path, mime_type, content, is_json in managed:
        if parent_id is None:
            file_results.append({'path': rel_path, 'action': 'would_create'})
            continue
        existing = list_children(client, parent_id, name=name, exclude_folders=True)
        if not existing:
            if dry_run:
                file_results.append({'path': rel_path, 'action': 'would_create'})
            else:
                client.upload_file(name, parent_id, content, mime_type)
                file_results.append({'path': rel_path, 'action': 'created'})
        elif len(existing) > 1:
            entry = {'path': rel_path, 'action': 'ambiguous', 'count': len(existing),
                     'detail': 'multiple files with this name exist; left untouched'}
            file_results.append(entry)
            differences.append(entry)
        else:
            entry = compare_managed_file(rel_path, content, client.download_text(existing[0]['id']), is_json=is_json)
            file_results.append(entry)
            if entry['action'] != 'unchanged':
                differences.append(entry)
    return {'command': 'bootstrap', 'dry_run': dry_run, 'folders': folder_results,
            'files': file_results, 'differences': differences}


def load_valid_receipt(receipt_path, vid):
    path = Path(receipt_path)
    if path.is_symlink():
        raise VaultError('Receipt path must not be a symlink')
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if (isinstance(data, dict) and data.get('status') == 'provisioned' and data.get('venture') == vid
            and data.get('path') == f'Nix/Ventures/{vid}'
            and isinstance(data.get('folder_id_sha256'), str) and SHA256_RE.fullmatch(data['folder_id_sha256'])):
        return data
    return None


def cmd_ensure_venture(client, vid, name=None, mission=None, receipt_path=None, dry_run=False):
    if not VENTURE_ID_RE.fullmatch(vid or '') or vid in ('.', '..'):
        raise VaultError('Invalid venture id for Drive folder name')
    if receipt_path and (reused := load_valid_receipt(receipt_path, vid)) is not None:
        return dict(reused, reused_receipt=True)

    nix_id = resolve_nix_root(client)
    ventures_id, _ = find_or_create_folder(client, nix_id, 'Ventures', 'Nix/Ventures', dry_run=dry_run)
    rel_venture = f'Nix/Ventures/{vid}'
    venture_id, folder_action = (None, 'would_create') if ventures_id is None else find_or_create_folder(
        client, ventures_id, vid, rel_venture, dry_run=dry_run)

    index_id_sha256 = None
    if venture_id is None:
        index_action = 'would_create'
    elif existing_readme := list_children(client, venture_id, name='README.md', exclude_folders=True):
        index_action, index_id_sha256 = 'existing', sha256_text(existing_readme[0]['id'])
    elif dry_run:
        index_action = 'would_create'
    else:
        created = client.upload_file('README.md', venture_id, render_venture_index(vid, name=name, mission=mission), MARKDOWN_MIME)
        index_action, index_id_sha256 = 'created', sha256_text(created['id'])

    receipt = {
        'schema_version': SCHEMA_VERSION, 'venture': vid, 'path': rel_venture,
        'status': 'planned' if dry_run else 'provisioned', 'folder_action': folder_action,
        'index_note': 'README.md', 'index_action': index_action, 'recorded_at': utc_now(),
    }
    if not dry_run:
        receipt['parent_id_sha256'] = sha256_text(ventures_id)
        receipt['folder_id_sha256'] = sha256_text(venture_id)
        if index_id_sha256:
            receipt['index_note_id_sha256'] = index_id_sha256
        if receipt_path:
            atomic_write(receipt_path, json.dumps(receipt, indent=2) + '\n', mode=0o600)
    return receipt


def cmd_index_native(client, folder, recursive=True, dry_run=False):
    parts = normalize_subpath(folder)
    start_id, start_label = resolve_folder_path(client, resolve_nix_root(client), parts)
    venture = parts[1] if len(parts) >= 2 and parts[0].lower() == 'ventures' else 'company'
    indexed = []
    fields = 'nextPageToken,files(id,name,mimeType,parents,webViewLink)'
    for cur_id, cur_label, children in walk_drive_tree(client, start_id, start_label, fields, recursive=recursive):
        existing = {c.get('name') for c in children if c.get('mimeType') != FOLDER_MIME and c.get('name')}
        for child in children:
            native_type = NATIVE_MIME_TYPES.get(child.get('mimeType'))
            if not native_type:
                continue
            name = child.get('name') or ''
            index_name = f'{sanitize_title(name)}.index.md'
            rel_index = f'{cur_label}/{index_name}'
            if index_name in existing:
                indexed.append({'path': rel_index, 'native_type': native_type, 'action': 'existing'})
            elif not (link := (child.get('webViewLink') or '').strip()):
                indexed.append({'path': rel_index, 'native_type': native_type, 'action': 'skipped_missing_link'})
            elif dry_run:
                indexed.append({'path': rel_index, 'native_type': native_type, 'action': 'would_create'})
            else:
                client.upload_file(index_name, cur_id, render_native_index(name, native_type, venture, link), MARKDOWN_MIME)
                existing.add(index_name)
                indexed.append({'path': rel_index, 'native_type': native_type, 'action': 'created'})
    return {'command': 'index-native', 'folder': start_label, 'dry_run': dry_run, 'indexed': indexed}


def lint_markdown(rel_path, text):
    issues = []
    fm, body, body_start, duplicates = parse_frontmatter(text)
    if fm is None:
        issues.append({'path': rel_path, 'code': 'missing-frontmatter', 'detail': 'YAML frontmatter block is required'})
    else:
        if duplicates:
            issues.append({'path': rel_path, 'code': 'duplicate-frontmatter-key', 'keys': sorted(set(duplicates)),
                           'detail': 'Duplicate YAML frontmatter keys are not allowed'})
        if missing := [k for k in REQUIRED_FRONTMATTER_KEYS if not (fm.get(k) or '').strip()]:
            issues.append({'path': rel_path, 'code': 'missing-frontmatter-keys', 'keys': missing,
                           'detail': 'Missing required YAML frontmatter keys'})
    is_index = rel_path.endswith('.index.md') or (isinstance(fm, dict) and fm.get('type') == 'index-note')
    for offset, line in enumerate(body.splitlines()):
        lineno = body_start + offset
        if DRIVE_FOLDER_URL_RE.search(line):
            issues.append({'path': rel_path, 'code': 'absolute-drive-url', 'line': lineno,
                           'detail': 'Use relative links for vault folders instead of absolute Google Drive URLs'})
        elif DRIVE_URL_RE.search(line) and not (is_index and SOURCE_LINE_RE.match(line)):
            issues.append({'path': rel_path, 'code': 'absolute-drive-url', 'line': lineno,
                           'detail': 'Use relative links for local notes instead of absolute Google Drive URLs'})
    return issues


def cmd_lint(client, folder=None):
    parts = normalize_subpath(folder) if folder else []
    start_id, start_label = resolve_folder_path(client, resolve_nix_root(client), parts)
    checked, failed, issues = 0, 0, []
    fields = 'nextPageToken,files(id,name,mimeType,parents)'
    for _, cur_label, children in walk_drive_tree(client, start_id, start_label, fields, recursive=True):
        for child in children:
            name, mime = child.get('name') or '', child.get('mimeType')
            if mime != FOLDER_MIME and (name.lower().endswith('.md') or mime == MARKDOWN_MIME):
                checked += 1
                note_issues = lint_markdown(f'{cur_label}/{name}', client.download_text(child['id']))
                if note_issues:
                    failed += 1
                    issues.extend(note_issues)
    return {'command': 'lint', 'scope': start_label, 'checked': checked,
            'passed': checked - failed, 'failed': failed, 'issues': issues}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gws', help='Path to gws binary (default: GWS_BIN env or `gws` on PATH)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('bootstrap', help='Ensure START-HERE.md, CONVENTIONS.md, and .obsidian/app.json').add_argument('--dry-run', action='store_true')
    p_vent = sub.add_parser('ensure-venture', help='Ensure Nix/Ventures/<id>/ and its README.md index note')
    p_vent.add_argument('id', help='Venture identifier')
    p_vent.add_argument('--name', help='Human-readable venture name')
    p_vent.add_argument('--mission', help='Venture mission statement')
    p_vent.add_argument('--receipt', help='Local path to write/reuse a redacted provisioning receipt')
    p_vent.add_argument('--dry-run', action='store_true')
    p_idx = sub.add_parser('index-native', help='Generate <title>.index.md notes for native Docs/Sheets/Slides')
    p_idx.add_argument('folder', help='Folder path relative to Nix (e.g. Ventures/<id>)')
    p_idx.add_argument('--no-recursive', action='store_true', help='Only scan the target folder directly')
    p_idx.add_argument('--dry-run', action='store_true')
    sub.add_parser('lint', help='Lint Markdown notes for frontmatter and relative-link hygiene').add_argument('--folder')

    args = parser.parse_args(argv)
    client = GwsClient(binary=args.gws)
    try:
        if args.command == 'bootstrap':
            result = cmd_bootstrap(client, dry_run=args.dry_run)
        elif args.command == 'ensure-venture':
            result = cmd_ensure_venture(client, args.id, name=args.name, mission=args.mission,
                                        receipt_path=args.receipt, dry_run=args.dry_run)
        elif args.command == 'index-native':
            result = cmd_index_native(client, args.folder, recursive=not args.no_recursive, dry_run=args.dry_run)
        else:
            result = cmd_lint(client, folder=args.folder)
    except VaultError as exc:
        print(f'vault: {exc}', file=sys.stderr)
        return 2 if isinstance(exc, GwsUnavailable) else 1
    print(json.dumps(result, indent=2))
    return 1 if args.command == 'lint' and result.get('failed', 0) > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
