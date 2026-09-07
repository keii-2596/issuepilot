import http.client
import time
import unittest
import urllib.error
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from issuepilot.github import GitHubClient, GitHubError, GitHubRateLimitError
from tests.helpers import issue, repository


class RecordingClient(GitHubClient):
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return [
            {
                "number": 7,
                "title": "Small bug",
                "body": "Details",
                "html_url": "https://github.com/octo/example/issues/7",
                "labels": [{"name": "help wanted"}],
                "comments": 0,
                "locked": False,
                "assignee": None,
            }
        ]


class WriteRecordingClient(GitHubClient):
    def __init__(self):
        self.posts = []

    def post(self, path, payload):
        self.posts.append((path, payload))
        return {"html_url": "https://github.com/octo/example/pull/9"}


class IssueDetailClient(GitHubClient):
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/repos/octo/example":
            return {
                "full_name": "octo/example",
                "stargazers_count": 12000,
                "default_branch": "main",
                "clone_url": "https://github.com/octo/example.git",
                "html_url": "https://github.com/octo/example",
            }
        if path == "/repos/octo/example/issues/42":
            return {
                "number": 42,
                "title": "Handle empty input",
                "body": "Original issue body.",
                "html_url": "https://github.com/octo/example/issues/42",
                "labels": [],
                "comments": 101,
                "state": "open",
            }
        if path.endswith("/comments"):
            page = params["page"]
            if page == 1:
                return [
                    {
                        "user": {"login": "user-{}".format(index)},
                        "author_association": "CONTRIBUTOR",
                        "created_at": "2026-09-01T00:00:00Z",
                        "updated_at": "2026-09-01T00:00:00Z",
                        "html_url": "https://example.test/comment/{}".format(index),
                        "body": "Comment {}".format(index),
                    }
                    for index in range(100)
                ]
            return [
                {
                    "user": {"login": "last-user"},
                    "author_association": "MEMBER",
                    "created_at": "2026-09-02T00:00:00Z",
                    "updated_at": "2026-09-02T00:00:00Z",
                    "html_url": "https://example.test/comment/100",
                    "body": "Final comment",
                }
            ]
        raise AssertionError("unexpected request: {}".format(path))


class RepositorySearchClient(GitHubClient):
    def __init__(self):
        self.params = []

    def get(self, path, params=None):
        self.params.append(params)
        base = {
            "stargazers_count": 10000,
            "default_branch": "main",
            "clone_url": "https://github.com/octo/code.git",
            "html_url": "https://github.com/octo/code",
            "language": "Python",
            "archived": False,
            "fork": False,
        }
        blocked = dict(
            base,
            full_name="octo/list",
            topics=["awesome-list"],
            stargazers_count=500000,
            clone_url="https://github.com/octo/list.git",
            html_url="https://github.com/octo/list",
        )
        allowed = dict(base, full_name="octo/code", topics=["database"])
        return {"items": [blocked, allowed]}


class TimelineClient(GitHubClient):
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return [
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "closed",
                        "html_url": "https://github.com/octo/example/pull/8",
                        "pull_request": {},
                    }
                },
            },
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "html_url": "https://github.com/octo/example/issues/9",
                    }
                },
            },
            {
                "event": "cross-referenced",
                "source": {
                    "issue": {
                        "state": "open",
                        "html_url": "https://github.com/octo/example/pull/10",
                        "pull_request": {},
                    }
                },
            },
        ]


class SearchFallbackClient(GitHubClient):
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path.endswith("/timeline"):
            return []
        return {
            "items": [
                {
                    "title": "Refactor 42 helpers",
                    "body": "No issue reference here.",
                    "html_url": "https://github.com/octo/example/pull/8",
                    "state": "open",
                },
                {
                    "title": "Fixes #42",
                    "body": "The relationship is missing from the issue timeline.",
                    "html_url": "https://github.com/octo/example/pull/9",
                    "state": "open",
                },
            ]
        }


class RecentActivityClient(GitHubClient):
    def get(self, path, params=None):
        if path.endswith("/timeline"):
            return [
                {
                    "event": "referenced",
                    "created_at": "2999-01-01T00:00:00Z",
                    "commit_id": "abc123",
                    "commit_url": "https://github.com/octo/example/commit/abc123",
                },
                {
                    "event": "commented",
                    "created_at": "2999-01-02T00:00:00Z",
                    "body": "I'm working on this and will send a PR.",
                    "html_url": "https://github.com/octo/example/issues/42#issuecomment-1",
                    "actor": {"type": "User"},
                },
            ]
        return {"items": []}


class NaturalLanguageClaimClient(GitHubClient):
    def get(self, path, params=None):
        if path.endswith("/timeline"):
            return [
                {
                    "event": "commented",
                    "created_at": "2999-01-02T00:00:00Z",
                    "body": (
                        "Hi! I'd like to implement this frontend change. "
                        "May I be assigned?"
                    ),
                    "html_url": (
                        "https://github.com/octo/example/issues/42#issuecomment-2"
                    ),
                    "actor": {"type": "User"},
                }
            ]
        return {"items": []}


