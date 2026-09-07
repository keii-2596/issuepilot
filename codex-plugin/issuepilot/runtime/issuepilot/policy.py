import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .github import run_git


FORBIDDEN_PREFIXES = (
    ".gitattributes",
    ".gitmodules",
    ".lfsconfig",
    ".github/workflows/",
    ".github/actions/",
    ".github/CODEOWNERS",
    ".github/FUNDING",
    "SECURITY.md",
)

SECRET_PATTERNS = (
    re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    re.compile(r"(?:github_pat_|ghp_|sk-)[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}"),
)


@dataclass(frozen=True)
class PolicyReport:
    allowed: bool
    changed_files: List[str]
    diff_bytes: int
    reasons: List[str]


def change_fingerprint(repository: Path) -> str:
    """Hash the exact tracked diff plus all untracked file contents."""
    digest = hashlib.sha256()
    tracked_diff = run_git(
        ["diff", "--no-ext-diff", "--binary", "HEAD"], repository
    ).stdout.encode("utf-8")
    digest.update(b"tracked\0")
    digest.update(tracked_diff)
    untracked = run_git(
        ["ls-files", "--others", "--exclude-standard"], repository
    ).stdout.splitlines()
    for name in sorted(untracked):
        candidate = repository / name
        digest.update(b"untracked\0")
        digest.update(name.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if candidate.is_symlink():
            digest.update(b"symlink\0")
            digest.update(candidate.readlink().as_posix().encode("utf-8"))
        elif candidate.is_file():
            digest.update(b"file\0")
            digest.update(candidate.read_bytes())
        else:
            digest.update(b"other\0")
    return digest.hexdigest()


def inspect_changes(repository: Path, max_files: int, max_bytes: int) -> PolicyReport:
    tracked = run_git(["diff", "--name-only", "HEAD"], repository).stdout.splitlines()
    untracked = run_git(
        ["ls-files", "--others", "--exclude-standard"], repository
    ).stdout.splitlines()
    changed_files = sorted(set(tracked + untracked))
    reasons: List[str] = []
    if not changed_files:
        reasons.append("Codex reported a fix but produced no changes")
    if len(changed_files) > max_files:
        reasons.append("Too many changed files: {} > {}".format(len(changed_files), max_files))
    untracked_bytes = 0
    for name in changed_files:
        candidate = repository / name
        if candidate.is_symlink():
            reasons.append("Changed symbolic link requires human review: {}".format(name))
        elif name in untracked and candidate.is_file():
            untracked_bytes += candidate.stat().st_size
        if name.startswith(FORBIDDEN_PREFIXES):
            reasons.append("Protected path changed: {}".format(name))
    if untracked_bytes > max_bytes:
        reasons.append(
            "Changed files are too large: {} > {} bytes".format(
                untracked_bytes, max_bytes
            )
        )
        return PolicyReport(False, changed_files, untracked_bytes, reasons)

    diff = run_git(["diff", "--no-ext-diff", "--binary"], repository).stdout
    exact_untracked_bytes = 0
    untracked_text = []
    for name in untracked:
        candidate = repository / name
        if candidate.is_file() and not candidate.is_symlink():
            data = candidate.read_bytes()
            exact_untracked_bytes += len(data)
            untracked_text.append(data.decode("utf-8", errors="replace"))
    diff_bytes = len(diff.encode("utf-8")) + exact_untracked_bytes
    if diff_bytes > max_bytes:
        reasons.append("Diff is too large: {} > {} bytes".format(diff_bytes, max_bytes))
    added_lines = "\n".join(
        line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    added_lines += "\n" + "\n".join(untracked_text)
    for pattern in SECRET_PATTERNS:
        if pattern.search(added_lines):
            reasons.append("Possible credential detected in added lines")
            break
    return PolicyReport(not reasons, changed_files, diff_bytes, reasons)
