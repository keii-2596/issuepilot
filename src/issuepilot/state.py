import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .config import Config
from .models import AgentResult, Issue


DISCOVERY_PAGE_LIMIT = 250
DISCOVERY_RUN_LIMIT = 100
DISCOVERY_CANDIDATE_LIMIT = 2000


def _key(full_name: str, number: int) -> str:
    return "{}--issue-{}".format(full_name.replace("/", "--"), number)


def _discovery_state_path(config: Config) -> Path:
    return config.workspace_root.parent / "discovery.json"


def _candidate_archive_path(config: Config) -> Path:
    return config.workspace_root.parent / "candidate-archive.json"


def _candidate_snapshot(issue: Issue) -> Dict[str, Any]:
    return {
        "score": issue.score,
        "stars": issue.repo.stars,
        "language": issue.repo.language,
        "repository": issue.repo.full_name,
        "repository_description": issue.repo.description,
        "repository_topics": issue.repo.topics,
        "issue": issue.number,
        "title": issue.title,
        "url": issue.html_url,
        "labels": issue.labels,
        "comments": issue.comments,
        "summary": (issue.body or "").strip()[:320],
        "fit": issue.fit,
        "fit_reasons": issue.fit_reasons,
        "risk_reasons": issue.risk_reasons,
        "longevity_reason": issue.longevity_reason,
        "age_days": issue.age_days,
        "updated_days": issue.updated_days,
        "verification": issue.verification,
        "warnings": issue.warnings,
    }


def load_candidate_archive(config: Config) -> Dict[str, Any]:
    path = _candidate_archive_path(config)
    empty = {"version": 1, "candidates": [], "runs": []}
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    candidates = data.get("candidates")
    runs = data.get("runs")
    if not isinstance(candidates, list) or not isinstance(runs, list):
        return empty
    return {"version": 1, "candidates": candidates, "runs": runs}


def candidate_archive_summary(config: Config) -> Dict[str, Any]:
    data = load_candidate_archive(config)
    latest = data["runs"][-1] if data["runs"] else None
    by_url = {
        item.get("url"): item
        for item in data["candidates"]
        if isinstance(item, dict) and item.get("url")
    }
    latest_candidates = []
    if isinstance(latest, dict):
        for url in latest.get("candidate_urls", []):
            record = by_url.get(url)
            if record and isinstance(record.get("candidate"), dict):
                latest_candidates.append(record["candidate"])
    return {
        "total_candidates": len(by_url),
        "run_count": len(data["runs"]),
        "latest_at": latest.get("created_at") if isinstance(latest, dict) else None,
        "latest_candidates": latest_candidates,
        "latest_filters": latest.get("filters") if isinstance(latest, dict) else None,
        "latest_scan": latest.get("scan") if isinstance(latest, dict) else None,
    }


