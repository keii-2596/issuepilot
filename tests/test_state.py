import tempfile
import unittest
from pathlib import Path

from issuepilot.config import Config
from issuepilot.state import (
    advance_discovery_page,
    archive_run,
    candidate_archive_summary,
    discovery_page,
    load_candidate_archive,
    manifest_path,
    save_discovery_run,
)
from tests.helpers import issue


class StateTests(unittest.TestCase):
    def test_discovery_runs_build_a_local_deduplicated_candidate_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(
                github_token=None,
                codex_path="codex",
                codex_model=None,
                workspace_root=root / "state" / "workspaces",
                min_stars=5000,
                languages=["Python"],
                repo_limit=5,
                issues_per_repo=5,
                max_changed_files=20,
                max_diff_bytes=200000,
                allow_publish=False,
            )
            candidate = issue()
            filters = {"min_stars": 500, "limit": 12}
            scan = {"target_count": 12, "target_reached": False}

            save_discovery_run(config, [candidate], filters, scan)
            summary = save_discovery_run(config, [candidate], filters, scan)
            archive = load_candidate_archive(config)

            self.assertEqual(summary["total_candidates"], 1)
            self.assertEqual(summary["run_count"], 2)
            self.assertEqual(summary["latest_candidates"][0]["url"], candidate.html_url)
            self.assertEqual(archive["candidates"][0]["times_seen"], 2)
            self.assertEqual(candidate_archive_summary(config), summary)

    def test_discovery_page_advances_and_wraps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(
                github_token=None,
                codex_path="codex",
                codex_model=None,
                workspace_root=root / "state" / "workspaces",
                min_stars=5000,
                languages=["Python"],
                repo_limit=5,
                issues_per_repo=5,
                max_changed_files=20,
                max_diff_bytes=200000,
                allow_publish=False,
            )
            self.assertEqual(discovery_page(config), 1)
            self.assertEqual(advance_discovery_page(config, 9, 3), 12)
            self.assertEqual(advance_discovery_page(config, 9, 3, 10), 2)
            self.assertEqual(discovery_page(config), 2)

    def test_archive_moves_workspace_and_manifest_recoverably(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(
                github_token=None,
                codex_path="codex",
                codex_model=None,
                workspace_root=root / "state" / "workspaces",
                min_stars=5000,
                languages=["Python"],
                repo_limit=5,
                issues_per_repo=5,
                max_changed_files=20,
                max_diff_bytes=200000,
                allow_publish=False,
            )
            workspace = config.workspace_root / "octo--example--issue-42"
            workspace.mkdir(parents=True)
            (workspace / "change.txt").write_text("kept\n", encoding="utf-8")
            manifest = manifest_path(config, "octo/example", 42)
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            destination = archive_run(config, "octo/example", 42)
            self.assertFalse(workspace.exists())
            self.assertFalse(manifest.exists())
            self.assertEqual(
                (destination / "workspace" / "change.txt").read_text(encoding="utf-8"),
                "kept\n",
            )
            self.assertTrue((destination / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
