"""Exercise harness verification against isolated copies, never the installed skills."""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.verify_harness import verify


class HarnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="telebotshop-harness-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        for directory in (".agents", "docs"):
            shutil.copytree(source / directory, self.root / directory)
        for name in ("AGENTS.md", ".gitattributes", ".gitignore"):
            shutil.copyfile(source / name, self.root / name)

    def test_installed_harness_is_valid(self):
        self.assertEqual(verify(self.root), [])

    def test_modified_upstream_skill_is_rejected(self):
        skill = self.root / ".agents/skills/spec-driven-development/SKILL.md"
        skill.write_bytes(skill.read_bytes() + b"\nChanged locally.\n")
        self.assertTrue(any("SHA-256 mismatch" in error for error in verify(self.root)))

    def test_missing_shared_reference_is_rejected(self):
        (self.root / ".agents/references/definition-of-done.md").unlink()
        self.assertTrue(any("Missing reference" in error for error in verify(self.root)))

    def test_unrequested_skill_is_rejected(self):
        router = self.root / ".agents/skills/using-agent-skills/SKILL.md"
        router.parent.mkdir()
        router.write_text("---\nname: using-agent-skills\ndescription: Unrequested.\n---\n")
        self.assertTrue(any("Skill set mismatch" in error for error in verify(self.root)))

    def test_reference_resolution_is_checked_beyond_hashes(self):
        relative = ".agents/skills/security-and-hardening/SKILL.md"
        skill = self.root / relative
        skill.write_bytes(skill.read_bytes() + b"\n`references/missing.md`\n")
        manifest = self.root / ".agents/upstream.json"
        provenance = json.loads(manifest.read_text(encoding="utf-8"))
        provenance["files"][relative] = hashlib.sha256(skill.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(provenance), encoding="utf-8")
        errors = verify(self.root)
        self.assertTrue(any("Missing reference" in error for error in errors))
        self.assertFalse(any("SHA-256 mismatch" in error for error in errors))

    def test_archive_checksum_is_checked_before_parsing(self):
        archive = self.root / "untrusted.tar.gz"
        archive.write_bytes(b"This is not the pinned upstream archive.")
        self.assertIn("Archive SHA-256 mismatch", verify(self.root, archive))


if __name__ == "__main__":
    unittest.main()
