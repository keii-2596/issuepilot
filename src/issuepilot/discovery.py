import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .github import GitHubClient, GitHubError, GitHubRateLimitError
from .models import Issue
from .interests import match_repository, normalize_interests


def _days_since(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def _timestamp(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _sort_candidates(candidates: List[Issue], sort_mode: str) -> List[Issue]:
    if sort_mode == "stars_desc":
        key = lambda item: (item.repo.stars, item.score)
        reverse = True
    elif sort_mode == "stars_asc":
        key = lambda item: (item.repo.stars, -item.score)
        reverse = False
    elif sort_mode == "newest_issues":
        key = lambda item: (_timestamp(item.created_at), item.score)
        reverse = True
    elif sort_mode == "recently_updated":
        key = lambda item: (_timestamp(item.updated_at), item.score)
        reverse = True
    else:
        key = lambda item: item.score
        reverse = True
    return sorted(candidates, key=key, reverse=reverse)


def explain_issue(issue: Issue) -> Dict[str, object]:
    """Explain ranking and age with transparent, non-AI heuristics."""
    labels = {label.lower() for label in issue.labels}
    age_days = _days_since(issue.created_at)
    updated_days = _days_since(issue.updated_at)
    fit_reasons: List[str] = []
    risk_reasons: List[str] = []

    if "good first issue" in labels:
        fit_reasons.append("维护者标记为 good first issue")
    elif "help wanted" in labels:
        fit_reasons.append("维护者明确标记为 help wanted")
    if "bug" in labels:
        fit_reasons.append("Bug 通常比开放式功能需求更容易验证")
    if len(issue.body.strip()) >= 240:
        fit_reasons.append("描述包含较多上下文")
    if issue.comments <= 3:
        fit_reasons.append("讨论较少，冲突和共识成本相对低")

    title = issue.title.lower()
    if any(label in labels for label in ("feature request", "enhancement")):
        risk_reasons.append("功能需求可能需要维护者做产品决策")
    if len(issue.body.strip()) < 120:
        risk_reasons.append("描述较短，复现条件可能不足")
    if issue.comments >= 8:
        risk_reasons.append("讨论较多，可能存在尚未解决的方案分歧")
    if any(term in title for term in ("tracking", "umbrella", "epic", "rfc", "refactor")):
        risk_reasons.append("标题显示范围可能偏大")

    if age_days is None:
        longevity_reason = "GitHub 未提供创建时间，无法判断积压原因"
    elif age_days < 90:
        longevity_reason = "这是相对较新的 Issue，尚不能视为长期积压"
    elif issue.comments >= 8:
        longevity_reason = "存在较长讨论，长期未解决更可能是方案或边界尚未形成共识"
    elif updated_days is not None and updated_days <= 30:
        longevity_reason = "Issue 虽然创建较早，但近期仍有活动，可能刚被重新关注"
    elif len(issue.body.strip()) < 120:
        longevity_reason = "长期安静且描述较少，可能因为信息不足或优先级较低"
    elif any(label in labels for label in ("feature request", "enhancement")):
        longevity_reason = "更像产品演进需求，长期未解决可能源于优先级或设计决策"
    else:
        longevity_reason = "讨论较少但长期未处理，可能是维护资源或优先级问题；仍需阅读原讨论确认"

    fit = "recommended"
    if len(risk_reasons) >= 2 or issue.comments >= 10:
        fit = "caution"
    elif risk_reasons:
        fit = "review"
    return {
        "fit": fit,
        "fit_reasons": fit_reasons[:3],
        "risk_reasons": risk_reasons[:3],
        "longevity_reason": longevity_reason,
        "age_days": age_days,
        "updated_days": updated_days,
    }


def score_issue(issue: Issue) -> float:
    """Favor popular projects and specific, recent, low-discussion issues."""
    stars = min(5.0, math.log10(max(issue.repo.stars, 1))) * 14.0
    description = min(len(issue.body), 3000) / 3000.0 * 16.0
    labels = {label.lower() for label in issue.labels}
    label_score = 12.0 if "good first issue" in labels else 7.0
    if "feature request" in labels or "enhancement" in labels:
        label_score -= 8.0
    title = issue.title.lower()
    broad_terms = ("tracking", "umbrella", "epic", "rfc", "refactor", "clean-up", "cleanup")
    scope_penalty = 15.0 if any(term in title for term in broad_terms) else 0.0
    translation_terms = ("translation", "translate", "localization", "localisation")
    translation_penalty = 25.0 if (
        any(term in title for term in translation_terms)
        or any("translat" in label.lower() for label in issue.labels)
    ) else 0.0
    discussion_penalty = min(issue.comments, 12) * 1.5
    freshness = 0.0
    if issue.updated_at:
        updated = datetime.fromisoformat(issue.updated_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - updated).days
        freshness = max(0.0, 12.0 - age_days / 30.0)
    return round(
        stars
        + description
        + label_score
        + freshness
        - discussion_penalty
        - scope_penalty
        - translation_penalty,
        2,
    )


def discover(
    client: GitHubClient,
    min_stars: int,
    languages: List[str],
    repo_limit: int,
    issues_per_repo: int,
    total_limit: int,
    max_repo_idle_days: int = 180,
    excluded_topics: Optional[List[str]] = None,
    max_issue_idle_days: int = 365,
    issue_labels: Optional[List[str]] = None,
    active_work_days: int = 45,
    start_page: int = 1,
    max_scan_pages: int = 250,
    concurrency: int = 6,
    metrics: Optional[Dict[str, object]] = None,
    max_stars: Optional[int] = None,
    max_issue_age_days: int = 180,
    sort_mode: str = "recommended",
    should_stop: Optional[Callable[[], bool]] = None,
    interests: Optional[dict] = None,
) -> List[Issue]:
    """Find verified candidates, expanding and rotating repository pages.

    Network-heavy repository scans and candidate verification run with bounded
    concurrency.  A scan advances to later repository pages only when the
    current page cannot fill the requested candidate count.
    """
    interests = normalize_interests(interests or {})
    worker_count = max(1, min(int(concurrency), 8))
    language_count = max(1, len(languages))
    desired_per_language = max(1, math.ceil(repo_limit / language_count))
    repositories_per_page = min(100, desired_per_language * 4)
    search_page_limit = max(1, math.ceil(1000 / repositories_per_page))
    page_count = max(1, int(max_scan_pages))
    normalized_start_page = (
        (max(1, int(start_page)) - 1) % search_page_limit
    ) + 1
    scan_metrics: Dict[str, object] = {
        "start_page": normalized_start_page,
        "last_page": normalized_start_page,
        "pages_scanned": 0,
        "repositories_scanned": 0,
        "issues_collected": 0,
        "issues_filtered_by_age": 0,
        "issues_verified": 0,
        "target_count": total_limit,
        "target_reached": False,
        "search_exhausted": False,
        "scan_limit_reached": False,
        "cancelled": False,
        "search_page_limit": search_page_limit,
        "star_windows_scanned": 1,
    }
    errors: List[Tuple[str, GitHubError]] = []
    verification_errors: List[Tuple[str, GitHubError]] = []
    eligible: List[Issue] = []
    seen_repositories = set()
    seen_issues = set()

    def search_repository(repo):
        try:
            labels = issue_labels or ["good first issue", "help wanted"]
            try:
                candidates = client.search_issues(
                    repo, issues_per_repo, labels, sort_mode=sort_mode
                )
            except TypeError as exc:
                # Keep compatibility with lightweight third-party/test clients
                # that implemented the original two-argument protocol.
                if "unexpected keyword argument 'sort_mode'" in str(exc):
                    try:
                        candidates = client.search_issues(
                            repo, issues_per_repo, labels
                        )
                    except TypeError as labels_exc:
                        if "positional" not in str(labels_exc):
                            raise
                        candidates = client.search_issues(repo, issues_per_repo)
                elif "positional" not in str(exc):
                    raise
                else:
                    candidates = client.search_issues(repo, issues_per_repo)
        except GitHubError as exc:
            return repo, [], exc
        return repo, candidates, None

    def verify_issue(candidate: Issue):
        try:
            if getattr(client, "token", None) and hasattr(
                client, "inspect_live_issue"
            ):
                refreshed, activity = client.inspect_live_issue(
                    candidate, active_work_days
                )
            else:
                refreshed = client.refresh_issue(candidate)
                if (
                    refreshed.state != "open"
                    or refreshed.locked
                    or refreshed.assignee
                ):
                    return None, None
                updated_days = _days_since(refreshed.updated_at)
                if (
                    updated_days is not None
                    and updated_days > max_issue_idle_days
                ):
                    return None, None
                if hasattr(client, "inspect_issue_activity"):
                    activity = client.inspect_issue_activity(
                        refreshed, active_work_days
                    )
                else:
                    from .models import IssueActivity

                    linked_pr = client.find_open_linked_pull_request(refreshed)
                    activity = IssueActivity(open_pr_url=linked_pr)
            if refreshed.state != "open" or refreshed.locked or refreshed.assignee:
                return None, None
            age_days = _days_since(refreshed.created_at)
            if age_days is not None and age_days > max_issue_age_days:
                return None, None
            updated_days = _days_since(refreshed.updated_at)
            if updated_days is not None and updated_days > max_issue_idle_days:
                return None, None
            checked = replace(refreshed, score=score_issue(refreshed))
            domain_reasons = match_repository(checked.repo, interests)
            if domain_reasons is None:
                return None, None
            if (
                activity.open_pr_url
                or activity.recent_commit_url
                or activity.active_claim_url
                or activity.likely_resolved_url
            ):
                return None, None
            explanation = explain_issue(checked)
            explanation["fit_reasons"] = domain_reasons + explanation["fit_reasons"]
            return replace(
                checked,
                **explanation,
                verification=activity.checks,
                warnings=activity.warnings,
            ), None
        except GitHubError as exc:
            return None, exc

    repository_window_exhausted = False
    range_min_stars = min_stars
    range_max_stars = max_stars
    current_page = normalized_start_page
    window_star_values: List[int] = []

    def advance_star_window() -> bool:
        nonlocal range_min_stars, range_max_stars, current_page
        nonlocal repository_window_exhausted, window_star_values
        if not window_star_values:
            return False
        if sort_mode == "stars_asc":
            boundary = max(window_star_values)
            if range_max_stars is not None and boundary >= range_max_stars:
                return False
            next_min = boundary + 1
            if next_min <= range_min_stars:
                return False
            range_min_stars = next_min
        elif sort_mode == "stars_desc":
            boundary = min(window_star_values)
            if boundary <= range_min_stars:
                return False
            range_max_stars = boundary - 1
        else:
            return False
        current_page = 1
        repository_window_exhausted = False
        window_star_values = []
        scan_metrics["star_windows_scanned"] = (
            int(scan_metrics["star_windows_scanned"]) + 1
        )
        return True

    for _scan_index in range(page_count):
        if should_stop and should_stop():
            scan_metrics["cancelled"] = True
            break
        page = current_page
        scan_metrics["last_page"] = page
        scan_metrics["pages_scanned"] = int(scan_metrics["pages_scanned"]) + 1
        try:
            try:
                repos = client.search_repositories(
                    range_min_stars,
                    languages,
                    repo_limit,
                    max_repo_idle_days,
                    excluded_topics,
                    page=page,
                    max_stars=range_max_stars,
                    sort_mode=sort_mode,
                    interests=interests,
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                try:
                    repos = client.search_repositories(
                        range_min_stars,
                        languages,
                        repo_limit,
                        max_repo_idle_days,
                        excluded_topics,
                        page=page,
                    )
                except TypeError as page_exc:
                    if "unexpected keyword argument 'page'" not in str(page_exc):
                        raise
                        repos = client.search_repositories(
                            range_min_stars,
                        languages,
                        repo_limit,
                        max_repo_idle_days,
                        excluded_topics,
                    )
        except GitHubError as exc:
            if isinstance(exc, GitHubRateLimitError):
                raise
            errors.append(("repository page {}".format(page), exc))
            current_page = (current_page % search_page_limit) + 1
            continue
        window_star_values.extend(getattr(client, "repository_search_star_values", [repo.stars for repo in repos]))
        repos = [
            repo
            for repo in repos
            if repo.full_name not in seen_repositories
            and match_repository(repo, interests) is not None
        ]
        seen_repositories.update(repo.full_name for repo in repos)
        scan_metrics["repositories_scanned"] = (
            int(scan_metrics["repositories_scanned"]) + len(repos)
        )
        repository_window_exhausted = bool(
            getattr(client, "repository_search_exhausted", False)
        )
        if not repos:
            if repository_window_exhausted:
                if advance_star_window():
                    continue
                break
            current_page = (current_page % search_page_limit) + 1
            continue

        issues: List[Issue] = []
        with ThreadPoolExecutor(max_workers=min(worker_count, len(repos))) as executor:
            for repo, candidates, error in executor.map(search_repository, repos):
                if error is not None:
                    if isinstance(error, GitHubRateLimitError):
                        raise error
                    errors.append((repo.full_name, error))
                    continue
                for candidate in candidates:
                    key = (candidate.repo.full_name, candidate.number)
                    if key not in seen_issues:
                        seen_issues.add(key)
                        issues.append(candidate)
        scan_metrics["issues_collected"] = (
            int(scan_metrics["issues_collected"]) + len(issues)
        )
        before_age_filter = len(issues)
        def age_allowed(candidate: Issue) -> bool:
            age_days = _days_since(candidate.created_at)
            return age_days is None or age_days <= max_issue_age_days

        issues = [candidate for candidate in issues if age_allowed(candidate)]
        scan_metrics["issues_filtered_by_age"] = (
            int(scan_metrics["issues_filtered_by_age"])
            + before_age_filter
            - len(issues)
        )
        issues = _sort_candidates(
            [replace(candidate, score=score_issue(candidate)) for candidate in issues],
            sort_mode,
        )

        batch_size = max(worker_count * 2, total_limit - len(eligible))
        for index in range(0, len(issues), batch_size):
            if should_stop and should_stop():
                scan_metrics["cancelled"] = True
                break
            batch = issues[index : index + batch_size]
            if not batch:
                break
            with ThreadPoolExecutor(
                max_workers=min(worker_count, len(batch))
            ) as executor:
                results = list(executor.map(verify_issue, batch))
            scan_metrics["issues_verified"] = (
                int(scan_metrics["issues_verified"]) + len(batch)
            )
            for source_issue, (candidate, error) in zip(batch, results):
                if error is not None:
                    if isinstance(error, GitHubRateLimitError):
                        raise error
                    verification_errors.append((source_issue.html_url, error))
                    continue
                if candidate is not None:
                    eligible.append(candidate)
                    if len(eligible) >= total_limit:
                        break
            if len(eligible) >= total_limit:
                break
        if len(eligible) >= total_limit or bool(scan_metrics["cancelled"]):
            break
        if repository_window_exhausted:
            if advance_star_window():
                continue
            break
        current_page = (current_page % search_page_limit) + 1

    scan_metrics["target_reached"] = len(eligible) >= total_limit
    scan_metrics["search_exhausted"] = (
        len(eligible) < total_limit
        and not bool(scan_metrics["cancelled"])
        and repository_window_exhausted
    )
    scan_metrics["scan_limit_reached"] = (
        len(eligible) < total_limit
        and not bool(scan_metrics["cancelled"])
        and not bool(scan_metrics["search_exhausted"])
        and int(scan_metrics["pages_scanned"]) >= page_count
    )
    if metrics is not None:
        metrics.clear()
        metrics.update(scan_metrics)
    if not seen_issues and errors:
        repo_name, first_error = next(
            (
                (repo_name, error)
                for repo_name, error in errors
                if isinstance(error, GitHubRateLimitError)
            ),
            errors[0],
        )
        error_type = (
            GitHubRateLimitError
            if isinstance(first_error, GitHubRateLimitError)
            else GitHubError
        )
        raise error_type(
            "No repository could be inspected: {}: {}".format(
                repo_name, first_error
            )
        )
    if not eligible and verification_errors:
        issue_url, first_error = next(
            (
                (issue_url, error)
                for issue_url, error in verification_errors
                if isinstance(error, GitHubRateLimitError)
            ),
            verification_errors[0],
        )
        error_type = (
            GitHubRateLimitError
            if isinstance(first_error, GitHubRateLimitError)
            else GitHubError
        )
        raise error_type(
            "No candidate could be verified as unclaimed: {}".format(
                "{}: {}".format(issue_url, first_error)
            )
        )
    return _sort_candidates(eligible, sort_mode)[:total_limit]
