from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/reconcile-hermes-models.py"
MODELS = {
    "default": "gpt-6-luna",
    "pulse": "gpt-6-luna",
    "meabeclick": "gpt-6-luna",
    "meabeclickux": "gpt-6-luna",
    "meabeclickgrowth": "gpt-6-luna",
    "algua": "gpt-6.1-sol",
    "meabeclickeng": "gpt-6.1-sol",
}
CRON_NAME = "Weekly Hermes aggregate usage review"


def write_fake(path: Path) -> None:
    path.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os
        from pathlib import Path
        import sys

        args = sys.argv[1:]
        log = Path(os.environ["FAKE_LOG"])
        with log.open("a", encoding="utf-8") as out:
            out.write(json.dumps(args) + "\\n")
        if args[:1] != ["-p"] or len(args) < 4:
            raise SystemExit("unexpected command")
        profile, command = args[1], args[2:]
        state_path = Path(os.environ["FAKE_STATE"])
        state = json.loads(state_path.read_text(encoding="utf-8"))

        if command[:2] == ["config", "get"] and len(command) == 4 and command[3] == "--json":
            key = command[2]
            if f"{profile}:{key}" == os.environ.get("FAKE_GET_FAILURE"):
                print("injected read failure", file=sys.stderr)
                raise SystemExit(7)
            value = state[profile][key]
            if f"{profile}:{key}" == os.environ.get("FAKE_BAD_TYPE"):
                value = 42
            print(json.dumps(value))
        elif command[:2] == ["config", "set"] and len(command) == 4:
            key, value = command[2:]
            if f"{profile}:{key}" == os.environ.get("FAKE_SET_FAILURE"):
                print("injected setter failure", file=sys.stderr)
                raise SystemExit(8)
            if f"{profile}:{key}" != os.environ.get("FAKE_INEFFECTIVE_SET"):
                state[profile][key] = value
                state_path.write_text(json.dumps(state), encoding="utf-8")
        elif command == ["cron", "list", "--all"]:
            pass
        elif command[:2] == ["cron", "edit"]:
            identifier = command[2]
            store = Path(os.environ["HERMES_HOME"]) / "cron/jobs.json"
            jobs = json.loads(store.read_text(encoding="utf-8"))
            jobs = jobs["jobs"] if isinstance(jobs, dict) else jobs
            job = next(item for item in jobs if (item.get("id") or item.get("job_id")) == identifier)
            if not os.environ.get("FAKE_INEFFECTIVE_CRON"):
                job["provider"] = command[command.index("--provider") + 1]
                job["model"] = command[command.index("--model") + 1]
                store.write_text(json.dumps(jobs), encoding="utf-8")
        else:
            print(f"unexpected command: {args!r}", file=sys.stderr)
            raise SystemExit(64)
    """), encoding="utf-8")
    path.chmod(0o755)


class ModelRoutingTests(unittest.TestCase):
    def fixture(self, base: Path) -> tuple[dict[str, str], Path, Path, Path]:
        home = base / "home/.hermes"
        home.mkdir(parents=True)
        for profile in MODELS:
            if profile != "default":
                (home / "profiles" / profile).mkdir(parents=True)
        cron = home / "cron/jobs.json"
        cron.parent.mkdir()
        cron.write_text(json.dumps([{
            "id": "11ec4b2b3b88",
            "name": CRON_NAME,
            "provider": None,
            "model": None,
        }]), encoding="utf-8")
        state = base / "state.json"
        state.write_text(json.dumps({
            profile: {"model.provider": "old-provider", "model.default": "old-model"}
            for profile in MODELS
        }), encoding="utf-8")
        log = base / "calls.jsonl"
        fake = base / "hermes"
        write_fake(fake)
        env = os.environ.copy()
        env.update({
            "HOME": str(base / "home"),
            "HERMES_HOME": str(home),
            "HERMES_BIN": str(fake),
            "FAKE_STATE": str(state),
            "FAKE_LOG": str(log),
        })
        return env, state, log, cron

    def run_script(self, env: dict[str, str], *args: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(script), *args], cwd=script.parents[1], env=env,
            text=True, capture_output=True, check=False,
        )

    def test_preview_is_complete_and_does_not_invoke_hermes_or_touch_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            log = base / "calls.jsonl"
            env = os.environ.copy()
            env.update({
                "HOME": str(base / "home"), "HERMES_HOME": str(base / "home/.hermes"),
                "HERMES_BIN": str(base / "missing-hermes"), "FAKE_LOG": str(log),
            })
            result = self.run_script(env)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("PREVIEW", result.stdout)
            self.assertIn(CRON_NAME, result.stdout)
            for profile, model in MODELS.items():
                self.assertIn(f"hermes -p {profile} config set model.provider openai-codex", result.stdout)
                self.assertIn(f"hermes -p {profile} config set model.default {model}", result.stdout)
            self.assertFalse(log.exists())
            self.assertFalse((base / "home/.hermes").exists())

    def test_apply_twice_sets_and_verifies_every_profile_and_named_cron(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, state_path, log, cron = self.fixture(Path(tmp))
            batches = []
            for _attempt in range(2):
                if log.exists():
                    log.unlink()
                result = self.run_script(env, "--apply")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("all seven Hermes profiles", result.stdout)
                batches.append([json.loads(line) for line in log.read_text().splitlines()])
                state = json.loads(state_path.read_text())
                self.assertEqual(set(MODELS), set(state))
                for profile, model in MODELS.items():
                    self.assertEqual("openai-codex", state[profile]["model.provider"])
                    self.assertEqual(model, state[profile]["model.default"])
                job = json.loads(cron.read_text())[0]
                self.assertEqual(("openai-codex", "gpt-6-luna"), (job["provider"], job["model"]))
            for calls in batches:
                for profile, model in MODELS.items():
                    for key, value in (("model.provider", "openai-codex"), ("model.default", model)):
                        self.assertEqual(1, calls.count(["-p", profile, "config", "set", key, value]))
                        self.assertEqual(2, calls.count(["-p", profile, "config", "get", key, "--json"]))
                self.assertIn(["-p", "default", "cron", "list", "--all"], calls)
                self.assertIn([
                    "-p", "default", "cron", "edit", "11ec4b2b3b88",
                    "--provider", "openai-codex", "--model", "gpt-6-luna",
                ], calls)

    def test_missing_profile_refuses_before_any_hermes_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            env, _state, log, _cron = self.fixture(Path(tmp))
            shutil.rmtree(Path(env["HERMES_HOME"]) / "profiles/algua")
            result = self.run_script(env, "--apply")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("algua is absent", result.stderr)
            self.assertFalse(log.exists())

    def test_preflight_read_and_managed_job_failures_do_not_set_config(self):
        cases = (
            ("FAKE_GET_FAILURE", "pulse:model.default", "config readback failed"),
            ("FAKE_BAD_TYPE", "pulse:model.default", "unexpected type"),
        )
        for variable, value, message in cases:
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as tmp:
                env, _state, log, _cron = self.fixture(Path(tmp))
                env[variable] = value
                result = self.run_script(env, "--apply")
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertFalse(any(call[2:4] == ["config", "set"] for call in calls), calls)

        for cron_jobs, expected_count in (([], 0), ([
            {"id": "one", "name": CRON_NAME, "provider": None, "model": None},
            {"id": "two", "name": CRON_NAME, "provider": None, "model": None},
        ], 2)):
            with self.subTest(managed_cron_matches=expected_count), tempfile.TemporaryDirectory() as tmp:
                env, _state, log, cron = self.fixture(Path(tmp))
                cron.write_text(json.dumps(cron_jobs), encoding="utf-8")
                result = self.run_script(env, "--apply")
                self.assertNotEqual(0, result.returncode)
                self.assertIn(f"found {expected_count}", result.stderr)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertFalse(any(call[2:4] == ["config", "set"] for call in calls), calls)

    def test_rejected_and_ineffective_setters_and_cron_readback_fail_closed(self):
        cases = (
            ("FAKE_SET_FAILURE", "pulse:model.default", "config setter failed"),
            ("FAKE_INEFFECTIVE_SET", "pulse:model.default", "config readback mismatch"),
            ("FAKE_INEFFECTIVE_CRON", "1", "managed cron job readback"),
        )
        for variable, value, message in cases:
            with self.subTest(variable=variable), tempfile.TemporaryDirectory() as tmp:
                env, _state, _log, _cron = self.fixture(Path(tmp))
                env[variable] = value
                result = self.run_script(env, "--apply")
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertIn("may be partially applied", result.stderr)

    def test_script_rejects_unapproved_manifest_mapping_without_invoking_hermes(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repo"
            (copy / "scripts").mkdir(parents=True)
            shutil.copy2(SCRIPT, copy / "scripts/reconcile-hermes-models.py")
            manifest = json.loads((ROOT / "nix-os.yaml").read_text())
            manifest["hermes_model_routing"]["models"]["algua"] = "gpt-6-luna"
            (copy / "nix-os.yaml").write_text(json.dumps(manifest), encoding="utf-8")
            log = copy / "calls.jsonl"
            env = os.environ.copy()
            env.update({"HERMES_BIN": "/missing", "FAKE_LOG": str(log)})
            result = self.run_script(env, "--apply", script=copy / "scripts/reconcile-hermes-models.py")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("does not match the approved provider/profile mapping", result.stderr)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
