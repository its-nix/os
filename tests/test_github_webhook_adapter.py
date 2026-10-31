import json
import unittest
from pathlib import Path

from automation.monitoring.github_webhook import MAX_OUTPUT_BYTES, adapt_payload

ROOT = Path(__file__).resolve().parents[1]
REPO = "its-nix/os"
SHA = "a" * 40


def event_payload(event, action):
    base = {"action": action, "repository": {"full_name": REPO}, "secret": "hidden", "comment": "private"}
    if event == "check_run":
        base["check_run"] = {"id": 17, "status": "completed", "conclusion": "success", "head_sha": SHA,
                             "pull_requests": [{"number": 49}], "output": {"text": "private output"}}
    elif event == "pull_request_review":
        base["review"] = {"id": 18, "state": "approved", "body": "private review", "commit_id": SHA}
        base["pull_request"] = {"number": 49, "head": {"sha": SHA}}
    else:
        base["pull_request"] = {"number": 49, "head": {"sha": SHA}, "body": "private PR"}
    return base


class GitHubWebhookAdapterTests(unittest.TestCase):
    def test_all_allowlisted_events_and_actions(self):
        cases = [("check_run", "completed")]
        cases += [("pull_request_review", x) for x in ("submitted", "dismissed")]
        cases += [("pull_request", x) for x in ("opened", "reopened", "synchronize", "closed")]
        for event, action in cases:
            with self.subTest(event=event, action=action):
                result = adapt_payload(event, event_payload(event, action), approved_repository=REPO)
                self.assertEqual(result["event"], event)
                self.assertEqual(result["action"], action)
                self.assertEqual(result["pr_number"], 49)
                self.assertEqual(result["head_sha"], SHA)

    def test_wrong_repo_action_and_unmapped_or_ambiguous_check_are_ignored(self):
        p = event_payload("pull_request", "opened")
        p["repository"]["full_name"] = "other/repo"
        self.assertIsNone(adapt_payload("pull_request", p, approved_repository=REPO))
        self.assertIsNone(adapt_payload("pull_request", event_payload("pull_request", "edited"), approved_repository=REPO))
        p = event_payload("check_run", "completed")
        p["check_run"]["pull_requests"] = []
        self.assertIsNone(adapt_payload("check_run", p, approved_repository=REPO))
        p["check_run"]["pull_requests"] = [{"number": 1}, {"number": 2}]
        self.assertIsNone(adapt_payload("check_run", p, approved_repository=REPO))

    def test_malformed_ids_sha_status_and_pr_rejected(self):
        mutations = [
            ("check_run", "completed", "check_run", "id", 0),
            ("check_run", "completed", "check_run", "head_sha", "bad"),
            ("check_run", "completed", "check_run", "status", "mystery"),
            ("pull_request_review", "submitted", "review", "id", -1),
            ("pull_request_review", "submitted", "review", "state", "mystery"),
            ("pull_request_review", "submitted", "review", "commit_id", "bad"),
        ]
        for event, action, obj, key, value in mutations:
            with self.subTest(field=key, value=value):
                p = event_payload(event, action)
                p[obj][key] = value
                with self.assertRaises(ValueError):
                    adapt_payload(event, p, approved_repository=REPO)
        p = event_payload("pull_request", "opened")
        p["pull_request"]["number"] = 0
        self.assertIsNone(adapt_payload("pull_request", p, approved_repository=REPO))

    def test_private_fields_stripped_output_bounded_deterministic_and_payload_cap(self):
        p = event_payload("pull_request_review", "submitted")
        result = adapt_payload("pull_request_review", p, approved_repository=REPO)
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.assertEqual(result, adapt_payload("pull_request_review", p, approved_repository=REPO))
        self.assertLessEqual(len(encoded.encode()), MAX_OUTPUT_BYTES)
        for forbidden in ("body", "comment", "secret", "output"):
            self.assertNotIn(forbidden, encoded)
        p["extra"] = "x" * 300_000
        with self.assertRaises(ValueError):
            adapt_payload("pull_request_review", p, approved_repository=REPO)

    def test_route_contract_is_preview_native_agent_mode_and_profile_bound(self):
        contract = json.loads((ROOT / "automation/monitoring/github-webhook-routes.json").read_text())
        manifest = json.loads((ROOT / "nix-os.yaml").read_text())
        self.assertEqual(contract["route_mode"], "agent")
        self.assertEqual(contract["activation"], "disabled")
        self.assertIn("nix-os.yaml discord_profile_routes", contract["profile_binding_source"])
        expected = {p["id"]: (p["lead"], next(r["name"] for r in manifest["discord_profile_routes"] if r["profile"] == p["lead"])) for p in manifest["projects"]}
        self.assertEqual({r["project"]: (r["lead"], r["route_name"]) for r in contract["routes"]}, expected)
        rendered = json.dumps(contract)
        self.assertEqual(set(contract["forbidden_modes"]), {"cron_job", "coalesce", "deliver_only"})
        self.assertNotIn("cron_job", contract)
        self.assertNotIn("coalesce", contract)
        self.assertNotIn("deliver_only", contract)
        self.assertIn("not exactly-once", contract["delivery_semantics"])


if __name__ == "__main__":
    unittest.main()
