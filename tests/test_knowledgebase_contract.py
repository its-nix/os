"""Static contract gates for inert candidates; not runtime/access acceptance."""
import json
import copy
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = "bd2060e3fe4ad12fc02fa903e7b0a64dfc16b5d3"
ENTRY = "https://github.com/its-nix/knowledgebase/blob/main/START-HERE.md"


class KnowledgebaseContractTests(unittest.TestCase):
    def text(self, path):
        return (ROOT / path).read_text()

    def test_canonical_discovery_and_on_demand_metadata(self):
        for name in ("nix-knowledge", "nix-document-design"):
            text = self.text(f"skills/{name}/SKILL.md")
            self.assertTrue(text.startswith(f"---\nname: {name}\n"))
            self.assertRegex(text, r'(?m)^description: "Use when .+"$')
            self.assertIn("license: MIT\n", text)
            for target in re.findall(r'\]\((references/[^)#]+)\)', text):
                self.assertTrue((ROOT / "skills" / name / target).is_file())
        self.assertIn(ENTRY, self.text("skills/nix-knowledge/SKILL.md"))

    def test_source_is_lifecycle_neutral_not_self_authorizing(self):
        for name in ("nix-knowledge", "nix-document-design"):
            text = self.text(f"skills/{name}/SKILL.md")
            for assertion in ("Inert first-party candidate", "not installed", "not deployed"):
                self.assertNotIn(assertion.lower(), text.lower())
            for term in ("Source availability grants no approval", "governed manifest", "authenticated approval receipt", "live inventory", "does not self-authorize"):
                self.assertIn(term, text)
        proposal = self.text("docs/KNOWLEDGEBASE-INTEGRATION.md")
        self.assertIn("inert", proposal.lower())
        self.assertIn("not deployed", proposal)

    def test_original_packages_have_exact_scoped_mit_grants(self):
        grant = """MIT License

Copyright (c) 2026 Nix

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
        for name in ("nix-knowledge", "nix-document-design"):
            scope = (
                f"\nScope: This license applies only to the original Nix-authored documentation\n"
                f"in skills/{name}/. It does not license the rest of this\n"
                "repository or any referenced third-party content.\n"
            )
            self.assertEqual(grant + scope, self.text(f"skills/{name}/LICENSE"))

    def test_phased_proposal_preserves_historical_authority_and_scope(self):
        paths = ["scripts/bootstrap.sh"]
        for directory in ("skills/approvals", "skills/proposals", "skills/pack-approvals", "skills/pack-proposals", "skills/host-native-approvals", "config"):
            paths.extend(subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE, "--", directory], cwd=ROOT, text=True).splitlines())
        for path in paths:
            before = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
            self.assertEqual(before, (ROOT / path).read_bytes(), path)
        manifest = json.loads(self.text("skills/packs.json"))
        before = json.loads(subprocess.check_output(["git", "show", f"{BASE}:skills/packs.json"], cwd=ROOT))
        for field in ("profiles", "packs", "proposed_pack_additions", "approved_pack_additions", "governance"):
            self.assertEqual(before[field], manifest[field], field)
        for profile, values in before["approved_additions"].items():
            self.assertEqual(values + (["nix-knowledge"] if profile == "lnx" else []), manifest["approved_additions"][profile])
        self.assertEqual(before["task_only"], [s for s in manifest["task_only"] if s not in {"nix-knowledge", "nix-document-design"}])
        self.assertEqual(before["excluded"], manifest["excluded"])
        proposal = json.loads(self.text("skills/activation-proposals/lnx-knowledge-v2.json"))
        expected_preservation = copy.deepcopy(before["host_native_satisfaction"])
        expected_preservation["lnx"] = proposal["preservation"]
        self.assertEqual(expected_preservation, manifest["host_native_satisfaction"])
        receipt = json.loads(self.text("skills/approvals/lnx-nix-knowledge-1182c51c.json"))
        self.assertEqual("skills/activation-approvals/lnx-knowledge-v2.json", receipt["activation_receipt"])

    def test_retrieval_and_contribution_boundaries(self):
        text = self.text("skills/nix-knowledge/references/contribution-contract.md")
        for term in ("AGENTS.md", "inbox/", "provisional", "disputed", "superseded", "archived", "review-by", "evidence", "Lead", "Kanban", "contradiction", "force-push"):
            self.assertIn(term, text)
        self.assertIn("not approval", text)

    def test_access_and_admission_limitations_explicit(self):
        text = self.text("docs/KNOWLEDGEBASE-INTEGRATION.md")
        for term in ("same UID", "session_search", "base permission", "free", "unprivileged", "all tools", "credentials", "namespace", "not deployed", "immutable catalog", "authenticated", "80/100", "writing-plans", "exit 2", "fresh Discord", "bootstrap"):
            self.assertIn(term, text)
        self.assertIn(ENTRY, text)
        self.assertNotIn("its-nix/knowledge-base", text)

    def test_document_design_portable_and_attributed(self):
        text = self.text("skills/nix-document-design/references/portable-markdown.md")
        for term in ("3ccff5338ea700537839b21900aa5358a0402c98", "MIT", "original", "Bases", "alt text", "Mermaid", "GitHub", "Obsidian", "provisional", "frontmatter", "fences"):
            self.assertIn(term, text)
        self.assertIn("not", text)

    def test_role_contracts_are_templates_not_permission_changes(self):
        for profile in ("lnx", "pulse", "algua", "meabeclick", "meabeclickux", "meabeclickeng", "meabeclickgrowth"):
            text = self.text(f"profiles/{profile}/ROLE.md")
            self.assertIn("Knowledgebase integration (proposal only)", text)
            self.assertIn("not deployed", text)
            self.assertIn("KNOWLEDGEBASE-INTEGRATION.md", text)
            original = subprocess.check_output(
                ["git", "show", f"HEAD:profiles/{profile}/ROLE.md"],
                cwd=ROOT, text=True,
            )
            section = "## Knowledgebase integration (proposal only)"
            self.assertIn(section, original)
            self.assertEqual(original[original.index(section):], text[text.index(section):])

    def test_candidate_payload_is_docs_only_and_portable_paths(self):
        for name in ("nix-knowledge", "nix-document-design"):
            files = list((ROOT / "skills" / name).rglob("*"))
            self.assertTrue(files)
            for path in files:
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertTrue(path.suffix == ".md" or path == ROOT / "skills" / name / "LICENSE")
                    text = path.read_text()
                    self.assertNotRegex(text, r"/" + r"home/[^\s/]+")
                    self.assertNotIn("skills.external_dirs:", text)


if __name__ == "__main__":
    unittest.main()
