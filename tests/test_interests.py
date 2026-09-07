import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from issuepilot.config import Config
from issuepilot.discovery import discover
from issuepilot.github import GitHubClient
from issuepilot.interests import match_repository, normalize_interests
from tests.helpers import issue, repository
from tests.test_discovery import ActivityDiscoveryClient


class InterestTests(unittest.TestCase):
    def test_ai_match_has_evidence_and_avoids_substrings(self):
        self.assertIsNone(match_repository(replace(repository(), description="Retail email tools"), {"domains": ["ai"]}))
        result = match_repository(replace(repository(), topics=["large-language-model"]), {"domains": ["ai"]})
        self.assertIn("Topic含 large language model", result[0])
        self.assertTrue(match_repository(replace(repository(), description="本地大模型应用"), {"domains": ["ai"]}))

    def test_or_matching_and_exclusions_win(self):
        repo = replace(repository(), description="LLM trading assistant")
        self.assertIsNone(match_repository(repo, {"domains": ["ai"], "excluded_keywords": ["trading"]}))
        self.assertTrue(match_repository(repo, {"domains": ["frontend"], "keywords": ["llm"]}))
        self.assertEqual(match_repository(repository(), {}), [])
        self.assertIsNone(match_repository(repository(), {"domains": ["ai"]}))

    def test_validation(self):
        self.assertEqual(normalize_interests({"keywords": "RAG， inference\nRAG"})["keywords"], ["rag", "inference"])
        for data in ({"domains": ["unknown"]}, {"keywords": [False]}, {"domains": "ai,unknown"}, {"keywords": ["x"] * 21}, []):
            with self.assertRaises(ValueError):
                normalize_interests(data)

    def test_saved_profile_loads_and_empty_profile_disables_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "interests.json"
            with patch.dict("os.environ", {"ISSUEPILOT_WORKSPACE": directory + "/workspaces"}), patch("issuepilot.config._github_token", return_value=None):
                profile.write_text(json.dumps({"domains": ["ai"]}))
                self.assertEqual(Config.load().interests["domains"], ["ai"])
                profile.write_text("{}")
                self.assertEqual(Config.load().interests["domains"], [])

    def test_discovery_skips_unmatched_repo_before_reading_issues_and_continues(self):
        class Client(ActivityDiscoveryClient):
            def __init__(self):
                super().__init__([])
                self.issue_reads = []

            def search_repositories(self, *args, **kwargs):
                page = kwargs["page"]
                return [replace(repository(), full_name="octo/repo{}".format(page), topics=["llm"] if page == 2 else [])]

            def search_issues(self, repo, *args, **kwargs):
                self.issue_reads.append(repo.full_name)
                return [issue(repo=repo)]

        client = Client()
        found = discover(client, 5000, ["Python"], 5, 5, 1, max_scan_pages=2, interests={"domains": ["ai"]})
        self.assertEqual(client.issue_reads, ["octo/repo2"])
        self.assertEqual(len(found), 1)
        self.assertIn("AI / 大模型", found[0].fit_reasons[0])

    def test_github_filters_before_limit_and_preserves_raw_star_boundary(self):
        class Client(GitHubClient):
            def get(self, *args):
                return {"total_count": 2, "items": [
                    {"full_name": "o/retail", "clone_url": "https://example.com/a.git", "html_url": "https://example.com/a", "stargazers_count": 9000},
                    {"full_name": "o/model", "clone_url": "https://example.com/b.git", "html_url": "https://example.com/b", "stargazers_count": 8000, "topics": ["llm"]},
                ]}
        client = Client(None)
        found = client.search_repositories(5000, ["Python"], 1, interests={"domains": ["ai"]})
        self.assertEqual([repo.full_name for repo in found], ["o/model"])
        self.assertEqual(client.repository_search_star_values, [9000, 8000])

    def test_domain_match_does_not_bypass_active_work(self):
        from issuepilot.models import IssueActivity
        candidate = issue(repo=replace(repository(), topics=["llm"]))
        client = ActivityDiscoveryClient([candidate], {42: IssueActivity(open_pr_url="https://example.com/pr")})
        client.search_repositories = lambda *args, **kwargs: [candidate.repo]
        self.assertEqual(discover(client, 5000, ["Python"], 5, 5, 1, max_scan_pages=1, interests={"domains": ["ai"]}), [])

    def test_preferences_endpoint_persists_and_rejects_invalid_input(self):
        from issuepilot.web import DashboardHandler
        from tests.test_web import config_for
        with tempfile.TemporaryDirectory() as directory:
            config = config_for(Path(directory))
            handler = DashboardHandler.__new__(DashboardHandler)
            handler.path = "/api/interests"
            handler._origin_is_local = lambda: True
            replies = []
            handler._json = lambda payload, status=200: replies.append((payload, status))
            handler._read_json = lambda: {"domains": ["ai"], "keywords": "inference，RAG"}
            with patch("issuepilot.web.Config.load", return_value=config), patch("issuepilot.web.GitHubClient") as github:
                handler.do_POST()
                github.assert_not_called()
                self.assertEqual(replies[-1][1], 200)
                profile = Path(directory) / "interests.json"
                self.assertEqual(json.loads(profile.read_text())["keywords"], ["inference", "rag"])
                handler._read_json = lambda: {"domains": ["invalid"]}
                handler.do_POST()
                self.assertEqual(replies[-1][1], 400)
                self.assertEqual(json.loads(profile.read_text())["domains"], ["ai"])