class AlreadyImplementedClient(GitHubClient):
    def get(self, path, params=None):
        if path.endswith("/timeline"):
            return [
                {
                    "event": "commented",
                    "created_at": "2025-01-02T00:00:00Z",
                    "body": (
                        "This already exists on master in `src/widget.py` and "
                        "covers exactly what was requested. It should be safe to close."
                    ),
                    "html_url": (
                        "https://github.com/octo/example/issues/42#issuecomment-3"
                    ),
                    "actor": {"type": "User"},
                }
            ]
        return {"items": []}


class FastLiveInspectionClient(GitHubClient):
    def __init__(self):
        super().__init__("token")
        self.posts = []

    def post(self, path, payload):
        self.posts.append((path, payload))
        return {
            "data": {
                "repository": {
                    "issue": {
                        "number": 42,
                        "title": "Handle empty input",
                        "body": "Clear reproduction and expected behavior.",
                        "url": "https://github.com/octo/example/issues/42",
                        "state": "OPEN",
                        "locked": False,
                        "createdAt": "2026-08-01T00:00:00Z",
                        "updatedAt": "2026-09-01T00:00:00Z",
                        "labels": {"nodes": [{"name": "good first issue"}]},
                        "assignees": {"nodes": []},
                        "comments": {
                            "totalCount": 1,
                            "nodes": [
                                {
                                    "author": {
                                        "__typename": "User",
                                        "login": "contributor",
                                    },
                                    "createdAt": "2999-01-01T00:00:00Z",
                                    "url": "https://github.com/octo/example/issues/42#issuecomment-4",
                                    "body": "I'd like to implement this. May I be assigned?",
                                }
                            ],
                        },
                        "timelineItems": {"nodes": []},
                    }
                },
                "search": {"nodes": []},
            }
        }


