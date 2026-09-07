import unittest
import threading
import time
from dataclasses import replace

from issuepilot.discovery import _sort_candidates, discover, explain_issue, score_issue
from issuepilot.github import GitHubRateLimitError
from issuepilot.models import IssueActivity
from tests.helpers import issue, repository


class DiscoveryClient:
    def __init__(self, candidates, linked=None):
        self.candidates = candidates
        self.linked = linked or {}
        self.checked = []

    def search_repositories(self, *args, **kwargs):
        return [repository()]

    def search_issues(self, repo, limit):
        return self.candidates

    def find_open_linked_pull_request(self, candidate):
        self.checked.append(candidate.number)
        return self.linked.get(candidate.number)

    def refresh_issue(self, candidate):
        return candidate


class RateLimitedDiscoveryClient(DiscoveryClient):
    def search_issues(self, repo, limit):
        raise GitHubRateLimitError("GitHub API rate limit exceeded")


class RateLimitedVerificationClient(DiscoveryClient):
    def find_open_linked_pull_request(self, candidate):
        raise GitHubRateLimitError("GitHub API rate limit exceeded")


class StaleIssueStateClient(DiscoveryClient):
    def refresh_issue(self, candidate):
        if candidate.number == 1:
            return replace(candidate, state="closed")
        return candidate


class ActivityDiscoveryClient(DiscoveryClient):
    def __init__(self, candidates, activities=None):
        super().__init__(candidates)
        self.activities = activities or {}

    def inspect_issue_activity(self, candidate, recent_days):
        self.checked.append(candidate.number)
        return self.activities.get(
            candidate.number,
            IssueActivity(
                checks=[
                    "未发现关联的开放 PR",
                    "最近 {} 天未发现关联 Commit".format(recent_days),
                ]
            ),
        )


class PagingDiscoveryClient(ActivityDiscoveryClient):
    def __init__(self):
        super().__init__([])
        self.pages = []

    def search_repositories(self, *args, **kwargs):
        page = kwargs.get("page", 1)
        self.pages.append(page)
        return [replace(repository(), full_name="octo/page-{}".format(page))]

    def search_issues(self, repo, limit, labels=None):
        if repo.full_name.endswith("page-4"):
            return []
        return [
            issue(
                repo=repo,
                number=page_number(repo.full_name),
                html_url="https://github.com/{}/issues/{}".format(
                    repo.full_name, page_number(repo.full_name)
                ),
            )
        ]


class StarWindowDiscoveryClient(ActivityDiscoveryClient):
    repository_search_exhausted = True

    def __init__(self):
        super().__init__([])
        self.minimums = []

    def search_repositories(self, min_stars, *args, **kwargs):
        self.minimums.append(min_stars)
        stars = 550 if min_stars == 500 else 600
        return [
            replace(
                repository(stars=stars),
                full_name="octo/stars-{}".format(stars),
            )
        ]

    def search_issues(self, repo, limit, labels=None, sort_mode="recommended"):
        return [
            issue(
                repo=repo,
                number=repo.stars,
                html_url="https://github.com/{}/issues/{}".format(
                    repo.full_name, repo.stars
                ),
            )
        ]


def page_number(full_name):
    return int(full_name.rsplit("-", 1)[1])


class ConcurrentDiscoveryClient(ActivityDiscoveryClient):
    def __init__(self):
        super().__init__([])
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def search_repositories(self, *args, **kwargs):
        return [
            replace(repository(), full_name="octo/repo-{}".format(index))
            for index in range(6)
        ]

    def search_issues(self, repo, limit, labels=None):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.03)
        with self.lock:
            self.active -= 1
        number = int(repo.full_name.rsplit("-", 1)[1]) + 1
        return [
            issue(
                repo=repo,
                number=number,
                html_url="https://github.com/{}/issues/{}".format(
                    repo.full_name, number
                ),
            )
        ]


class FastVerificationDiscoveryClient(ActivityDiscoveryClient):
    token = "token"

    def __init__(self, candidates):
        super().__init__(candidates)
        self.fast_checked = []

    def refresh_issue(self, candidate):
        raise AssertionError("fast inspection should replace REST refresh")

    def inspect_live_issue(self, candidate, recent_days):
        self.fast_checked.append(candidate.number)
        return candidate, IssueActivity(checks=["单次请求已完成实时核验"])


