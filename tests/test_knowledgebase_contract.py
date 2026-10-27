"""Static contract gates for inert candidates; not runtime/access acceptance."""
import json
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

    def test_canonical_discovery_and_inert_metadata(self):
        for name in ("nix-knowledge", "nix-document-design"):
            text = self.text(f"skills/{name}/SKILL.md")
            self.assertTrue(text.startswith(f"---\nname: {name}\n"))
            self.assertRegex(text, r'(?m)^description: "Use when .+"$')
            self.assertIn("inert", text.lower())
            self.assertIn("not installed", text.lower())
            for target in re.findall(r'\]\((references/[^)#]+)\)', text):
                self.assertTrue((ROOT / "skills" / name / target).is_file())
        self.assertIn(ENTRY, self.text("skills/nix-knowledge/SKILL.md"))

    def test_governance_and_activation_inputs_unchanged(self):
        paths = ["skills/packs.json", "scripts/bootstrap.sh", "scripts/install-skills.py"]
        for directory in ("skills/approvals", "skills/proposals", "skills/pack-approvals", "skills/pack-proposals", "skills/host-native-approvals", "config"):
            paths.extend(str(p.relative_to(ROOT)) for p in (ROOT / directory).rglob("*") if p.is_file())
        for path in paths:
            before = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
            self.assertEqual(before, (ROOT / path).read_bytes(), path)
        manifest = json.loads(self.text("skills/packs.json"))
        self.assertNotIn("nix-knowledge", json.dumps(manifest))
        self.assertNotIn("nix-document-design", json.dumps(manifest))

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
                ["git", "show", f"{BASE}:profiles/{profile}/ROLE.md"],
                cwd=ROOT, text=True,
            )
            self.assertTrue(text.startswith(original.rstrip() + "\n\n"))

    def test_candidate_payload_is_docs_only_and_portable_paths(self):
        for name in ("nix-knowledge", "nix-document-design"):
            files = list((ROOT / "skills" / name).rglob("*"))
            self.assertTrue(files)
            for path in files:
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertEqual(path.suffix, ".md")
                    text = path.read_text()
                    self.assertNotRegex(text, r"/" + r"home/[^\s/]+")
                    self.assertNotIn("skills.external_dirs:", text)


if __name__ == "__main__":
    unittest.main()
