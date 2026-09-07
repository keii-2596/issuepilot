import subprocess
import tempfile
import unittest
from pathlib import Path

from issuepilot.policy import inspect_changes


def git(cwd, *args):
    return subprocess.run(
        ["git"] + list(args), cwd=str(cwd), text=True, capture_output=True, check=True
    )


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        git(self.repo, "add", "--", "app.py")
        git(self.repo, "commit", "-qm", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def test_allows_small_source_change(self):
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        report = inspect_changes(self.repo, 5, 10000)
        self.assertTrue(report.allowed)
        self.assertEqual(report.changed_files, ["app.py"])

    def test_checks_untracked_files_for_secrets(self):
        (self.repo / "new.py").write_text(
            'token = "github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ"\n', encoding="utf-8"
        )
        report = inspect_changes(self.repo, 5, 10000)
        self.assertFalse(report.allowed)
        self.assertIn("Possible credential detected in added lines", report.reasons)

    def test_blocks_workflow_changes(self):
        target = self.repo / ".github" / "workflows"
        target.mkdir(parents=True)
        (target / "ci.yml").write_text("name: changed\n", encoding="utf-8")
        report = inspect_changes(self.repo, 5, 10000)
        self.assertFalse(report.allowed)
        self.assertTrue(any("Protected path" in reason for reason in report.reasons))

    def test_blocks_git_behavior_files(self):
        (self.repo / ".gitattributes").write_text(
            "*.py filter=unsafe\n", encoding="utf-8"
        )
        report = inspect_changes(self.repo, 5, 10000)
        self.assertFalse(report.allowed)
        self.assertIn("Protected path changed: .gitattributes", report.reasons)

    def test_blocks_new_symbolic_link(self):
        (self.repo / "outside").symlink_to("/tmp/outside")
        report = inspect_changes(self.repo, 5, 10000)
        self.assertFalse(report.allowed)
        self.assertTrue(any("symbolic link" in reason for reason in report.reasons))

    def test_rejects_large_file_before_reading_diff(self):
        (self.repo / "large.bin").write_bytes(b"x" * 101)
        report = inspect_changes(self.repo, 5, 100)
        self.assertFalse(report.allowed)
        self.assertIn("Changed files are too large: 101 > 100 bytes", report.reasons)

    def test_allows_small_diff_inside_a_large_tracked_text_file(self):
        large = self.repo / "large.py"
        large.write_text("value = 1\n" + "# padding\n" * 1000, encoding="utf-8")
        git(self.repo, "add", "--", "large.py")
        git(self.repo, "commit", "-qm", "add large tracked file")
        large.write_text("value = 2\n" + "# padding\n" * 1000, encoding="utf-8")

        report = inspect_changes(self.repo, 5, 500)

        self.assertTrue(report.allowed)
        self.assertLess(report.diff_bytes, 500)


if __name__ == "__main__":
    unittest.main()
