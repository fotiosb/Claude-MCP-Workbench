"""Unauthenticated GitHub resolve. 404 is reported as private-or-missing."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from src.audit.url_normalize import NormalizedRepo

log = logging.getLogger("mcp_workbench.github")

_UA = "mcp-workbench/1.0 (public-repo-audit; +https://github.com/fotiosb)"


class GithubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _rate_or_http_error(status: int, what: str) -> GithubError:
    if status in {403, 429}:
        return GithubError(
            "GitHub rate-limited the unauthenticated resolve. Try again shortly.",
            status=status,
        )
    return GithubError(f"GitHub returned HTTP {status} for {what}.", status=status)


async def github_resolve(repo: NormalizedRepo, client: httpx.AsyncClient | None = None) -> dict:
    """Resolve owner/repo (and optional branch/tag/SHA) without authentication."""
    own = None
    if client is None:
        own = httpx.AsyncClient(timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"})
        client = own
    try:
        url = f"https://api.github.com/repos/{repo.owner}/{repo.repo}"
        resp = await client.get(url)
        if resp.status_code == 404:
            raise GithubError("private or missing repo", status=404)
        if resp.status_code >= 400:
            raise _rate_or_http_error(resp.status_code, f"{repo.owner}/{repo.repo}")
        data = resp.json()
        default_branch = data.get("default_branch") or "main"
        branch = repo.branch or default_branch
        # Refs with slashes (e.g. release/1.2) must be percent-encoded or the
        # path is parsed as extra segments and 404s. Tags and commit SHAs also
        # work via the commits/{ref} endpoint.
        commit_url = (
            f"https://api.github.com/repos/{repo.owner}/{repo.repo}/commits/"
            f"{quote(branch, safe='')}"
        )
        cresp = await client.get(commit_url)
        if cresp.status_code == 404:
            raise GithubError(f"branch '{branch}' is private or missing", status=404)
        if cresp.status_code >= 400:
            raise _rate_or_http_error(cresp.status_code, f"commits/{branch}")
        body = cresp.json()
        sha = body.get("sha")
        # Prefer API-canonical owner/repo (GitHub login casing) for cache keys.
        owner = data.get("owner", {}).get("login") or repo.owner
        name = data.get("name") or repo.repo
        return {
            "owner": owner,
            "repo": name,
            "full_name": data.get("full_name") or f"{owner}/{name}",
            "default_branch": default_branch,
            "branch": branch,
            "sha": sha,
            "private": bool(data.get("private")),
            "size_kb": data.get("size"),
            "description": data.get("description") or "",
            "html_url": data.get("html_url") or repo.html_url,
            "language": data.get("language"),
            "archived": bool(data.get("archived")),
            "fork": bool(data.get("fork")),
            "license": (data.get("license") or {}).get("spdx_id"),
        }
    finally:
        if own is not None:
            await own.aclose()
