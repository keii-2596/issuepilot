from issuepilot.models import Issue, Repository


def repository(stars=12000):
    return Repository(
        full_name="octo/example",
        stars=stars,
        default_branch="main",
        clone_url="https://github.com/octo/example.git",
        html_url="https://github.com/octo/example",
        language="Python",
        license_key="mit",
    )


def issue(body="Clear reproduction and expected behavior.", **overrides):
    values = {
        "repo": repository(),
        "number": 42,
        "title": "Handle empty input",
        "body": body,
        "html_url": "https://github.com/octo/example/issues/42",
        "labels": ["good first issue", "bug"],
        "comments": 1,
        "state": "open",
        "updated_at": "2026-08-20T00:00:00Z",
    }
    values.update(overrides)
    return Issue(**values)