class ScoreIssueTests(unittest.TestCase):
    def test_more_stars_score_higher(self):
        small = issue(repo=repository(stars=100))
        large = issue(repo=repository(stars=100000))
        self.assertGreater(score_issue(large), score_issue(small))

    def test_many_comments_reduce_score(self):
        quiet = issue(comments=0)
        crowded = issue(comments=10)
        self.assertGreater(score_issue(quiet), score_issue(crowded))

    def test_broad_refactor_and_feature_request_score_lower(self):
        focused = issue(title="Fix empty input", labels=["good first issue", "bug"])
        broad = issue(
            title="Tracking: large refactor cleanup",
            labels=["good first issue", "feature request"],
        )
        self.assertGreater(score_issue(focused), score_issue(broad))

    def test_translation_issue_scores_lower(self):
        focused = issue(title="Fix empty input", labels=["good first issue", "bug"])
        translation = issue(
            title="Greek translation", labels=["help wanted", "translation"]
        )
        self.assertGreater(score_issue(focused), score_issue(translation))

    def test_explanation_surfaces_long_discussion_as_consensus_risk(self):
        old = issue(
            created_at="2024-01-01T00:00:00Z",
            updated_at="2026-08-20T00:00:00Z",
            comments=12,
        )
        assessment = explain_issue(old)
        self.assertEqual(assessment["fit"], "caution")
        self.assertIn("方案或边界", assessment["longevity_reason"])

    def test_discovery_skips_ranked_issue_with_open_linked_pr_and_backfills(self):
        occupied = issue(number=1, title="Fix recent bug", comments=0)
        available = issue(number=2, title="Fix another bug", comments=2)
        client = DiscoveryClient(
            [occupied, available],
            {1: "https://github.com/octo/example/pull/99"},
        )
        found = discover(client, 5000, ["Python"], 1, 5, 1)
        self.assertEqual([candidate.number for candidate in found], [2])
        self.assertEqual(client.checked, [1, 2])

    def test_discovery_preserves_rate_limit_error_from_repository_scan(self):
        client = RateLimitedDiscoveryClient([])
        with self.assertRaises(GitHubRateLimitError):
            discover(client, 5000, ["Python"], 1, 5, 1)

    def test_discovery_preserves_rate_limit_error_from_pr_verification(self):
        client = RateLimitedVerificationClient([issue(number=1)])
        with self.assertRaises(GitHubRateLimitError):
            discover(client, 5000, ["Python"], 1, 5, 1)

    def test_discovery_refreshes_issue_state_and_backfills_closed_candidate(self):
        client = StaleIssueStateClient([issue(number=1), issue(number=2)])
        found = discover(client, 5000, ["Python"], 1, 5, 1)
        self.assertEqual([candidate.number for candidate in found], [2])
        self.assertEqual(client.checked, [2])

    def test_discovery_skips_recent_commit_and_returns_verification_evidence(self):
        active = issue(number=1)
        available = issue(number=2)
        client = ActivityDiscoveryClient(
            [active, available],
            {
                1: IssueActivity(
                    recent_commit_url="https://github.com/octo/example/commit/abc"
                )
            },
        )
        found = discover(client, 5000, ["Python"], 1, 5, 1)
        self.assertEqual([candidate.number for candidate in found], [2])
        self.assertIn("未发现关联的开放 PR", found[0].verification)

    def test_discovery_skips_issue_reported_as_already_implemented(self):
        obsolete = issue(number=1)
        available = issue(number=2)
        client = ActivityDiscoveryClient(
            [obsolete, available],
            {
                1: IssueActivity(
                    likely_resolved_url=(
                        "https://github.com/octo/example/issues/1#issuecomment-3"
                    )
                )
            },
        )
        found = discover(client, 5000, ["Python"], 1, 5, 1)
        self.assertEqual([candidate.number for candidate in found], [2])

    def test_discovery_filters_issue_that_has_not_updated_recently(self):
        stale = issue(number=1, updated_at="2020-01-01T00:00:00Z")
        fresh = issue(number=2)
        client = ActivityDiscoveryClient([stale, fresh])
        found = discover(
            client, 5000, ["Python"], 1, 5, 1, max_issue_idle_days=365
        )
        self.assertEqual([candidate.number for candidate in found], [2])

    def test_discovery_filters_issue_that_has_existed_too_long_before_verification(self):
        old = issue(number=1, created_at="2020-01-01T00:00:00Z")
        recent = issue(number=2, created_at="2999-01-01T00:00:00Z")
        client = ActivityDiscoveryClient([old, recent])
        metrics = {}
        found = discover(
            client,
            5000,
            ["Python"],
            1,
            5,
            1,
            max_issue_age_days=180,
            metrics=metrics,
        )
        self.assertEqual([candidate.number for candidate in found], [2])
        self.assertEqual(client.checked, [2])
        self.assertEqual(metrics["issues_filtered_by_age"], 1)

    def test_candidate_sort_modes_support_stars_and_issue_dates(self):
        low = replace(
            issue(number=1, repo=repository(stars=5000)),
            score=70,
            created_at="2026-07-01T00:00:00Z",
            updated_at="2026-08-01T00:00:00Z",
        )
        high = replace(
            issue(number=2, repo=repository(stars=50000)),
            score=60,
            created_at="2026-08-01T00:00:00Z",
            updated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(
            [item.number for item in _sort_candidates([low, high], "stars_asc")],
            [1, 2],
        )
        self.assertEqual(
            [item.number for item in _sort_candidates([low, high], "stars_desc")],
            [2, 1],
        )
        self.assertEqual(
            [item.number for item in _sort_candidates([low, high], "newest_issues")],
            [2, 1],
        )

    def test_discovery_expands_to_next_repository_page_when_first_is_empty(self):
        client = PagingDiscoveryClient()
        metrics = {}
        found = discover(
            client,
            5000,
            ["Python"],
            1,
            5,
            1,
            start_page=4,
            max_scan_pages=3,
            metrics=metrics,
        )
        self.assertEqual(client.pages, [4, 5])
        self.assertEqual(found[0].repo.full_name, "octo/page-5")
        self.assertEqual(metrics["pages_scanned"], 2)
        self.assertEqual(metrics["repositories_scanned"], 2)

    def test_discovery_keeps_scanning_beyond_three_pages_until_target_is_filled(self):
        client = PagingDiscoveryClient()
        metrics = {}
        found = discover(
            client,
            5000,
            ["Python"],
            1,
            5,
            4,
            start_page=1,
            max_scan_pages=10,
            metrics=metrics,
        )
        self.assertEqual(client.pages, [1, 2, 3, 4, 5])
        self.assertEqual(len(found), 4)
        self.assertTrue(metrics["target_reached"])
        self.assertFalse(metrics["search_exhausted"])

    def test_discovery_keeps_scanning_beyond_ten_pages_until_target_is_filled(self):
        client = PagingDiscoveryClient()
        metrics = {}
        found = discover(
            client,
            5000,
            ["Python"],
            1,
            5,
            11,
            start_page=1,
            metrics=metrics,
        )
        self.assertEqual(client.pages, list(range(1, 13)))
        self.assertEqual(len(found), 11)
        self.assertTrue(metrics["target_reached"])
        self.assertFalse(metrics["scan_limit_reached"])

    def test_stars_ascending_continues_into_the_next_thousand_result_window(self):
        client = StarWindowDiscoveryClient()
        metrics = {}
        found = discover(
            client,
            500,
            ["Python"],
            1,
            5,
            2,
            max_stars=1_000_000,
            sort_mode="stars_asc",
            metrics=metrics,
        )

        self.assertEqual(client.minimums, [500, 551])
        self.assertEqual(len(found), 2)
        self.assertTrue(metrics["target_reached"])
        self.assertEqual(metrics["star_windows_scanned"], 2)

    def test_discovery_reports_a_custom_scan_cap_without_claiming_exhaustion(self):
        client = PagingDiscoveryClient()
        metrics = {}
        found = discover(
            client,
            5000,
            ["Python"],
            1,
            5,
            20,
            start_page=1,
            max_scan_pages=10,
            metrics=metrics,
        )
        self.assertEqual(client.pages, list(range(1, 11)))
        self.assertEqual(len(found), 9)
        self.assertFalse(metrics["target_reached"])
        self.assertFalse(metrics["search_exhausted"])
        self.assertTrue(metrics["scan_limit_reached"])

    def test_discovery_can_be_stopped_between_repository_pages(self):
        client = PagingDiscoveryClient()
        metrics = {}
        found = discover(
            client,
            5000,
            ["Python"],
            1,
            5,
            20,
            start_page=1,
            metrics=metrics,
            should_stop=lambda: len(client.pages) >= 3,
        )
        self.assertEqual(client.pages, [1, 2, 3])
        self.assertEqual(len(found), 2)
        self.assertTrue(metrics["cancelled"])
        self.assertFalse(metrics["search_exhausted"])

    def test_repository_issue_collection_uses_bounded_concurrency(self):
        client = ConcurrentDiscoveryClient()
        found = discover(
            client,
            5000,
            ["Python"],
            6,
            5,
            1,
            max_scan_pages=1,
            concurrency=3,
        )
        self.assertEqual(len(found), 1)
        self.assertGreater(client.max_active, 1)
        self.assertLessEqual(client.max_active, 3)

    def test_authenticated_discovery_uses_single_request_live_inspection(self):
        candidate = issue(number=7)
        client = FastVerificationDiscoveryClient([candidate])
        found = discover(
            client,
            5000,
            ["Python"],
            1,
            5,
            1,
            max_scan_pages=1,
        )
        self.assertEqual([item.number for item in found], [7])
        self.assertEqual(client.fast_checked, [7])


if __name__ == "__main__":
    unittest.main()