class GitHubSearchTests(unittest.TestCase):
    def test_get_issue_keeps_body_separate_and_fetches_every_comment_page(self):
        client = IssueDetailClient()
        candidate = client.get_issue("octo/example", 42)

        self.assertEqual(candidate.body, "Original issue body.")
        self.assertEqual(len(candidate.discussion), 101)
        self.assertEqual(candidate.discussion[-1]["author"], "last-user")
        self.assertEqual(candidate.discussion[-1]["body"], "Final comment")
        comment_calls = [call for call in client.calls if call[0].endswith("/comments")]
        self.assertEqual([call[1]["page"] for call in comment_calls], [1, 2])

    def test_search_rate_limit_waits_for_reset_before_using_last_slot(self):
        client = GitHubClient("token")
        url = "https://api.github.com/search/repositories"
        client._record_rate_limit(
            url,
            {
                "X-RateLimit-Resource": "search",
                "X-RateLimit-Remaining": "1",
                "X-RateLimit-Reset": "101",
            },
        )

        with patch.object(time, "time", side_effect=[100, 102]):
            with patch.object(time, "sleep") as sleep:
                client._wait_for_rate_slot(url)

        sleep.assert_called_once_with(2)

    def test_core_rate_limit_also_waits_for_reset(self):
        client = GitHubClient("token")
        url = "https://api.github.com/repos/octo/example/issues"
        client._record_rate_limit(
            url,
            {
                "x-ratelimit-resource": "core",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "3700",
            },
        )

        with patch.object(time, "time", side_effect=[100, 3701]):
            with patch.object(time, "sleep") as sleep:
                client._wait_for_rate_slot(url)

        sleep.assert_called_once_with(3601)

    def test_issue_search_uses_core_issue_api_and_deduplicates_labels(self):
        client = RecordingClient()
        issues = client.search_issues(repository(), 5)
        self.assertEqual(len(issues), 1)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(
            all(call[0] == "/repos/octo/example/issues" for call in client.calls)
        )
        self.assertEqual(
            {call[1]["labels"] for call in client.calls},
            {"good first issue", "help wanted"},
        )

    def test_newest_issue_mode_requests_creation_order(self):
        client = RecordingClient()
        client.search_issues(repository(), 5, sort_mode="newest_issues")
        self.assertTrue(all(call[1]["sort"] == "created" for call in client.calls))
        self.assertTrue(all(call[1]["direction"] == "desc" for call in client.calls))

    def test_owner_does_not_fork_own_repository(self):
        client = RecordingClient()
        self.assertEqual(client.fork(repository(), "octo"), "octo/example")
        self.assertEqual(client.calls, [])

    def test_pull_request_uses_exact_cross_repo_head_base_and_draft(self):
        client = WriteRecordingClient()
        url = client.create_pull_request(
            repository(),
            "tester:issuepilot/issue-7-fix",
            "Fix issue",
            "Body",
            draft=True,
        )
        self.assertEqual(url, "https://github.com/octo/example/pull/9")
        self.assertEqual(client.posts[0][0], "/repos/octo/example/pulls")
        self.assertEqual(
            client.posts[0][1],
            {
                "title": "Fix issue",
                "head": "tester:issuepilot/issue-7-fix",
                "base": "main",
                "body": "Body",
                "draft": True,
            },
        )

    def test_rejects_same_name_fork_from_different_parent(self):
        client = GitHubClient("token")
        with patch.object(
            client,
            "get",
            return_value={
                "fork": True,
                "parent": {"full_name": "someone-else/example"},
            },
        ):
            with self.assertRaisesRegex(GitHubError, "not a fork of octo/example"):
                client.fork(repository(), "tester")

    def test_repository_search_prioritizes_stars_activity_and_excludes_topics(self):
        client = RepositorySearchClient()
        repos = client.search_repositories(
            5000, ["Python"], 3, 180, ["awesome-list"], page=4
        )
        self.assertEqual([repo.full_name for repo in repos], ["octo/code"])
        params = client.params[0]
        self.assertEqual(params["sort"], "stars")
        self.assertIn("pushed:>=", params["q"])
        self.assertEqual(params["per_page"], 12)
        self.assertEqual(params["page"], 4)

    def test_repository_search_supports_star_range_and_ascending_order(self):
        client = RepositorySearchClient()
        client.search_repositories(
            5000,
            ["Python"],
            3,
            180,
            [],
            max_stars=20000,
            sort_mode="stars_asc",
        )
        params = client.params[0]
        self.assertIn("stars:5000..20000", params["q"])
        self.assertEqual(params["sort"], "stars")
        self.assertEqual(params["order"], "asc")

    def test_finds_open_cross_referenced_pr_and_ignores_closed_prs_and_issues(self):
        client = TimelineClient()
        linked = client.find_open_linked_pull_request(issue())
        self.assertEqual(linked, "https://github.com/octo/example/pull/10")
        self.assertEqual(
            client.calls,
            [("/repos/octo/example/issues/42/timeline", {"per_page": 100})],
        )

    def test_search_fallback_finds_open_pr_missing_from_timeline(self):
        client = SearchFallbackClient()
        linked = client.find_open_linked_pull_request(issue())
        self.assertEqual(linked, "https://github.com/octo/example/pull/9")
        self.assertEqual(client.calls[1][0], "/search/issues")
        self.assertEqual(
            client.calls[1][1]["q"], "repo:octo/example is:pr 42"
        )

    def test_activity_inspection_finds_recent_commit_and_claim(self):
        activity = RecentActivityClient().inspect_issue_activity(issue(), 45)
        self.assertEqual(
            activity.recent_commit_url,
            "https://github.com/octo/example/commit/abc123",
        )
        self.assertIn("issuecomment-1", activity.active_claim_url)
        self.assertIsNone(activity.open_pr_url)

    def test_activity_inspection_understands_natural_language_claim(self):
        activity = NaturalLanguageClaimClient().inspect_issue_activity(issue(), 45)
        self.assertIn("issuecomment-2", activity.active_claim_url)

    def test_activity_inspection_flags_issue_reported_as_already_implemented(self):
        activity = AlreadyImplementedClient().inspect_issue_activity(issue(), 45)
        self.assertIn("issuecomment-3", activity.likely_resolved_url)

    def test_fast_live_inspection_combines_state_comments_timeline_and_pr_search(self):
        client = FastLiveInspectionClient()
        refreshed, activity = client.inspect_live_issue(issue(), 45)
        self.assertEqual(refreshed.state, "open")
        self.assertEqual(refreshed.comments, 1)
        self.assertIn("issuecomment-4", activity.active_claim_url)
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(client.posts[0][0], "/graphql")

    def test_curl_fallback_keeps_token_out_of_arguments_and_cleans_header_file(self):
        client = GitHubClient("top-secret-token", max_retries=0)
        observed = {}

        def fake_curl(command, **kwargs):
            self.assertNotIn("top-secret-token", " ".join(command))
            header_value = command[command.index("--header") + 1]
            header_path = Path(header_value[1:])
            observed["header_path"] = header_path
            observed["headers"] = header_path.read_text(encoding="utf-8")
            return CompletedProcess(command, 0, '{"login":"tester"}\n200', "")

        with patch(
            "issuepilot.github.urllib.request.urlopen",
            side_effect=http.client.RemoteDisconnected("TLS failure"),
        ):
            with patch("issuepilot.github.shutil.which", return_value="/usr/bin/curl"):
                with patch("issuepilot.github.subprocess.run", side_effect=fake_curl):
                    data = client.get("/user")
        self.assertEqual(data["login"], "tester")
        self.assertIn("Authorization: Bearer top-secret-token", observed["headers"])
        self.assertFalse(observed["header_path"].exists())

    def test_curl_classifies_anonymous_api_quota_as_rate_limit(self):
        client = GitHubClient(max_retries=0)

        with patch("issuepilot.github.shutil.which", return_value="/usr/bin/curl"):
            with patch(
                "issuepilot.github.subprocess.run",
                return_value=CompletedProcess(
                    ["curl"],
                    0,
                    '{"message":"API rate limit exceeded for this IP"}\n403',
                    "",
                ),
            ):
                with self.assertRaises(GitHubRateLimitError):
                    client._request_with_curl(
                        "GET",
                        "https://api.github.com/search/issues",
                        None,
                        {"Accept": "application/vnd.github+json"},
                    )


if __name__ == "__main__":
    unittest.main()
