import tempfile
import unittest
from pathlib import Path

from issuepilot.config import Config
from issuepilot.models import Issue, Repository
from issuepilot.web import (
    _cancel_discovery_scan,
    _candidate_dict,
    _extract_device_code,
    _github_auth_command,
    _register_discovery_scan,
    _release_discovery_scan,
    _request_integer,
    _request_languages,
    _request_sort_mode,
    dashboard_status,
)


def config_for(root: Path) -> Config:
    return Config(
        github_token=None,
        codex_path=None,
        codex_model=None,
        workspace_root=root / "workspaces",
        min_stars=5000,
        languages=["Python"],
        repo_limit=5,
        issues_per_repo=2,
        max_changed_files=20,
        max_diff_bytes=200000,
        allow_publish=False,
    )


class DashboardTests(unittest.TestCase):
    def test_status_is_read_only_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status = dashboard_status(config_for(Path(directory)))
        self.assertEqual(status["github_mode"], "anonymous-read-only")
        self.assertFalse(status["publish_ready"])
        self.assertEqual(status["prepared"], [])
        self.assertEqual(status["filters"]["max_repo_idle_days"], 180)
        self.assertEqual(status["filters"]["max_issue_idle_days"], 365)
        self.assertEqual(status["filters"]["max_issue_age_days"], 180)
        self.assertEqual(status["filters"]["max_stars"], 1000000)
        self.assertEqual(status["filters"]["discovery_sort"], "recommended")
        self.assertEqual(status["filters"]["active_work_days"], 45)
        self.assertEqual(status["filters"]["discovery_scan_pages"], 250)
        self.assertEqual(status["discovery_next_page"], 1)
        self.assertEqual(status["candidate_archive"]["total_candidates"], 0)

    def test_discovery_filter_validation(self) -> None:
        self.assertEqual(
            _request_integer(
                {"min_stars": "12000"}, "min_stars", 5000, 1, 1000000
            ),
            12000,
        )
        with self.assertRaises(ValueError):
            _request_integer({"repo_limit": 26}, "repo_limit", 20, 1, 25)
        self.assertEqual(
            _request_languages(["Python", " TypeScript ", "Python"]),
            ["Python", "TypeScript"],
        )
        with self.assertRaises(ValueError):
            _request_languages([])
        self.assertEqual(
            _request_sort_mode("stars_asc", "recommended"), "stars_asc"
        )
        with self.assertRaises(ValueError):
            _request_sort_mode("random", "recommended")

    def test_github_login_is_visible_and_copies_device_code(self) -> None:
        command = _github_auth_command("/safe/gh")
        self.assertEqual(command[0], "/safe/gh")
        self.assertIn("--web", command)
        self.assertIn("--clipboard", command)
        self.assertEqual(
            _extract_device_code("First copy your one-time code: ab12-cd34"),
            "AB12-CD34",
        )
        self.assertIsNone(_extract_device_code("No device code yet"))

    def test_candidate_payload_contains_only_display_fields(self) -> None:
        repo = Repository(
            full_name="owner/project",
            stars=12000,
            default_branch="main",
            clone_url="https://github.com/owner/project.git",
            html_url="https://github.com/owner/project",
            language="Python",
        )
        issue = Issue(
            repo=repo,
            number=42,
            title="Fix a narrow bug",
            body="Reproduction details",
            html_url="https://github.com/owner/project/issues/42",
            labels=["bug"],
            score=88.5,
        )
        payload = _candidate_dict(issue)
        self.assertEqual(payload["repository"], "owner/project")
        self.assertEqual(payload["score"], 88.5)
        self.assertEqual(payload["fit"], "review")
        self.assertIn("verification", payload)
        self.assertNotIn("clone_url", payload)

    def test_discovery_scan_metadata_is_safe_for_display(self) -> None:
        from issuepilot.web import _scan_summary

        summary = _scan_summary(
            {
                "start_page": 4,
                "last_page": 6,
                "pages_scanned": 3,
                "repositories_scanned": 47,
                "issues_collected": 61,
                "issues_filtered_by_age": 23,
                "issues_verified": 18,
                "target_count": 20,
                "target_reached": False,
                "search_exhausted": True,
            },
            next_page=7,
        )
        self.assertEqual(summary["pages"], [4, 5, 6])
        self.assertEqual(summary["repositories_scanned"], 47)
        self.assertEqual(summary["issues_filtered_by_age"], 23)
        self.assertEqual(summary["target_count"], 20)
        self.assertFalse(summary["target_reached"])
        self.assertTrue(summary["search_exhausted"])
        self.assertFalse(summary["cancelled"])
        self.assertEqual(summary["star_windows_scanned"], 1)
        self.assertEqual(summary["next_page"], 7)

    def test_discovery_scan_can_be_cancelled_by_id(self) -> None:
        scan_id = "test-scan-1234"
        event = _register_discovery_scan(scan_id)
        try:
            self.assertFalse(event.is_set())
            self.assertTrue(_cancel_discovery_scan(scan_id))
            self.assertTrue(event.is_set())
        finally:
            _release_discovery_scan(scan_id)
        self.assertFalse(_cancel_discovery_scan(scan_id))


if __name__ == "__main__":
    unittest.main()