def save_discovery_run(
    config: Config,
    issues: List[Issue],
    filters: Dict[str, Any],
    scan: Dict[str, Any],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    data = load_candidate_archive(config)
    records = {
        item.get("url"): item
        for item in data["candidates"]
        if isinstance(item, dict) and item.get("url")
    }
    candidate_urls = []
    for issue in issues:
        snapshot = _candidate_snapshot(issue)
        url = issue.html_url
        previous = records.get(url, {})
        records[url] = {
            "url": url,
            "first_seen_at": previous.get("first_seen_at", now),
            "last_seen_at": now,
            "times_seen": int(previous.get("times_seen", 0)) + 1,
            "candidate": snapshot,
        }
        candidate_urls.append(url)
    runs = list(data["runs"])
    runs.append(
        {
            "created_at": now,
            "candidate_urls": candidate_urls,
            "filters": filters,
            "scan": scan,
        }
    )
    ordered = sorted(
        records.values(), key=lambda item: item.get("last_seen_at", ""), reverse=True
    )[:DISCOVERY_CANDIDATE_LIMIT]
    payload = {
        "version": 1,
        "candidates": ordered,
        "runs": runs[-DISCOVERY_RUN_LIMIT:],
    }
    path = _candidate_archive_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return candidate_archive_summary(config)


def discovery_page(config: Config) -> int:
    """Return the next rotating GitHub repository-search page."""
    path = _discovery_state_path(config)
    if not path.is_file():
        return 1
    try:
        value = int(json.loads(path.read_text(encoding="utf-8"))["next_page"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return 1
    return value if 1 <= value <= DISCOVERY_PAGE_LIMIT else 1


def advance_discovery_page(
    config: Config,
    start_page: int,
    pages_scanned: int,
    page_limit: int = DISCOVERY_PAGE_LIMIT,
) -> int:
    """Persist the next page, wrapping within GitHub's searchable window."""
    scanned = max(1, int(pages_scanned))
    limit = max(1, min(int(page_limit), DISCOVERY_PAGE_LIMIT))
    next_page = ((max(1, start_page) - 1 + scanned) % limit) + 1
    path = _discovery_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"next_page": next_page}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return next_page


def manifest_path(config: Config, full_name: str, number: int) -> Path:
    return config.workspace_root.parent / "manifests" / ("{}.json".format(_key(full_name, number)))


def save_manifest(config: Config, data: Dict[str, Any]) -> Path:
    path = manifest_path(config, str(data["repository"]), int(data["issue_number"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def create_manifest(
    config: Config,
    issue: Issue,
    workspace: Path,
    branch: str,
    base_head: str,
    git_config_hash: str,
    fingerprint: str,
    result: AgentResult,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "version": 1,
        "status": "prepared",
        "repository": issue.repo.full_name,
        "issue_number": issue.number,
        "issue_url": issue.html_url,
        "workspace": str(workspace),
        "branch": branch,
        "base_head": base_head,
        "git_config_hash": git_config_hash,
        "change_fingerprint": fingerprint,
        "agent_result": asdict(result),
        "commit_sha": None,
        "pull_request": None,
    }
    save_manifest(config, data)
    return data


def load_manifest(config: Config, full_name: str, number: int) -> Dict[str, Any]:
    path = manifest_path(config, full_name, number)
    if not path.is_file():
        raise RuntimeError(
            "No prepared fix found. Run `issuepilot run --issue <URL>` first."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Prepared-fix manifest is unreadable: {}".format(path)) from exc
    required = {
        "repository",
        "issue_number",
        "workspace",
        "branch",
        "base_head",
        "git_config_hash",
        "change_fingerprint",
        "agent_result",
    }
    missing = sorted(required.difference(data))
    if missing:
        raise RuntimeError("Prepared-fix manifest is missing: {}".format(", ".join(missing)))
    if data["repository"] != full_name or int(data["issue_number"]) != number:
        raise RuntimeError("Prepared-fix manifest does not match the requested issue")
    return data


def result_from_manifest(data: Dict[str, Any]) -> AgentResult:
    try:
        return AgentResult.from_dict(data["agent_result"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Prepared-fix manifest contains an invalid agent result") from exc


def list_manifests(config: Config) -> List[Dict[str, Any]]:
    directory = config.workspace_root.parent / "manifests"
    if not directory.is_dir():
        return []
    values = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values.append(
            {
                "status": data.get("status", "unknown"),
                "issue": data.get("issue_url"),
                "workspace": data.get("workspace"),
                "pull_request": data.get("pull_request"),
                "updated_at": data.get("updated_at"),
            }
        )
    return values


def archive_run(config: Config, full_name: str, number: int) -> Path:
    owner, name = full_name.split("/", 1)
    workspace = config.workspace_root / "{}--{}--issue-{}".format(owner, name, number)
    manifest = manifest_path(config, full_name, number)
    if not workspace.exists() and not manifest.exists():
        raise RuntimeError("No workspace or prepared fix exists for this issue")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = (
        config.workspace_root.parent
        / "archive"
        / "{}-{}".format(timestamp, _key(full_name, number))
    )
    destination.mkdir(parents=True, exist_ok=False)
    if workspace.exists():
        shutil.move(str(workspace), str(destination / "workspace"))
    if manifest.exists():
        shutil.move(str(manifest), str(destination / "manifest.json"))
    return destination
