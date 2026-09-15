"""Normalize and validate public GitHub repository URLs.

Accepted:
  https://github.com/{owner}/{repo}
  https://github.com/{owner}/{repo}.git
  https://github.com/{owner}/{repo}/tree/{branch}   (branch may contain slashes)

Normalization: strip trailing slash / query / fragment, lowercase host,
www.github.com → github.com, http → https.

Rejected with a precise one-line reason: SSH, gist, issues, PRs, blob,
gitlab, bitbucket, raw, releases, wiki, tree-less extras, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, unquote

from src.host.config import get_settings

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class UrlError(ValueError):
    """One-line, user-facing rejection reason."""


@dataclass(frozen=True)
class NormalizedRepo:
    url: str
    owner: str
    repo: str
    branch: str | None
    clone_url: str
    html_url: str

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "owner": self.owner,
            "repo": self.repo,
            "branch": self.branch,
            "clone_url": self.clone_url,
            "html_url": self.html_url,
        }


def _reject(reason: str) -> None:
    raise UrlError(reason)


def normalize(raw: str) -> NormalizedRepo:
    """Parse, normalize, and accept or reject a repository URL."""
    settings = get_settings()
    if raw is None:
        _reject("URL is required.")
    text = str(raw).strip()
    if not text:
        _reject("URL is required.")
    if len(text) > settings.max_url_length:
        _reject(f"URL exceeds {settings.max_url_length} characters.")

    lowered = text.lower()
    if lowered.startswith("git@"):
        _reject("SSH URLs are not accepted; use https://github.com/{owner}/{repo}.")
    if lowered.startswith("ssh://"):
        _reject("SSH URLs are not accepted; use https://github.com/{owner}/{repo}.")
    if lowered.startswith("git://"):
        _reject("git:// URLs are not accepted; use https://github.com/{owner}/{repo}.")

    # Allow a missing scheme only if it looks like github.com/...
    if "://" not in text:
        if lowered.startswith("github.com/") or lowered.startswith("www.github.com/"):
            text = "https://" + text
        else:
            _reject("URL must be an https://github.com/{owner}/{repo} address.")

    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme == "http":
        scheme = "https"
    if scheme != "https":
        _reject("Only https:// GitHub repository URLs are accepted.")

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "gist.github.com":
        _reject("Gist URLs are not accepted; provide a public repository URL.")
    if host in {"gitlab.com", "www.gitlab.com"}:
        _reject("Only GitHub is supported; GitLab URLs are rejected.")
    if host in {"bitbucket.org", "www.bitbucket.org"}:
        _reject("Only GitHub is supported; Bitbucket URLs are rejected.")
    if host in {"raw.githubusercontent.com", "objects.githubusercontent.com"}:
        _reject("Raw content URLs are not accepted; provide a repository URL.")
    if host.endswith(".github.io"):
        _reject("GitHub Pages URLs are not accepted; provide a repository URL.")
    if host != "github.com":
        _reject("Only github.com repository URLs are accepted.")

    path = unquote(parsed.path or "")
    path = path.split("#", 1)[0]
    path = path.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        _reject("URL must include both owner and repository.")

    owner, repo = parts[0], parts[1]
    if owner.lower() in {"orgs", "users", "settings", "marketplace", "explore", "topics", "notifications", "new"}:
        _reject("URL must include both owner and repository.")
    if owner.lower() == "gist":
        _reject("Gist URLs are not accepted; provide a public repository URL.")

    extra = parts[2:]
    branch: str | None = None
    if extra:
        head = extra[0].lower()
        if head in {"issues", "issue"}:
            _reject("Issue URLs are not accepted; provide a repository URL.")
        if head in {"pull", "pulls"}:
            _reject("Pull request URLs are not accepted; provide a repository URL.")
        if head == "blob":
            _reject("File blob URLs are not accepted; provide a repository URL.")
        if head == "raw":
            _reject("Raw file URLs are not accepted; provide a repository URL.")
        if head in {"commit", "commits"}:
            _reject("Commit URLs are not accepted; provide a repository URL.")
        if head in {"releases", "release", "tags", "tag"}:
            _reject("Release/tag URLs are not accepted; provide a repository URL.")
        if head in {"wiki", "pulse", "graphs", "network", "security", "actions", "projects", "discussions"}:
            _reject(f"github.com/{owner}/{repo}/{head} is not a repository URL.")
        if head == "tree":
            if len(extra) < 2:
                _reject("Tree URL is missing a branch name.")
            branch = "/".join(extra[1:])
            if not branch:
                _reject("Tree URL is missing a branch name.")
        else:
            _reject(
                f"Unsupported GitHub path /{'/'.join(extra)}; "
                "use https://github.com/{owner}/{repo} or /tree/{branch}."
            )

    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not repo:
        _reject("URL must include both owner and repository.")
    if not _OWNER_RE.match(owner):
        _reject("GitHub owner name is invalid.")
    if not _REPO_RE.match(repo) or repo in {".", ".."}:
        _reject("GitHub repository name is invalid.")

    html = f"https://github.com/{owner}/{repo}"
    clone = f"https://github.com/{owner}/{repo}.git"
    return NormalizedRepo(
        url=html if not branch else f"{html}/tree/{branch}",
        owner=owner,
        repo=repo,
        branch=branch,
        clone_url=clone,
        html_url=html,
    )


def normalize_or_reason(raw: str) -> tuple[NormalizedRepo | None, str | None]:
    try:
        return normalize(raw), None
    except UrlError as exc:
        return None, str(exc)
