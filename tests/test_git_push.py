import subprocess
import tempfile
import unittest
from pathlib import Path

from issuepilot.github import push_with_token


def git(cwd, *args):
    return subprocess.run(
        ["git"] + list(args), cwd=str(cwd), text=True, capture_output=True, check=True
    )


class GitPushTests(unittest.TestCase):
    def test_pushes_branch_without_running_pre_push_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            git(source, "init", "-q")
            git(source, "checkout", "-qb", "main")
            git(source, "config", "user.name", "Test")
            git(source, "config", "user.email", "test@example.com")
            (source / "file.txt").write_text("one\n", encoding="utf-8")
            git(source, "add", "--", "file.txt")
            git(source, "commit", "-qm", "initial")
            bare = root / "fork.git"
            git(root, "clone", "-q", "--bare", str(source), str(bare))
            git(source, "remote", "add", "origin", str(bare))
            (source / "file.txt").write_text("two\n", encoding="utf-8")
            git(source, "add", "--", "file.txt")
            git(source, "commit", "-qm", "change")
            marker = root / "hook-ran"
            hook = source / ".git" / "hooks" / "pre-push"
            hook.write_text(
                "#!/bin/sh\nprintf hook > '{}'\n".format(marker), encoding="utf-8"
            )
            hook.chmod(0o700)
            push_with_token(source, "origin", "main", "fake-token")
            self.assertFalse(marker.exists())
            local_head = git(source, "rev-parse", "HEAD").stdout.strip()
            remote_head = git(bare, "rev-parse", "refs/heads/main").stdout.strip()
            self.assertEqual(local_head, remote_head)


if __name__ == "__main__":
    unittest.main()
