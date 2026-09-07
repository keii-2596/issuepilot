from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Repository:
    full_name: str
    stars: int
    default_branch: str
    clone_url: str
    html_url: str
    language: Optional[str] = None
    archived: bool = False
    fork: bool = False
    license_key: Optional[str] = None
    description: str = ""
    topics: List[str] = field(default_factory=list)
    pushed_at: str = ""

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Repository":
        license_data = data.get("license") or {}
        return cls(
            full_name=data["full_name"],
            stars=int(data.get("stargazers_count", 0)),
            default_branch=data.get("default_branch", "main"),
            clone_url=data["clone_url"],
            html_url=data["html_url"],
            language=data.get("language"),
            archived=bool(data.get("archived")),
            fork=bool(data.get("fork")),
            license_key=license_data.get("key"),
            description=data.get("description") or "",
            topics=[str(value).lower() for value in data.get("topics", [])],
            pushed_at=data.get("pushed_at", ""),
        )


@dataclass(frozen=True)
class Issue:
    repo: Repository
    number: int
    title: str
    body: str
    html_url: str
    labels: List[str] = field(default_factory=list)
    comments: int = 0
    assignee: Optional[str] = None
    locked: bool = False
    state: str = "open"
    created_at: str = ""
    updated_at: str = ""
    score: float = 0.0
    fit: str = "review"
    fit_reasons: List[str] = field(default_factory=list)
    risk_reasons: List[str] = field(default_factory=list)
    longevity_reason: str = ""
    age_days: Optional[int] = None
    updated_days: Optional[int] = None
    verification: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    discussion: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_api(cls, repo: Repository, data: Dict[str, Any]) -> "Issue":
        assignee = data.get("assignee") or {}
        labels = [item.get("name", "") for item in data.get("labels", [])]
        return cls(
            repo=repo,
            number=int(data["number"]),
            title=data.get("title", ""),
            body=data.get("body") or "",
            html_url=data["html_url"],
            labels=labels,
            comments=int(data.get("comments", 0)),
            assignee=assignee.get("login"),
            locked=bool(data.get("locked")),
            state=data.get("state", "open"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass(frozen=True)
class AgentResult:
    status: str
    summary: str
    tests: List[str]
    pr_title: str
    pr_body: str
    risk: str
    workspace_report_zh: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentResult":
        return cls(
            status=str(data["status"]),
            summary=str(data["summary"]),
            tests=[str(value) for value in data.get("tests", [])],
            pr_title=str(data.get("pr_title", "")),
            pr_body=str(data.get("pr_body", "")),
            risk=str(data.get("risk", "unknown")),
            workspace_report_zh=str(data.get("workspace_report_zh", "")),
        )


@dataclass(frozen=True)
class RunOutcome:
    issue: Issue
    status: str
    message: str
    workspace: Optional[Path] = None
    pr_url: Optional[str] = None


@dataclass(frozen=True)
class IssueActivity:
    """Signals used to avoid racing another contributor."""

    open_pr_url: Optional[str] = None
    recent_commit_url: Optional[str] = None
    active_claim_url: Optional[str] = None
    likely_resolved_url: Optional[str] = None
    checks: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
