#!/usr/bin/env python3
"""Mint or renew the Hermes -> T3 outside-agent MCP credential through T3's native OAuth.

T3 limits CLI-issued MCP sessions to `approval-required`; full-access outside agents are
approved through T3's own OAuth + PKCE flow with a one-time pairing code. This runs from the
root deployment (which already administers both containers), never inside Hermes or T3, and
never prints the token. The credential is written owner-only into Hermes' private `.env`
as MCP_T3_API_KEY (Hermes' native
`hermes mcp add t3 --auth header` convention: header `Bearer ${MCP_T3_API_KEY}`), with its expiry in a non-secret sidecar. T3 issues 30-day tokens without
refresh, so the daily deployment renews it when fewer than RENEW_DAYS remain.
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = 'http://127.0.0.1:3773'
CONTAINER = 'nixos-t3'
REDIRECT = 'http://127.0.0.1:9/hermes-callback'  # never contacted; code is read from redirectTo
RENEW_DAYS = 7
KEYS = ('MCP_T3_API_KEY',)


class Refusal(RuntimeError):
    pass


def http(method, path, body=None, form=False):
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(ORIGIN + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise Refusal(f'T3 OAuth step {path} failed (HTTP {exc.code})') from None


def pairing_code(run=subprocess.run):
    result = run(['docker', 'exec', CONTAINER, 't3', 'auth', 'pairing', 'create', '--ttl', '5m',
                  '--label', 'nix-hermes-mcp', '--json'], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise Refusal('Could not create a one-time T3 pairing code')
    return json.loads(result.stdout)['credential']


def mint(code_source=pairing_code, request=http):
    resource = request('GET', '/.well-known/oauth-protected-resource/mcp')['resource']
    client = request('POST', '/oauth/mcp/register', {
        'client_name': 'Nix Hermes', 'redirect_uris': [REDIRECT], 'token_endpoint_auth_method': 'none'})
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(16)
    authorization = {'response_type': 'code', 'client_id': client['client_id'], 'redirect_uri': REDIRECT,
                     'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': state,
                     'resource': resource}
    decision = request('POST', '/oauth/mcp/decision', {
        'authorization': authorization,
        'decision': {'_tag': 'pairing-code', 'access': 'full-access', 'code': code_source()}})
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(decision['redirectTo']).query)
    if query.get('state') != [state] or 'code' not in query:
        raise Refusal('T3 OAuth approval did not return a matching authorization code')
    token = request('POST', '/oauth/mcp/token', {
        'grant_type': 'authorization_code', 'code': query['code'][0], 'redirect_uri': REDIRECT,
        'client_id': client['client_id'], 'code_verifier': verifier, 'resource': resource}, form=True)
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(token.get('expires_in', 30 * 86400)))
    return token['access_token'], expires


TOKEN_RE = re.compile(r'[A-Za-z0-9._~+/=-]{20,4096}')


def safe_write(path, text, owner):
    """Atomic, no-follow, owner-only write: a random O_EXCL temp file, then rename over path."""
    if path.is_symlink() or path.parent.is_symlink():
        raise Refusal(f'Refusing to write through a symlink: {path.name}')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.chown(temporary, owner.st_uid, owner.st_gid)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_env(env_path, values):
    """Replace only our keys in Hermes' private .env, preserving every other line and ownership."""
    for value in values.values():
        if not TOKEN_RE.fullmatch(value):
            raise Refusal('T3 returned a credential with an unexpected format; not written')
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    kept = [line for line in lines if line.split('=', 1)[0].strip() not in KEYS]
    kept += [f'{k}={v}' for k, v in values.items()]
    owner = env_path.stat() if env_path.exists() else env_path.parent.stat()
    safe_write(env_path, '\n'.join(kept) + '\n', owner)


def current_token(hermes_home):
    env = hermes_home / '.env'
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        key, _, value = line.partition('=')
        if key.strip() == KEYS[0]:
            return value.strip()
    return None


def probe(token, request=None):
    """True when T3 accepts the credential for MCP (initialize)."""
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'nix-deploy', 'version': '1'}}})
    req = urllib.request.Request(ORIGIN + '/mcp', data=body.encode(), method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status == 200
    except urllib.error.HTTPError:
        return False


def fingerprint(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def ensure(hermes_home, now=None, minter=mint, prober=probe):
    """Returns True when Hermes must (re)load the credential: newly minted, or never confirmed loaded."""
    now = now or datetime.now(timezone.utc)
    sidecar = hermes_home / 'nix' / 't3-mcp.json'
    state = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    token = current_token(hermes_home)
    if (state and token and datetime.fromisoformat(state['expires_at']) - now > timedelta(days=RENEW_DAYS)
            and prober(token)):
        return state.get('loaded_fingerprint') != fingerprint(token)
    token, expires = minter()
    write_env(hermes_home / '.env', {KEYS[0]: token})
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    safe_write(sidecar, json.dumps({'expires_at': expires.isoformat(), 'access': 'full-access',
                                    'renewed_at': now.isoformat()}) + '\n', hermes_home.stat())
    return True


def venture_profiles(hermes_home):
    """Registry-owned lead profiles (marker names its own profile; no symlinks)."""
    profiles = hermes_home / 'profiles'
    if not profiles.is_dir() or profiles.is_symlink():
        return []
    found = []
    for marker in sorted(profiles.glob('*/nix-venture.json')):
        directory = marker.parent
        if directory.is_symlink() or marker.is_symlink() or not marker.is_file():
            continue
        try:
            if json.loads(marker.read_text()).get('venture') == directory.name:
                found.append(directory)
        except ValueError:
            continue
    return found


def share_with_leads(hermes_home):
    """Profile secrets are scoped under the multiplexed gateway: give each venture lead the current
    credential in its own private .env. Returns True when any lead's value changed."""
    token = current_token(hermes_home)
    changed = False
    for profile in venture_profiles(hermes_home):
        if current_token(profile) != token:
            write_env(profile / '.env', {KEYS[0]: token})
            changed = True
    return changed


def mark_loaded(hermes_home):
    """Called by the deployment only after Hermes restarted healthy with the new credential."""
    sidecar = hermes_home / 'nix' / 't3-mcp.json'
    state = json.loads(sidecar.read_text())
    state['loaded_fingerprint'] = fingerprint(current_token(hermes_home) or '')
    safe_write(sidecar, json.dumps(state) + '\n', hermes_home.stat())


if __name__ == '__main__':
    try:
        renewed = ensure(Path(sys.argv[1] if len(sys.argv) > 1 else '/srv/nix-clean/hermes'))
        print('T3 MCP credential ' + ('renewed' if renewed else 'current'))
    except (Refusal, OSError, KeyError, ValueError) as exc:
        print(f'T3 MCP credential renewal failed: {exc}', file=sys.stderr)
        sys.exit(1)
