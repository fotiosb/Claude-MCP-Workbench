"""High-signal credential scan for perusing a GitHub repo.

Skips dependency / vendor / cache trees. Drops noisy high_entropy and
doc-example generic_api_key_assign hits. Findings are redacted.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.audit.path_safe import is_probably_binary, resolve_inside
from src.host.config import get_settings

SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.staging",
    ".env.test",
    "credentials.json",
    "credentials.xml",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "service-account.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
}

# True secret-ish keystore suffixes only when path looks project-owned.
# Broad .pem alone is too noisy (cacert.pem / certifi); prefer SECRET_FILENAMES.
SECRET_SUFFIXES = {".p12", ".pfx", ".jks", ".keystore"}

SKIP_PATH_SEGMENTS = {
    "node_modules",
    ".venv",
    "venv",
    "site-packages",
    "__pycache__",
    ".git",
    "dist",
    "build",
    "vendor",
    "third_party",
    "third-party",
    "env/lib",
    "env/Lib",
    "Lib/site-packages",
    "lib/site-packages",
    "pip/_internal",
    "pip/_vendor",
    "certifi",
    ".tox",
    ".mypy_cache",
    "coverage",
    "htmlcov",
    "wheels",
    "egg-info",
}

SKIP_FILENAMES = {
    "cacert.pem",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Cargo.lock",
}

# High-signal credential patterns only (no high_entropy, no doc-noise generics in md).
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("stripe_key", re.compile(r"sk_live_[0-9A-Za-z]{16,}")),
    ("generic_api_key_assign", re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,500}['\"]"
    )),
]

HIGH_SIGNAL_KINDS = {
    "aws_access_key",
    "github_pat",
    "github_fine_grained",
    "slack_token",
    "private_key_block",
    "google_api_key",
    "stripe_key",
    "secret_filename",
    "generic_api_key_assign",
}

TEXT_EXT = {
    "", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp", ".swift",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".md", ".txt", ".sh", ".bash", ".zsh", ".ps1", ".xml", ".properties",
    ".gradle", ".tf", ".tfvars",
}

DOC_NAME_PREFIXES = ("readme",)
DOC_DIR_SEGMENTS = {"docs", "doc", "documentation"}


def redact(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return "[REDACTED]"
    return value[:2] + "…" + value[-2:] + " [REDACTED]"


def path_segments(rel: str) -> list[str]:
    return [p for p in rel.replace("\\", "/").split("/") if p]


def should_skip_path(rel: str) -> bool:
    """True if path is under dependency / vendor / cache noise."""
    lowered = rel.replace("\\", "/").lower()
    name = Path(lowered).name

    if name in {f.lower() for f in SKIP_FILENAMES}:
        return True
    if name.endswith(".min.js"):
        return True

    # Multi-segment skip markers (normalized lowercase).
    multi = {
        "env/lib",
        "env\\lib",
        "lib/site-packages",
        "lib\\site-packages",
        "pip/_internal",
        "pip/_vendor",
    }
    for marker in multi:
        if marker.replace("\\", "/") in lowered:
            return True

    segs = [s.lower() for s in path_segments(rel)]
    skip_single = {
        "node_modules",
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
        ".git",
        "dist",
        "build",
        "vendor",
        "third_party",
        "third-party",
        "certifi",
        ".tox",
        ".mypy_cache",
        "coverage",
        "htmlcov",
        "wheels",
    }
    for s in segs:
        if s in skip_single:
            return True
        if s.endswith(".egg-info") or s == "egg-info":
            return True
    return False


def _is_doc_path(rel: str) -> bool:
    name = Path(rel).name.lower()
    if name.endswith(".md"):
        return True
    if any(name.startswith(p) for p in DOC_NAME_PREFIXES):
        return True
    segs = [s.lower() for s in path_segments(rel)]
    if any(s in DOC_DIR_SEGMENTS for s in segs):
        return True
    return False


def _project_owned_keystore(rel: str) -> bool:
    """Allow keystore suffix hits only near repo root or under secrets/certs."""
    segs = path_segments(rel)
    if len(segs) <= 2:
        return True
    top = segs[0].lower()
    return top in {"secrets", "certs", "cert", "keys", "credentials", "config", ".secrets"}


def filter_secret_findings(secrets: list[dict] | None) -> list[dict]:
    """Post-filter for cache / _public_run (defense in depth)."""
    if not secrets:
        return []
    out: list[dict] = []
    for item in secrets:
        kind = item.get("kind") or ""
        path = item.get("path") or ""
        if kind == "high_entropy":
            continue
        if should_skip_path(path):
            continue
        if kind == "generic_api_key_assign" and _is_doc_path(path):
            continue
        if kind not in HIGH_SIGNAL_KINDS and kind != "secret_filename":
            # Drop unknown/noisy kinds from older caches.
            if kind.startswith("secret"):
                pass
            else:
                continue
        # Drop cacert / bare .pem filename hits from old caches.
        name = Path(path).name.lower()
        if name == "cacert.pem":
            continue
        if kind == "secret_filename" and name.endswith(".pem") and name not in {
            n.lower() for n in SECRET_FILENAMES
        }:
            if not _project_owned_keystore(path):
                continue
        out.append(item)
    return out


def scan_secrets(root: Path, tree_files: list[dict] | None = None) -> list[dict]:
    """Report high-signal credentials only. Values are redacted."""
    settings = get_settings()
    root = Path(root)
    findings: list[dict] = []
    paths: list[str] = []

    def _want(rel: str, name: str, ext: str) -> bool:
        if should_skip_path(rel):
            return False
        if name in SECRET_FILENAMES or ext in SECRET_SUFFIXES or ext in TEXT_EXT:
            return True
        # .env* prefix
        if name.startswith(".env"):
            return True
        return False

    if tree_files:
        for item in tree_files:
            if item.get("binary"):
                continue
            p = item["path"]
            name = Path(p).name
            ext = Path(p).suffix.lower()
            if _want(p, name, ext):
                paths.append(p)
    else:
        import os

        skip_dir_names = {
            "node_modules", ".venv", "venv", "site-packages", "__pycache__",
            ".git", "dist", "build", "vendor", "third_party", "third-party",
            "certifi", ".tox", ".mypy_cache", "coverage", "htmlcov", "wheels",
        }
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = str(Path(dirpath).relative_to(root)).replace("\\", "/")
            if rel_dir == ".":
                rel_dir = ""
            dirnames[:] = [
                d for d in dirnames
                if d not in skip_dir_names
                and not d.endswith(".egg-info")
                and not should_skip_path(f"{rel_dir}/{d}" if rel_dir else d)
            ]
            for name in filenames:
                rel = str(Path(dirpath, name).relative_to(root)).replace("\\", "/")
                ext = Path(name).suffix.lower()
                if _want(rel, name, ext):
                    paths.append(rel)

    for rel in paths[:4000]:
        if should_skip_path(rel):
            continue
        try:
            path = resolve_inside(root, rel)
        except Exception:
            continue
        if not path.is_file():
            continue
        name = path.name
        ext = path.suffix.lower()

        # Filename hits: SECRET_FILENAMES or project-owned keystore suffixes.
        filename_hit = False
        if name in SECRET_FILENAMES or name.startswith(".env"):
            filename_hit = True
        elif ext in SECRET_SUFFIXES and _project_owned_keystore(rel):
            filename_hit = True

        if filename_hit:
            findings.append(
                {
                    "kind": "secret_filename",
                    "severity": "medium",
                    "path": rel,
                    "detail": f"Filename {name} is commonly used for credentials. Contents not shown.",
                    "redacted": True,
                }
            )
            if ext in SECRET_SUFFIXES:
                continue

        if is_probably_binary(path):
            continue
        try:
            text = path.read_bytes()[: settings.max_read_bytes].decode("utf-8", errors="replace")
        except OSError:
            continue

        is_doc = _is_doc_path(rel)
        for kind, pat in PATTERNS:
            if kind == "generic_api_key_assign" and is_doc:
                continue
            for match in pat.finditer(text):
                findings.append(
                    {
                        "kind": kind,
                        "severity": "high" if kind != "generic_api_key_assign" else "medium",
                        "path": rel,
                        "detail": f"Pattern {kind} matched; value redacted.",
                        "sample": redact(match.group(0)),
                        "redacted": True,
                    }
                )
        # high_entropy intentionally dropped — too noisy for repo browsing.
        if len(findings) >= 200:
            break

    return filter_secret_findings(findings)
