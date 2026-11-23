from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hermes_token', ROOT / 'deploy/t3/hermes_token.py')
token = importlib.util.module_from_spec(spec)
spec.loader.exec_module(token)
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)
GOOD = 'a' * 40 + '.' + 'b' * 40


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        (self.home / '.env').write_text('OTHER=keep\nMCP_T3_API_KEY=old\n')
        self.mints = 0

    def tearDown(self):
        self.td.cleanup()

    def minter(self, value=GOOD, days=30):
        def mint():
            self.mints += 1
            return value, NOW + timedelta(days=days)
        return mint

    def test_mint_preserves_other_env_lines_owner_only(self):
        self.assertTrue(token.ensure(self.home, NOW, self.minter(), prober=lambda t: True))
        env = (self.home / '.env').read_text().splitlines()
        self.assertEqual(['OTHER=keep', 'MCP_T3_API_KEY=' + GOOD], env)
        self.assertEqual(0o600, (self.home / '.env').stat().st_mode & 0o777)
        self.assertNotIn('loaded_fingerprint', json.loads((self.home / 'nix/t3-mcp.json').read_text()))

    def test_restart_retried_until_confirmed_then_quiet(self):
        token.ensure(self.home, NOW, self.minter(), prober=lambda t: True)
        self.assertTrue(token.ensure(self.home, NOW, self.minter(), prober=lambda t: True))  # not loaded yet
        token.mark_loaded(self.home)
        self.assertFalse(token.ensure(self.home, NOW, self.minter(), prober=lambda t: True))
        self.assertEqual(1, self.mints)

    def test_revoked_or_expiring_credential_is_reminted(self):
        token.ensure(self.home, NOW, self.minter(), prober=lambda t: True)
        token.mark_loaded(self.home)
        self.assertTrue(token.ensure(self.home, NOW, self.minter(), prober=lambda t: False))
        self.assertTrue(token.ensure(self.home, NOW + timedelta(days=25), self.minter(), prober=lambda t: True))
        self.assertEqual(3, self.mints)

    def test_env_replaced_but_marker_stale_still_restarts(self):
        token.ensure(self.home, NOW, self.minter(), prober=lambda t: True)
        token.mark_loaded(self.home)
        # Crash window: a new credential reached .env, the sidecar still says the old one loaded.
        other = 'c' * 40 + '.' + 'd' * 40
        token.write_env(self.home / '.env', {'MCP_T3_API_KEY': other})
        self.assertTrue(token.ensure(self.home, NOW, self.minter(), prober=lambda t: True))

    def test_injected_newline_or_malformed_token_refused(self):
        with self.assertRaises(token.Refusal):
            token.ensure(self.home, NOW, self.minter(GOOD + '\nHERMES_EVIL=1'), prober=lambda t: True)
        self.assertEqual('OTHER=keep\nMCP_T3_API_KEY=old\n', (self.home / '.env').read_text())

    def test_symlinked_target_refused(self):
        (self.home / '.env').unlink()
        (self.home / '.env').symlink_to(self.home / 'elsewhere')
        with self.assertRaises(token.Refusal):
            token.write_env(self.home / '.env', {'MCP_T3_API_KEY': GOOD})
        self.assertFalse((self.home / 'elsewhere').exists())


if __name__ == '__main__':
    unittest.main()
