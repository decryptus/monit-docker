"""Exercise release decisions against real Git histories without network writes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select-release.sh"


class ReleaseSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / "VERSION").write_text("0.0.51\n")
        (self.root / "RELEASE").write_text("0.0.51\n")
        self.git("add", ".")
        self.git("commit", "-qm", "release")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def select(self, ref="refs/heads/master", success=True):
        output = self.root / "outputs"
        result = subprocess.run(["bash", str(SCRIPT)], cwd=self.root,
                                env=dict(os.environ, GITHUB_REF=ref,
                                         GITHUB_OUTPUT=str(output)),
                                capture_output=True, text=True)
        if not success:
            self.assertNotEqual(result.returncode, 0)
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_missing_tag_is_selected(self):
        self.assertEqual(self.select(), dict(tag="v0.0.51", create="true", publish="true"))
        self.assertEqual(self.git("tag"), "")  # Selection itself never writes a tag.

    def test_same_commit_can_retry(self):
        self.git("tag", "v0.0.51")
        self.assertEqual(self.select()["publish"], "true")

    def test_annotated_tag_can_retry(self):
        self.git("tag", "-a", "v0.0.51", "-m", "release")
        self.assertEqual(self.select()["create"], "false")

    def test_ordinary_commit_does_not_republish(self):
        self.git("tag", "v0.0.51")
        self.git("commit", "--allow-empty", "-qm", "docs")
        self.assertEqual(self.select()["publish"], "false")

    def test_unrelated_tag_fails(self):
        original = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-qm", "other release")
        self.git("tag", "v0.0.51")
        self.git("checkout", "--detach", original)
        self.select(success=False)

    def test_manual_tag_publishes_without_creation(self):
        self.assertEqual(self.select("refs/tags/v0.0.51"),
                         dict(tag="v0.0.51", create="false", publish="true"))

    def test_wrong_manual_tag_fails(self):
        self.select("refs/tags/v0.0.52", success=False)

    def test_other_branch_does_not_publish(self):
        self.assertEqual(self.select("refs/heads/feature")["publish"], "false")

    def test_mismatched_release_fails(self):
        (self.root / "RELEASE").write_text("0.0.52\n")
        self.select(success=False)

    def test_invalid_version_fails(self):
        (self.root / "VERSION").write_text("not-a-version\n")
        self.select(success=False)


if __name__ == "__main__":
    unittest.main()
