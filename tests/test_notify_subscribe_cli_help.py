"""Read-only check of the documented native notifier CLI interface."""
import shutil
import subprocess
import unittest


class NotifySubscribeCliHelpTests(unittest.TestCase):
    def test_help_exposes_required_interface_flags(self):
        hermes = shutil.which("hermes")
        if hermes is None:
            self.skipTest("Hermes CLI unavailable; cannot inspect notify-subscribe help")
        result = subprocess.run(
            [hermes, "kanban", "notify-subscribe", "--help"],
            check=False, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        help_text = result.stdout + result.stderr
        for flag in ("--platform", "--chat-id", "--notifier-profile", "--delivery-mode"):
            with self.subTest(flag=flag):
                self.assertIn(flag, help_text)
        self.assertIn("notify+wake", help_text)


if __name__ == "__main__":
    unittest.main()
