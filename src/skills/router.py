"""Skill router: map a URL to a skill pack.

v1 ships one pack: repo-audit, for public GitHub repositories.
"""

from __future__ import annotations

from src.audit.url_normalize import UrlError, normalize


def classify(url: str) -> dict:
    """Return {kind, skill, reason} plus normalized fields when accepted."""
    try:
        repo = normalize(url)
    except UrlError as exc:
        return {
            "kind": "unsupported",
            "skill": None,
            "reason": str(exc),
            "accepted": False,
        }
    return {
        "kind": "github_repo",
        "skill": "repo-audit",
        "reason": f"Public GitHub repository {repo.owner}/{repo.repo} maps to repo-audit.",
        "accepted": True,
        "owner": repo.owner,
        "repo": repo.repo,
        "branch": repo.branch,
        "normalized_url": repo.html_url,
        "clone_url": repo.clone_url,
    }
